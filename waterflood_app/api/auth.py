"""Authentication and user management — architecture §3.1 and §18.

* local accounts (bcrypt hashes) or corporate SSO pass-through (a trusted identity header, enabled
  explicitly; passwords are never stored when SSO is used);
* roles: admin · approver · reviewer · engineer · operations · viewer; a user may hold several;
  admin may act in any role. Acting-as is explicit: the ``X-Acting-Role`` header (defaults to the
  user's highest role) and every action logs actor and acting_role;
* admin adds users by e-mail or account name, assigns roles and the assets they may see, and
  deactivates rather than deletes so past approvals keep their author; no self-registration;
* JWT bearer tokens (HS256), secret from ``WFO_JWT_SECRET`` or generated per store.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from waterflood_app.store.project import ProjectStore

ROLES = ("admin", "approver", "reviewer", "engineer", "operations", "viewer")
ROLE_RANK = {r: k for k, r in enumerate(ROLES)}  # lower = more powerful


class AuthError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class User:
    id: str
    username: str
    display_name: str
    roles: tuple[str, ...]
    assets: tuple[str, ...]
    active: bool
    created_at: str

    def can_act_as(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles

    def sees_asset(self, asset: str) -> bool:
        return "admin" in self.roles or asset in self.assets

    @property
    def default_role(self) -> str:
        return min(self.roles, key=lambda r: ROLE_RANK[r]) if self.roles else "viewer"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "roles": list(self.roles),
            "assets": list(self.assets),
            "active": self.active,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class Principal:
    """The authenticated user plus the role they act in for this request."""

    user: User
    acting_role: str


class UserStore:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def _row(self, r: tuple[Any, ...]) -> User:
        return User(r[0], r[1], r[2] or "", tuple(json.loads(r[4])), tuple(json.loads(r[5])), bool(r[6]), r[7])

    def create(
        self, username: str, password: str | None, roles: list[str], assets: list[str], display_name: str = ""
    ) -> User:
        for r in roles:
            if r not in ROLES:
                raise AuthError(422, f"unknown role {r!r}")
        if self.get_by_username(username) is not None:
            raise AuthError(409, f"user {username!r} already exists")
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() if password else None
        uid = uuid.uuid4().hex[:12]
        with self.store.conn() as c:
            c.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
                (
                    uid,
                    username.strip().lower(),
                    display_name,
                    pw_hash,
                    json.dumps(roles),
                    json.dumps(assets),
                    1,
                    datetime.now(UTC).isoformat(),
                ),
            )
        user = self.get(uid)
        assert user is not None
        return user

    def get(self, uid: str) -> User | None:
        with self.store.conn() as c:
            r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return None if r is None else self._row(r)

    def get_by_username(self, username: str) -> User | None:
        with self.store.conn() as c:
            r = c.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        return None if r is None else self._row(r)

    def all(self) -> list[User]:
        with self.store.conn() as c:
            rows = c.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._row(r) for r in rows]

    def update(
        self,
        uid: str,
        roles: list[str] | None = None,
        assets: list[str] | None = None,
        active: bool | None = None,
        password: str | None = None,
        display_name: str | None = None,
    ) -> User:
        u = self.get(uid)
        if u is None:
            raise AuthError(404, "user not found")
        if roles is not None:
            for r in roles:
                if r not in ROLES:
                    raise AuthError(422, f"unknown role {r!r}")
        sets: list[str] = []
        args: list[Any] = []
        if roles is not None:
            sets.append("roles_json=?")
            args.append(json.dumps(roles))
        if assets is not None:
            sets.append("assets_json=?")
            args.append(json.dumps(assets))
        if active is not None:
            sets.append("active=?")
            args.append(1 if active else 0)
        if password is not None:
            sets.append("pw_hash=?")
            args.append(bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode())
        if display_name is not None:
            sets.append("display_name=?")
            args.append(display_name)
        if sets:
            with self.store.conn() as c:
                c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=?", (*args, uid))
        out = self.get(uid)
        assert out is not None
        return out

    def verify_password(self, username: str, password: str) -> User | None:
        with self.store.conn() as c:
            r = c.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        if r is None or r[3] is None or not r[6]:
            return None
        return self._row(r) if bcrypt.checkpw(password.encode(), str(r[3]).encode()) else None

    def bootstrap_admin(self, username: str, password: str) -> User | None:
        """Create the first admin when the users table is empty (§3.1: no self-registration)."""
        if self.all():
            return None
        return self.create(username, password, ["admin"], [], "Administrator")


class TokenService:
    def __init__(self, store: ProjectStore, expires_hours: float = 8.0) -> None:
        self.expires = timedelta(hours=expires_hours)
        self.secret = os.environ.get("WFO_JWT_SECRET") or self._store_secret(store)

    @staticmethod
    def _store_secret(store: ProjectStore) -> str:
        p = store.root / ".jwt_secret"
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        s = secrets.token_urlsafe(48)
        p.write_text(s, encoding="utf-8")
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return s

    def issue(self, user: User) -> tuple[str, int]:
        exp = datetime.now(UTC) + self.expires
        token = jwt.encode(
            {"sub": user.id, "username": user.username, "roles": list(user.roles), "exp": exp},
            self.secret,
            algorithm="HS256",
        )
        return token, int(self.expires.total_seconds())

    def decode(self, token: str) -> dict[str, Any]:
        try:
            return dict(jwt.decode(token, self.secret, algorithms=["HS256"]))
        except jwt.ExpiredSignatureError as exc:
            raise AuthError(401, "token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError(401, "invalid token") from exc


def resolve_principal(
    users: UserStore,
    tokens: TokenService,
    authorization: str | None,
    acting_role: str | None,
    sso_user: str | None,
    sso_trusted: bool,
) -> Principal:
    """Bearer token, or a trusted SSO identity header when enabled. Acting role must be held (admin: any)."""
    user: User | None = None
    if authorization and authorization.lower().startswith("bearer "):
        payload = tokens.decode(authorization.split(" ", 1)[1])
        user = users.get(str(payload.get("sub")))
    elif sso_trusted and sso_user:
        user = users.get_by_username(sso_user)
        if user is None:
            raise AuthError(403, "SSO identity is not a registered user; ask an admin to add you")
    if user is None:
        raise AuthError(401, "not authenticated")
    if not user.active:
        raise AuthError(403, "user is deactivated")
    role = (acting_role or user.default_role).lower()
    if role not in ROLES:
        raise AuthError(422, f"unknown acting role {role!r}")
    if not user.can_act_as(role):
        raise AuthError(403, f"{user.username} does not hold the role {role!r}")
    return Principal(user, role)


def require(principal: Principal, *roles: str) -> None:
    """The acting role must be one of ``roles`` (admin acting as admin always passes)."""
    if principal.acting_role in roles or principal.acting_role == "admin":
        return
    raise AuthError(403, f"this action needs one of {roles}; acting as {principal.acting_role!r}")
