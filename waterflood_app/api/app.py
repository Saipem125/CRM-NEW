"""FastAPI application — architecture §18 ("API: POST /runs, GET /runs/{id}, GET /recommendations/{id}, webhooks").

Routes: /auth · /users (admin) · /projects · /connections · /mapping · /wells · /runs · /scenarios ·
/recommendations/{id} with state transitions · /evaluations · /admin/thresholds · /webhooks.
Every request carries a bearer token (or a trusted SSO header) and an optional ``X-Acting-Role``.
"""

import os
import secrets as _secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse

from waterflood_app.api import schemas as S
from waterflood_app.api.auth import AuthError, Principal, require, resolve_principal
from waterflood_app.api.service import ConflictError, NotFoundError, Service
from waterflood_app.config import Config, load_config
from waterflood_app.workflow.states import WorkflowError


def create_app(root: Path | str | None = None, cfg: Config | None = None, sync_jobs: bool | None = None) -> FastAPI:
    cfg = cfg or load_config()
    root = Path(root or os.environ.get("WFO_STORE", "./wfo_store"))
    svc = Service(root, cfg, sync_jobs=sync_jobs)
    sso_trusted = bool(int(os.environ.get("WFO_SSO_TRUST", "0")))
    sso_header = str(cfg.get("api.sso_header", "X-SSO-User"))
    admin_user = os.environ.get("WFO_ADMIN_USER", str(cfg.get("api.bootstrap_admin_user", "admin")))
    admin_pw = os.environ.get("WFO_ADMIN_PASSWORD") or None
    generated = None
    if not svc.users.all():
        if admin_pw is None:
            generated = admin_pw = _secrets.token_urlsafe(12)
        svc.users.bootstrap_admin(admin_user, admin_pw)

    app = FastAPI(
        title="Waterflood Optimizer API",
        version="0.3.0",
        description=(
            "CRM-based waterflood evaluation and optimization — architecture v2.1. All technical detail "
            "lives behind /runs/{id} results; the main path is load → map → wells → run → recommendation."
        ),
    )
    app.state.service = svc
    app.state.generated_admin_password = generated

    # ---- errors ----------------------------------------------------------------------------
    @app.exception_handler(AuthError)
    async def _auth(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(NotFoundError)
    async def _nf(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(WorkflowError)
    async def _wf(_: Request, exc: WorkflowError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def _val(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # ---- auth dependency --------------------------------------------------------------------
    def principal(
        authorization: Annotated[str | None, Header()] = None,
        x_acting_role: Annotated[str | None, Header()] = None,
        request: Request = None,  # type: ignore[assignment]
    ) -> Principal:
        sso_user = request.headers.get(sso_header) if request is not None else None
        return resolve_principal(svc.users, svc.tokens, authorization, x_acting_role, sso_user, sso_trusted)

    P = Annotated[Principal, Depends(principal)]

    # ---- auth --------------------------------------------------------------------------------
    @app.post("/auth/login", response_model=S.Token, tags=["auth"])
    def login(body: S.LoginRequest) -> S.Token:
        user = svc.users.verify_password(body.username, body.password)
        if user is None:
            raise AuthError(401, "invalid username or password")
        token, ttl = svc.tokens.issue(user)
        svc.audit.record(user.username, user.default_role, "login", "user", user.id)
        return S.Token(access_token=token, expires_in=ttl, roles=list(user.roles))  # type: ignore[arg-type]

    @app.get("/auth/me", response_model=S.UserOut, tags=["auth"])
    def me(p: P) -> dict[str, Any]:
        return p.user.to_dict()

    # ---- users (admin) ------------------------------------------------------------------------
    @app.get("/users", response_model=list[S.UserOut], tags=["users"])
    def list_users(p: P) -> list[dict[str, Any]]:
        require(p, "admin")
        return [u.to_dict() for u in svc.users.all()]

    @app.post("/users", response_model=S.UserOut, status_code=201, tags=["users"])
    def create_user(body: S.UserCreate, p: P) -> dict[str, Any]:
        require(p, "admin")
        u = svc.users.create(body.username, body.password, list(body.roles), body.assets, body.display_name)
        svc.log(p, "create_user", "user", u.id, roles=list(body.roles), assets=body.assets)
        return u.to_dict()

    @app.patch("/users/{uid}", response_model=S.UserOut, tags=["users"])
    def update_user(uid: str, body: S.UserUpdate, p: P) -> dict[str, Any]:
        require(p, "admin")
        u = svc.users.update(
            uid,
            None if body.roles is None else list(body.roles),
            body.assets,
            body.active,
            body.password,
            body.display_name,
        )
        svc.log(
            p,
            "update_user",
            "user",
            uid,
            **{k: v for k, v in body.model_dump().items() if v is not None and k != "password"},
        )
        return u.to_dict()

    @app.post("/users/{uid}/deactivate", response_model=S.UserOut, tags=["users"])
    def deactivate_user(uid: str, p: P) -> dict[str, Any]:
        require(p, "admin")
        u = svc.users.update(uid, active=False)
        svc.log(p, "deactivate_user", "user", uid)
        return u.to_dict()

    # ---- projects ----------------------------------------------------------------------------
    @app.post("/projects", response_model=S.ProjectOut, status_code=201, tags=["projects"])
    def create_project(body: S.ProjectCreate, p: P) -> dict[str, Any]:
        return svc.create_project(p, body.name, body.asset, body.unit_system, body.pvt, body.threshold_overrides)

    @app.get("/projects", response_model=list[S.ProjectOut], tags=["projects"])
    def list_projects(p: P) -> list[dict[str, Any]]:
        return svc.list_projects(p)

    @app.get("/projects/{pid}", response_model=S.ProjectOut, tags=["projects"])
    def get_project(pid: str, p: P) -> dict[str, Any]:
        return svc.get_project(p, pid)

    # ---- connections -------------------------------------------------------------------------
    @app.post("/connections", response_model=S.ConnectionOut, status_code=201, tags=["connections"])
    def create_connection(body: S.ConnectionCreate, p: P) -> dict[str, Any]:
        return svc.create_connection(
            p,
            body.project_id,
            body.kind,
            body.host,
            body.port,
            body.database,
            body.user,
            body.password,
            body.schema_name,
            body.options,
        )

    @app.get("/connections", response_model=list[S.ConnectionOut], tags=["connections"])
    def list_connections(p: P, project_id: str = Query(...)) -> list[dict[str, Any]]:
        return svc.list_connections(p, project_id)

    @app.get("/connections/{cid}", response_model=S.ConnectionOut, tags=["connections"])
    def get_connection(cid: str, p: P) -> dict[str, Any]:
        return svc.get_connection(p, cid)

    @app.post("/connections/{cid}/test", tags=["connections"])
    def test_connection(cid: str, p: P) -> dict[str, Any]:
        return svc.test_connection(p, cid)

    @app.get("/connections/{cid}/tables", tags=["connections"])
    def connection_tables(cid: str, p: P) -> list[dict[str, Any]]:
        return svc.connection_tables(p, cid)

    @app.get("/connections/{cid}/tables/{table}/columns", tags=["connections"])
    def connection_columns(cid: str, table: str, p: P) -> list[dict[str, Any]]:
        return svc.connection_columns(p, cid, table)

    @app.post("/connections/{cid}/query-override", response_model=S.ConnectionOut, tags=["connections"])
    def query_override(cid: str, body: S.QueryOverride, p: P) -> dict[str, Any]:
        return svc.set_query_override(p, cid, body.sql)

    # ---- mapping -----------------------------------------------------------------------------
    @app.get("/mapping/suggest", tags=["mapping"])
    def suggest(p: P, connection_id: str = Query(...)) -> dict[str, Any]:
        return svc.suggest(p, connection_id)

    @app.post("/mapping", tags=["mapping"])
    def save_mapping(body: S.MappingIn, p: P) -> dict[str, Any]:
        return svc.save_mapping(
            p, body.project_id, body.connection_id, {k: v.model_dump() for k, v in body.datasets.items()}, body.aliases
        )

    @app.get("/mapping/{project_id}", tags=["mapping"])
    def get_mapping(project_id: str, p: P) -> dict[str, Any]:
        return svc.get_mapping(p, project_id)

    # ---- wells -------------------------------------------------------------------------------
    @app.get("/wells", response_model=list[S.WellOut], tags=["wells"])
    def wells(
        p: P,
        project_id: str = Query(...),
        field: str | None = None,
        reservoir: str | None = None,
        block: str | None = None,
    ) -> list[dict[str, Any]]:
        return svc.wells(p, project_id, {"field": field, "reservoir": reservoir, "block": block})

    @app.get("/wells/categories", tags=["wells"])
    def categories(p: P, project_id: str = Query(...)) -> dict[str, Any]:
        return svc.category_tree(p, project_id)

    # ---- runs --------------------------------------------------------------------------------
    @app.post("/runs", response_model=S.JobOut, status_code=202, tags=["runs"])
    def submit_run(body: S.RunRequest, p: P) -> dict[str, Any]:
        job_id = svc.submit_run(p, body.model_dump())
        return svc.job_out(p, job_id)

    @app.get("/runs/jobs/{job_id}", response_model=S.JobOut, tags=["runs"])
    def job_status(job_id: str, p: P) -> dict[str, Any]:
        return svc.job_out(p, job_id)

    @app.get("/runs", tags=["runs"])
    def list_runs(p: P, project_id: str = Query(...)) -> list[dict[str, Any]]:
        return svc.list_runs(p, project_id)

    @app.get("/runs/{run_id}", tags=["runs"])
    def run_result(run_id: str, p: P) -> dict[str, Any]:
        return svc.run_result(p, run_id)

    # ---- scenarios ---------------------------------------------------------------------------
    @app.post("/scenarios", tags=["scenarios"])
    def scenario(body: S.ScenarioRequest, p: P) -> dict[str, Any]:
        return svc.scenario(p, body.model_dump())

    # ---- recommendations -----------------------------------------------------------------------
    @app.post("/recommendations", status_code=201, tags=["recommendations"])
    def create_recommendation(body: S.RecommendationCreate, p: P) -> dict[str, Any]:
        return svc.create_recommendation(p, body.run_id, body.sector)

    @app.get("/recommendations", tags=["recommendations"])
    def list_recommendations(p: P, project_id: str = Query(...)) -> list[dict[str, Any]]:
        return svc.list_recommendations(p, project_id)

    @app.get("/recommendations/{rid}", tags=["recommendations"])
    def get_recommendation(rid: str, p: P) -> dict[str, Any]:
        return svc.get_recommendation(p, rid)

    def _transition(action: str) -> Callable[..., dict[str, Any]]:
        def handler(rid: str, body: S.TransitionRequest, p: P) -> dict[str, Any]:
            return svc.transition(
                p, rid, action, body.note, body.override_reason, body.actual_rates, body.implemented_at
            )

        handler.__name__ = f"{action}_recommendation"
        return handler

    for action in ("review", "approve", "reject", "implement"):
        app.add_api_route(
            f"/recommendations/{{rid}}/{action}",
            _transition(action),
            methods=["POST"],
            tags=["recommendations"],
            summary=f"{action.capitalize()} a recommendation (state transition, §3)",
        )

    # ---- evaluations ---------------------------------------------------------------------------
    @app.post("/evaluations", status_code=201, tags=["evaluations"])
    def add_evaluation(body: S.EvaluationIn, p: P) -> dict[str, Any]:
        return svc.add_evaluation(
            p, body.recommendation_id, body.months_after, body.realised_oil, body.evaluated_on, body.note
        )

    @app.get("/evaluations", tags=["evaluations"])
    def list_evaluations(p: P, recommendation_id: str = Query(...)) -> list[dict[str, Any]]:
        return svc.evaluations(p, recommendation_id)

    @app.get("/evaluations/calibration", tags=["evaluations"])
    def calibration(p: P) -> dict[str, Any]:
        return svc.calibration(p)

    # ---- admin -------------------------------------------------------------------------------
    @app.get("/admin/thresholds", tags=["admin"])
    def get_thresholds(p: P, project_id: str | None = None) -> dict[str, Any]:
        return svc.thresholds(p, project_id)

    @app.patch("/admin/thresholds", tags=["admin"])
    def patch_thresholds(body: S.ThresholdPatch, p: P) -> dict[str, Any]:
        return svc.patch_thresholds(p, body.overrides, body.project_id)

    @app.put("/admin/separation-policy/{policy}", tags=["admin"])
    def separation_policy(policy: str, p: P) -> dict[str, str]:
        return svc.set_separation_policy(p, policy)

    @app.get("/admin/audit", tags=["admin"])
    def audit(p: P, object_id: str | None = None, actor: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        require(p, "reviewer", "approver")
        return svc.audit.entries(object_id, actor, limit)

    # ---- webhooks ----------------------------------------------------------------------------
    @app.post("/webhooks", response_model=S.WebhookOut, status_code=201, tags=["webhooks"])
    def add_webhook(body: S.WebhookCreate, p: P) -> dict[str, Any]:
        return svc.add_webhook(p, body.url, body.events, body.secret)

    @app.get("/webhooks", response_model=list[S.WebhookOut], tags=["webhooks"])
    def list_webhooks(p: P) -> list[dict[str, Any]]:
        return svc.list_webhooks(p)

    @app.post("/webhooks/{wid}/deactivate", response_model=S.WebhookOut, tags=["webhooks"])
    def deactivate_webhook(wid: str, p: P) -> dict[str, Any]:
        return svc.deactivate_webhook(p, wid)

    @app.get("/health", tags=["admin"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": app.version}

    return app


def main() -> None:  # pragma: no cover — `python -m waterflood_app.api.app`
    import uvicorn

    app = create_app()
    if app.state.generated_admin_password:
        print(f"[wfo] bootstrap admin password (shown once): {app.state.generated_admin_password}")
    uvicorn.run(app, host=os.environ.get("WFO_HOST", "127.0.0.1"), port=int(os.environ.get("WFO_PORT", "8000")))


if __name__ == "__main__":  # pragma: no cover
    main()
