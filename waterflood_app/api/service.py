"""Application service — the operations behind every API route (architecture §3, §5, §18).

Keeps FastAPI out of the business logic: routes validate and translate, the service enforces
roles and asset scope, persists through the store, logs every action with actor and acting
role, runs engine jobs, and drives the recommendation lifecycle.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from waterflood_app.api.auth import AuthError, Principal, TokenService, UserStore, require
from waterflood_app.api.bundle import Display, build_bundle, display_recommendation
from waterflood_app.api.jobs import JobHandle, JobRunner
from waterflood_app.config import Config, load_config
from waterflood_app.engine import RunResult, run_engine
from waterflood_app.ingest import schema
from waterflood_app.ingest.connectors import LoadedData, load_project
from waterflood_app.ingest.mapping import suggest_mapping, suggest_table
from waterflood_app.ingest.sql import ConnectionSpec, SecretsStore, SqlSource, read_source
from waterflood_app.ingest.units import PVT
from waterflood_app.ingest.welltype import derive_well_types
from waterflood_app.optimize.economics import Economics
from waterflood_app.optimize.objectives import make_objective
from waterflood_app.optimize.run import SectorRecommendation, forecast_models, optimize_sector
from waterflood_app.optimize.scenarios import ScenarioManager
from waterflood_app.store.audit import AuditLog
from waterflood_app.store.project import ProjectStore
from waterflood_app.store.registry import RunRegistry, code_version
from waterflood_app.store.validation_record import ValidationRecord
from waterflood_app.workflow.evaluation import EvaluationRecord, evaluate
from waterflood_app.workflow.states import Actor, Event, Recommendation, Snapshot, State, WorkflowError


def _now() -> str:
    return datetime.now(UTC).isoformat()


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


@dataclass
class RunArtifacts:
    """In-memory objects of a finished run (the registry holds the serialisable summary)."""

    run: RunResult
    recommendations: dict[str, SectorRecommendation]
    cfg: Config
    project_id: str
    request: dict[str, Any] = field(default_factory=dict)


class Service:
    def __init__(self, root: Path | str, cfg: Config | None = None, sync_jobs: bool | None = None) -> None:
        self.root = Path(root)
        self.store = ProjectStore.open(self.root)
        self.base_cfg = cfg or load_config()
        self.users = UserStore(self.store)
        self.tokens = TokenService(self.store, float(self.base_cfg.get("api.jwt_expires_hours", 8)))
        self.secrets = SecretsStore(self.root)
        self.registry = RunRegistry(self.store)
        self.audit = AuditLog(self.store)
        self.validation = ValidationRecord(self.store)
        self.jobs = JobRunner(self.store, int(self.base_cfg.get("api.job_workers", 2)), sync=sync_jobs)
        self._artifacts: dict[str, RunArtifacts] = {}
        self._lock = threading.Lock()

    # ---- helpers --------------------------------------------------------------------------
    def log(
        self,
        p: Principal,
        action: str,
        object_type: str,
        object_id: str,
        data_hash: str = "",
        config_hash: str = "",
        **detail: Any,
    ) -> None:
        self.audit.record(
            p.user.username, p.acting_role, action, object_type, object_id, data_hash, config_hash, **detail
        )

    def effective_config(self, project_id: str | None = None, extra: dict[str, Any] | None = None) -> Config:
        cfg = self.base_cfg.with_overrides(self.store.get_setting("thresholds_overrides", {}) or {})
        if project_id:
            proj = self.store.project_config(project_id)
            cfg = cfg.with_overrides(proj.get("threshold_overrides", {}) or {})
        if extra:
            cfg = cfg.with_overrides(extra)
        return cfg

    def _project(self, p: Principal, project_id: str) -> dict[str, Any]:
        try:
            cfg = self.store.project_config(project_id)
        except KeyError as exc:
            raise NotFoundError(f"project {project_id!r} not found") from exc
        if not p.user.sees_asset(str(cfg.get("asset", ""))):
            raise AuthError(403, "no access to this asset")
        return cfg

    # ---- projects -------------------------------------------------------------------------
    def create_project(
        self, p: Principal, name: str, asset: str, unit_system: str, pvt: dict[str, float], overrides: dict[str, Any]
    ) -> dict[str, Any]:
        require(p, "engineer", "reviewer")
        if not p.user.sees_asset(asset):
            raise AuthError(403, "no access to this asset")
        pid = uuid.uuid4().hex[:12]
        config = {
            "name": name,
            "asset": asset,
            "unit_system": unit_system,
            "pvt": pvt,
            "threshold_overrides": overrides,
            "created_at": _now(),
        }
        self.store.create_project(pid, asset, config)
        self.log(p, "create_project", "project", pid, asset=asset)
        return self.project_out(pid)

    def project_out(self, pid: str) -> dict[str, Any]:
        cfg = self.store.project_config(pid)
        return {
            "id": pid,
            "name": cfg.get("name", ""),
            "asset": cfg.get("asset", ""),
            "unit_system": cfg.get("unit_system", "field"),
            "config": cfg,
            "created_at": cfg.get("created_at", ""),
        }

    def list_projects(self, p: Principal) -> list[dict[str, Any]]:
        return [self.project_out(r["id"]) for r in self.store.list_projects() if p.user.sees_asset(str(r["asset"]))]

    def get_project(self, p: Principal, pid: str) -> dict[str, Any]:
        self._project(p, pid)
        return self.project_out(pid)

    # ---- connections ----------------------------------------------------------------------
    def create_connection(
        self,
        p: Principal,
        project_id: str,
        kind: str,
        host: str,
        port: int | None,
        database: str,
        user: str,
        password: str | None,
        schema_name: str,
        options: dict[str, str],
    ) -> dict[str, Any]:
        require(p, "engineer", "reviewer")
        self._project(p, project_id)
        cid = uuid.uuid4().hex[:12]
        secret_ref = ""
        if password:
            secret_ref = f"conn_{cid}"
            self.secrets.set(secret_ref, password)
        spec = ConnectionSpec(
            kind=kind,
            host=host,
            port=port,
            database=database,
            user=user,
            secret_ref=secret_ref,
            schema=schema_name,
            options=dict(options),
        )
        with self.store.conn() as c:
            c.execute("INSERT INTO connections VALUES (?,?,?,?)", (cid, project_id, json.dumps(spec.public()), _now()))
        self.log(p, "create_connection", "connection", cid, kind=kind, database=database)
        return self.connection_out(cid)

    def _spec(self, cid: str) -> tuple[str, ConnectionSpec, str]:
        with self.store.conn() as c:
            r = c.execute("SELECT project_id, spec_json, created_at FROM connections WHERE id=?", (cid,)).fetchone()
        if r is None:
            raise NotFoundError(f"connection {cid!r} not found")
        d = json.loads(r[1])
        spec = ConnectionSpec(
            kind=d["kind"],
            host=d.get("host", ""),
            port=d.get("port"),
            database=d["database"],
            user=d.get("user", ""),
            secret_ref=d.get("secret_ref", ""),
            schema=d.get("schema", ""),
            options=dict(d.get("options", {})),
            query_override=d.get("query_override"),
        )
        return str(r[0]), spec, str(r[2])

    def connection_out(self, cid: str) -> dict[str, Any]:
        pid, spec, created = self._spec(cid)
        return {
            "id": cid,
            "project_id": pid,
            "kind": spec.kind,
            "host": spec.host,
            "port": spec.port,
            "database": spec.database,
            "user": spec.user,
            "secret_ref": spec.secret_ref,
            "schema_name": spec.schema,
            "options": spec.options,
            "created_at": created,
            "query_override": spec.query_override,
        }

    def list_connections(self, p: Principal, project_id: str) -> list[dict[str, Any]]:
        self._project(p, project_id)
        with self.store.conn() as c:
            ids = [
                r[0]
                for r in c.execute(
                    "SELECT id FROM connections WHERE project_id=? ORDER BY created_at", (project_id,)
                ).fetchall()
            ]
        return [self.connection_out(i) for i in ids]

    def get_connection(self, p: Principal, cid: str) -> dict[str, Any]:
        pid, _, _ = self._spec(cid)
        self._project(p, pid)
        return self.connection_out(cid)

    def test_connection(self, p: Principal, cid: str) -> dict[str, Any]:
        pid, spec, _ = self._spec(cid)
        self._project(p, pid)
        if spec.kind == "files":
            path = Path(spec.database)
            if not path.exists():
                return {"ok": False, "error": f"path {spec.database!r} does not exist"}
            tables = read_source(spec, self.secrets)
            return {"ok": True, "kind": "files", "tables": len(tables)}
        try:
            return SqlSource(spec, self.secrets).test()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def connection_tables(self, p: Principal, cid: str) -> list[dict[str, Any]]:
        pid, spec, _ = self._spec(cid)
        self._project(p, pid)
        if spec.kind == "files":
            tables = read_source(spec, self.secrets)
            return [{"name": n, "rows": t.height, "columns": t.columns} for n, t in tables.items()]
        src = SqlSource(spec, self.secrets)
        return [{"name": n, "columns": [c["name"] for c in src.columns(n)]} for n in src.tables()]

    def connection_columns(self, p: Principal, cid: str, table: str) -> list[dict[str, Any]]:
        pid, spec, _ = self._spec(cid)
        self._project(p, pid)
        if spec.kind == "files":
            t = read_source(spec, self.secrets, [table]).get(table)
            if t is None:
                raise NotFoundError(f"table {table!r} not found")
            return [{"name": c, "type": str(t.schema[c])} for c in t.columns]
        return SqlSource(spec, self.secrets).columns(table)

    def set_query_override(self, p: Principal, cid: str, sql: str) -> dict[str, Any]:
        require(p, "reviewer")
        pid, spec, _ = self._spec(cid)
        self._project(p, pid)
        if not sql.strip().lower().startswith(("select", "with")):
            raise ValueError("only SELECT statements are allowed")
        spec.query_override = sql
        with self.store.conn() as c:
            c.execute("UPDATE connections SET spec_json=? WHERE id=?", (json.dumps(spec.public()), cid))
        self.log(p, "query_override", "connection", cid, sql=sql[:500])
        return self.connection_out(cid)

    # ---- mapping ----------------------------------------------------------------------------
    def _read_tables(self, cid: str) -> dict[str, pl.DataFrame]:
        _, spec, _ = self._spec(cid)
        return read_source(spec, self.secrets)

    def suggest(self, p: Principal, cid: str) -> dict[str, Any]:
        pid, _spec, _ = self._spec(cid)
        proj = self._project(p, pid)
        tables = self._read_tables(cid)
        out: dict[str, Any] = {"tables": list(tables), "datasets": {}}
        for ds in schema.DATASETS.values():
            table = suggest_table(ds, list(tables)) or (ds.name if ds.name in tables else None)
            m = suggest_mapping(ds, tables[table].columns, str(proj.get("unit_system", "field"))) if table else None
            out["datasets"][ds.name] = {
                "required": ds.required,
                "table": table,
                "columns": dict(m.columns) if m else {},
                "units": dict(m.units) if m else {},
                "fields": [{"name": f.name, "label": f.label, "required": f.required} for f in ds.fields],
                "missing_required": [
                    f.name for f in ds.fields if f.required and (m is None or f.name not in m.columns)
                ],
            }
        return out

    def save_mapping(
        self, p: Principal, project_id: str, connection_id: str, datasets: dict[str, Any], aliases: dict[str, str]
    ) -> dict[str, Any]:
        require(p, "engineer", "reviewer")
        self._project(p, project_id)
        pid, _, _ = self._spec(connection_id)
        if pid != project_id:
            raise ConflictError("connection belongs to another project")
        if "rates" not in datasets:
            raise ValueError("the rates dataset is required")
        r = datasets["rates"]
        cols = r["columns"] if isinstance(r, dict) else r.columns
        if "well" not in cols or "date" not in cols or not any(k in cols for k in ("q_oil", "q_water", "q_inj")):
            raise ValueError("rates: well id, date and at least one of oil/water or injection must be mapped")
        payload = {
            "connection_id": connection_id,
            "datasets": {k: (v if isinstance(v, dict) else v.model_dump()) for k, v in datasets.items()},
            "aliases": aliases,
        }
        with self.store.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO mappings VALUES (?,?,?,?)",
                (project_id, connection_id, json.dumps(payload), _now()),
            )
        self.log(p, "save_mapping", "project", project_id, datasets=list(datasets))
        return self.get_mapping(p, project_id)

    def get_mapping(self, p: Principal, project_id: str) -> dict[str, Any]:
        self._project(p, project_id)
        with self.store.conn() as c:
            r = c.execute(
                "SELECT connection_id, mapping_json, updated_at FROM mappings WHERE project_id=?", (project_id,)
            ).fetchone()
        if r is None:
            raise NotFoundError("no mapping saved for this project")
        d: dict[str, Any] = dict(json.loads(r[1]))
        d["project_id"] = project_id
        d["updated_at"] = r[2]
        return d

    def load_data(self, project_id: str) -> tuple[LoadedData, str]:
        """Read the source with the saved mapping → canonical tables (+ the snapshot hash)."""
        with self.store.conn() as c:
            r = c.execute(
                "SELECT connection_id, mapping_json FROM mappings WHERE project_id=?", (project_id,)
            ).fetchone()
        if r is None:
            raise NotFoundError("no mapping saved for this project")
        m = json.loads(r[1])
        tables = self._read_tables(str(r[0]))
        mappings = {}
        for ds, spec in m["datasets"].items():
            cm = schema.ColumnMapping(
                table=spec["table"],
                columns=dict(spec["columns"]),
                units=dict(spec.get("units", {})),
                date_format=spec.get("date_format"),
            )
            mappings[ds] = cm
        proj = self.store.project_config(project_id)
        loaded = load_project(
            Path(str(r[0])),
            mappings=mappings,
            unit_system=str(proj.get("unit_system", "field")),
            aliases=m.get("aliases") or None,
            tables=tables,
        )
        snap = self.store.save_snapshot(
            project_id, {k: v for k, v in tables.items() if k in {s["table"] for s in m["datasets"].values()}}
        )
        return loaded, snap

    # ---- wells ------------------------------------------------------------------------------
    def wells(self, p: Principal, project_id: str, flt: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
        self._project(p, project_id)
        loaded, _ = self.load_data(project_id)
        rates = loaded.rates
        scope: set[str] | None = None
        if flt and loaded.category is not None and any(flt.values()):
            cat = loaded.category
            for key in ("field", "reservoir", "block"):
                val = flt.get(key)
                if val and key in cat.columns:
                    cat = cat.filter(pl.col(key) == val)
            scope = set(cat.get_column("well").to_list())
            rates = rates.filter(pl.col("well").is_in(list(scope)))
        types = derive_well_types(rates)
        coords = set(loaded.coords.get_column("well").to_list()) if loaded.coords is not None else set()
        press = set(loaded.pressure.get_column("well").to_list()) if loaded.pressure is not None else set()
        out = []
        for w, t in sorted(types.items()):
            out.append(
                {
                    "well": w,
                    "type": t.type,
                    "conversions": [
                        {"date": c.date.isoformat(), "from": c.from_role, "to": c.to_role} for c in t.conversions
                    ],
                    "first": t.first.isoformat() if t.first else None,
                    "last": t.last.isoformat() if t.last else None,
                    "n_active": t.n_active,
                    "n_simultaneous": t.n_simultaneous,
                    "has_coords": w in coords,
                    "has_pressure": w in press,
                }
            )
        return out

    def category_tree(self, p: Principal, project_id: str) -> dict[str, Any]:
        self._project(p, project_id)
        loaded, _ = self.load_data(project_id)
        if loaded.category is None:
            return {"fields": {}}
        tree: dict[str, dict[str, set[str]]] = {}
        for row in loaded.category.to_dicts():
            f = str(row.get("field") or "")
            r = str(row.get("reservoir") or "") if "reservoir" in row else ""
            b = str(row.get("block") or "") if "block" in row else ""
            tree.setdefault(f, {}).setdefault(r, set()).add(b)
        return {"fields": {f: {r: sorted(bs) for r, bs in rs.items()} for f, rs in tree.items()}}

    # ---- runs -------------------------------------------------------------------------------
    def submit_run(self, p: Principal, req: dict[str, Any]) -> str:
        require(p, "engineer", "reviewer")
        pid = str(req["project_id"])
        self._project(p, pid)
        if req.get("variants") or req.get("threshold_overrides"):
            require(p, "reviewer")  # advanced mode (§2): manual variant / solver settings
        principal = p

        def job(h: JobHandle) -> dict[str, Any]:
            return self._run_job(h, principal, req)

        job_id = self.jobs.submit("run", job, {k: v for k, v in req.items() if k != "economics"})
        self.log(
            p,
            "submit_run",
            "job",
            job_id,
            project_id=pid,
            advanced=bool(req.get("variants") or req.get("threshold_overrides")),
        )
        return job_id

    def _run_job(self, h: JobHandle, p: Principal, req: dict[str, Any]) -> dict[str, Any]:
        pid = str(req["project_id"])
        proj = self.store.project_config(pid)
        cfg = self.effective_config(pid, req.get("threshold_overrides") or None)
        h.progress(0.05, "loading data")
        loaded, snap = self.load_data(pid)
        wells = req.get("wells") or []
        if wells:
            loaded.rates = loaded.rates.filter(pl.col("well").is_in(wells))
        pvt_d = proj.get("pvt") or {}
        pvt = PVT(
            bo=float(pvt_d.get("Bo", 1.0)),
            bw=float(pvt_d.get("Bw", 1.0)),
            rs=float(pvt_d.get("Rs", 0.0)),
            bg=float(pvt_d.get("Bg", 0.0)),
        )
        h.progress(0.15, "fitting models")
        run = run_engine(loaded, cfg, pvt, seed=req.get("seed"), variants=req.get("variants"))
        h.progress(0.7, "optimising")
        econ = Economics.from_config(cfg, req.get("economics") or None)
        recs: dict[str, SectorRecommendation] = {}
        for s in run.latest():
            try:
                recs[s.sector.id] = optimize_sector(
                    s,
                    cfg,
                    str(req.get("objective", "oil")),
                    req.get("posture"),
                    req.get("horizon_months"),
                    economics=econ,
                    target_oil=req.get("target_oil"),
                    seed=run.seed,
                )
            except Exception as exc:
                recs[s.sector.id] = None  # type: ignore[assignment]
                run.meta.setdefault("optimizer_errors", {})[s.sector.id] = f"{type(exc).__name__}: {exc}"
        run_id = uuid.uuid4().hex[:12]
        summary = run.summary()
        summary["snapshot_hash"] = snap
        summary["request"] = {k: v for k, v in req.items() if k != "economics"}
        summary["recommendations"] = {sid: (r.to_dict() if r is not None else None) for sid, r in recs.items()}
        summary["optimizer_errors"] = run.meta.get("optimizer_errors", {})
        self.registry.register(run_id, pid, run.data_hash, run.config_hash, run.seed, summary, version=code_version())
        h.progress(0.9, "preparing results")
        self.registry.save_bundle(
            run_id, build_bundle(run, recs, cfg, pid, {**dict(req), "unit_system": proj.get("unit_system", "field")})
        )
        for s in run.sectors:
            w = s.tournament.winner
            if w is not None:
                self.registry.save_model(
                    run_id,
                    s.key,
                    w.variant,
                    w.fit.params.to_dict(s.grid.injectors, s.grid.producers),
                    {
                        "blind_r2": w.report.blind_r2_field,
                        "blind_mape": w.report.blind_mape_median,
                        "aicc": w.report.aicc,
                        "confidence": s.tournament.confidence,
                    },
                )
        with self._lock:
            self._artifacts[run_id] = RunArtifacts(
                run, {k: v for k, v in recs.items() if v is not None}, cfg, pid, dict(req)
            )
        self.log(p, "run", "run", run_id, run.data_hash, run.config_hash, project_id=pid, confidence=run.confidence)
        self.dispatch_webhook("run.finished", {"run_id": run_id, "project_id": pid, "confidence": run.confidence})
        return {"result_id": run_id, "confidence": run.confidence}

    def job_out(self, p: Principal, job_id: str) -> dict[str, Any]:
        j = self.jobs.get(job_id)
        if j is None:
            raise NotFoundError("job not found")
        return dict(j)

    def run_result(self, p: Principal, run_id: str) -> dict[str, Any]:
        r = self.registry.get(run_id)
        if r is None:
            raise NotFoundError("run not found")
        self._project(p, str(r["project_id"]))
        return r

    def run_details(self, p: Principal, run_id: str) -> dict[str, Any]:
        r = self.registry.get(run_id)
        if r is None:
            raise NotFoundError("run not found")
        self._project(p, str(r["project_id"]))
        b = self.registry.bundle(run_id)
        if b is None:
            raise NotFoundError("no result bundle for this run")
        return b

    def list_runs(self, p: Principal, project_id: str) -> list[dict[str, Any]]:
        self._project(p, project_id)
        return self.registry.list_runs(project_id)

    def _artifact(self, p: Principal, run_id: str) -> RunArtifacts:
        art = self._artifacts.get(run_id)
        if art is None:
            if self.registry.get(run_id) is None:
                raise NotFoundError("run not found")
            raise ConflictError(
                "run results are not in memory (service restarted); re-run the project to use scenarios"
            )
        self._project(p, art.project_id)
        return art

    # ---- scenarios --------------------------------------------------------------------------
    def scenario(self, p: Principal, req: dict[str, Any]) -> dict[str, Any]:
        require(p, "engineer", "reviewer")
        art = self._artifact(p, str(req["run_id"]))
        latest = art.run.latest()
        sector = req.get("sector") or (latest[0].sector.id if latest else None)
        s = next((x for x in latest if x.sector.id == sector), None)
        if s is None:
            raise NotFoundError(f"sector {sector!r} not in the latest window")
        models, weights = forecast_models(s, art.cfg)
        from waterflood_app.optimize.posture import effective_posture

        posture, _ = effective_posture(req.get("posture"), s.tournament.confidence, art.cfg)
        obj = make_objective(
            str(req.get("objective", "oil")), Economics.from_config(art.cfg), req.get("params", {}).get("target_oil")
        )
        sm = ScenarioManager(models, weights, obj, posture, art.cfg, seed=art.run.seed)
        kind = str(req["kind"])
        params = dict(req.get("params", {}))
        if kind == "base":
            sc = sm.base()
        elif kind == "reallocation":
            sc = sm.reallocation()
        elif kind == "water_budget":
            scs = sm.water_budget_sweep(tuple(float(x) for x in params.get("fractions", (0.8, 0.9, 1.0, 1.1, 1.2))))
            self.log(p, "scenario", "run", art.run.data_hash, kind=kind)
            return {"scenarios": [x.to_dict() for x in scs]}
        elif kind == "shut_in":
            sc = sm.shut_in_injector(str(params["injector"]))
        elif kind == "new_well":
            sc = sm.new_injector(
                str(params.get("name", "NEW")), np.asarray(params["xy"], dtype=float), float(params["rate"])
            )
        elif kind == "conversion":
            sc = sm.convert_producer(str(params["producer"]), float(params["rate"]))
        elif kind == "pattern_balancing":
            sc = sm.pattern_balancing(float(params.get("vrr_target", 1.0)), params.get("patterns"))
        else:
            raise ValueError(f"unknown scenario kind {kind!r}")
        self.log(p, "scenario", "run", str(req["run_id"]), kind=kind)
        return {"scenario": sc.to_dict(), "base": sm.base().to_dict()}

    # ---- recommendations ----------------------------------------------------------------------
    def create_recommendation(self, p: Principal, run_id: str, sector: str | None) -> dict[str, Any]:
        require(p, "engineer", "reviewer")
        art = self._artifact(p, run_id)
        sid = sector or next(iter(art.recommendations), None)
        rec_opt = art.recommendations.get(sid or "")
        if rec_opt is None:
            raise NotFoundError(f"no recommendation for sector {sid!r}")
        rid = uuid.uuid4().hex[:12]
        rates = {w: float(v) for w, v in zip(rec_opt.injectors, rec_opt.result.plan.x[0], strict=True)}
        asset = str(self.store.project_config(art.project_id).get("asset", ""))
        rec = Recommendation(
            rid,
            asset,
            p.user.username,
            art.run.data_hash,
            art.run.config_hash,
            code_version(),
            rates,
            rec_opt.confidence,
        )
        payload = rec.to_dict()
        payload["sector"] = sid
        payload["recommendation"] = rec_opt.to_dict()
        payload["members_cum_oil_plan"] = [float(v) for v in rec_opt.result.member_values_plan]
        payload["members_cum_oil_base"] = [float(v) for v in rec_opt.result.member_values_base]
        payload["water_cut_at_creation"] = {
            w: float(v) for w, v in zip(rec_opt.producers, art.run.latest()[0].grid.water_cut[-1], strict=False)
        }
        self.registry.save_recommendation(rid, art.project_id, run_id, rec.state.value, payload)
        self.log(
            p,
            "create_recommendation",
            "recommendation",
            rid,
            art.run.data_hash,
            art.run.config_hash,
            run_id=run_id,
            sector=sid,
        )
        return self.get_recommendation(p, rid)

    def _load_rec(self, rid: str) -> tuple[Recommendation, dict[str, Any]]:
        row = self.registry.recommendation(rid)
        if row is None:
            raise NotFoundError("recommendation not found")
        d = row["payload"]
        rec = Recommendation(
            d["id"],
            d["asset"],
            d["originator"],
            d["data_hash"],
            d["config_hash"],
            d["model_version"],
            dict(d["recommended_rates"]),
            d["confidence"],
            State(d["state"]),
        )
        rec.history = [Event(**e) for e in d.get("history", [])]
        rec.snapshot = Snapshot(**d["snapshot"]) if d.get("snapshot") else None
        rec.implemented_rates = dict(d.get("implemented_rates", {}))
        rec.implemented_at = d.get("implemented_at")
        rec.approved_by_originator = bool(d.get("approved_by_originator", False))
        rec.override_reason = d.get("override_reason")
        rec.advanced_overrides = dict(d.get("advanced_overrides", {}))
        rec.evaluations = list(d.get("evaluations", []))
        return rec, row

    def _save_rec(self, rec: Recommendation, row: dict[str, Any]) -> None:
        payload = dict(row["payload"])
        payload.update(rec.to_dict())
        self.registry.save_recommendation(rec.id, row["project_id"], row["run_id"], rec.state.value, payload)

    def get_recommendation(self, p: Principal, rid: str) -> dict[str, Any]:
        rec, row = self._load_rec(rid)
        self._project(p, str(row["project_id"]))
        d = Display(str(self.store.project_config(str(row["project_id"])).get("unit_system", "field")))
        out = dict(row["payload"])
        out.update(rec.to_dict())
        out["project_id"] = row["project_id"]
        out["run_id"] = row["run_id"]
        out["units"] = d.units
        out["recommended_rates"] = {w: float(v) * d.rate for w, v in rec.recommended_rates.items()}
        out["implemented_rates"] = {w: float(v) * d.rate for w, v in rec.implemented_rates.items()}
        if out.get("recommendation"):
            out["recommendation"] = display_recommendation(out["recommendation"], d)
        return out

    def list_recommendations(self, p: Principal, project_id: str) -> list[dict[str, Any]]:
        self._project(p, project_id)
        with self.store.conn() as c:
            ids = [
                r[0]
                for r in c.execute(
                    "SELECT id FROM recommendations WHERE project_id=? ORDER BY updated_at DESC", (project_id,)
                ).fetchall()
            ]
        return [self.get_recommendation(p, i) for i in ids]

    def transition(
        self,
        p: Principal,
        rid: str,
        action: str,
        note: str = "",
        override_reason: str | None = None,
        actual_rates: dict[str, float] | None = None,
        implemented_at: str | None = None,
    ) -> dict[str, Any]:
        rec, row = self._load_rec(rid)
        self._project(p, str(row["project_id"]))
        actor = Actor(p.user.username, p.acting_role, p.user.roles)
        policy = str(
            self.store.get_setting("separation_policy", self.base_cfg.get("workflow.separation_policy", "log"))
        )
        before = rec.state.value
        if action == "review":
            rec.review(actor, note)
        elif action == "approve":
            rec.approve(actor, policy, override_reason, note)
        elif action == "reject":
            rec.reject(actor, note)
        elif action == "implement":
            if not actual_rates:
                raise WorkflowError("implement needs the actual rates set in the field")
            d = Display(str(self.store.project_config(str(row["project_id"])).get("unit_system", "field")))
            rec.implement(actor, {w: float(v) / d.rate for w, v in actual_rates.items()}, implemented_at, note)
        else:
            raise WorkflowError(f"unknown action {action!r}")
        self._save_rec(rec, row)
        self.log(
            p,
            action,
            "recommendation",
            rid,
            rec.data_hash,
            rec.config_hash,
            from_state=before,
            to_state=rec.state.value,
            approved_by_originator=rec.approved_by_originator,
            override_reason=override_reason,
        )
        self.dispatch_webhook(
            "recommendation.state_changed",
            {
                "recommendation_id": rid,
                "from": before,
                "to": rec.state.value,
                "actor": p.user.username,
                "acting_role": p.acting_role,
            },
        )
        return self.get_recommendation(p, rid)

    # ---- evaluations ------------------------------------------------------------------------
    def add_evaluation(
        self, p: Principal, rid: str, months_after: int, realised_oil: float, evaluated_on: str | None, note: str
    ) -> dict[str, Any]:
        require(p, "engineer", "reviewer", "operations")
        rec, row = self._load_rec(rid)
        self._project(p, str(row["project_id"]))
        if rec.state not in (State.IMPLEMENTED, State.EVALUATED):
            raise WorkflowError("evaluations need an implemented recommendation")
        members = np.asarray(row["payload"].get("members_cum_oil_plan") or [0.0], dtype=float)
        d = Display(str(self.store.project_config(str(row["project_id"])).get("unit_system", "field")))
        realised_oil = float(realised_oil) / d.volume  # display volume → internal
        # scale the horizon cumulative oil to the evaluation window (proportional share of the horizon)
        horizon = int(self.effective_config(str(row["project_id"])).get("optimize.horizon_months", 24))
        members_window = members * min(months_after / horizon, 1.0)
        day = date.fromisoformat(evaluated_on) if evaluated_on else date.today()
        deviations = {w: rec.implemented_rates.get(w, 0.0) - r for w, r in rec.recommended_rates.items()}
        er: EvaluationRecord = evaluate(
            rid,
            months_after,
            day,
            members_window,
            realised_oil,
            rec.confidence,
            self.effective_config(str(row["project_id"])),
            deviations,
        )
        er.note = note
        record = er.to_dict()
        actor = Actor(p.user.username, p.acting_role, p.user.roles)
        if rec.state == State.IMPLEMENTED:
            rec.evaluate(actor, record, note)
        else:
            rec.evaluations.append(record)
        self._save_rec(rec, row)
        self.validation.add_evaluation(er)
        self.log(
            p,
            "evaluate",
            "recommendation",
            rid,
            rec.data_hash,
            rec.config_hash,
            months_after=months_after,
            outcome=er.outcome,
        )
        self.dispatch_webhook(
            "recommendation.evaluated", {"recommendation_id": rid, "months_after": months_after, "outcome": er.outcome}
        )
        return record

    def evaluations(self, p: Principal, rid: str) -> list[dict[str, Any]]:
        rec, row = self._load_rec(rid)
        self._project(p, str(row["project_id"]))
        return list(rec.evaluations)

    def calibration(self, p: Principal) -> dict[str, Any]:
        require(p, "reviewer", "approver")
        return self.validation.calibration_table()

    # ---- admin ------------------------------------------------------------------------------
    def thresholds(self, p: Principal, project_id: str | None = None) -> dict[str, Any]:
        require(p, "reviewer")
        cfg = self.effective_config(project_id)
        return {
            "effective": cfg.to_dict(),
            "global_overrides": self.store.get_setting("thresholds_overrides", {}) or {},
            "project_overrides": (
                self.store.project_config(project_id).get("threshold_overrides", {}) if project_id else {}
            ),
            "config_hash": cfg.hash,
        }

    def patch_thresholds(
        self, p: Principal, overrides: dict[str, Any], project_id: str | None = None
    ) -> dict[str, Any]:
        require(p, "admin")
        if project_id:
            cfg = self.store.project_config(project_id)
            cur = cfg.get("threshold_overrides", {}) or {}
            _deep_update(cur, overrides)
            cfg["threshold_overrides"] = cur
            self.store.update_project_config(project_id, cfg)
        else:
            cur = self.store.get_setting("thresholds_overrides", {}) or {}
            _deep_update(cur, overrides)
            self.store.set_setting("thresholds_overrides", cur)
        self.log(p, "patch_thresholds", "settings", project_id or "global", overrides=overrides)
        return self.thresholds(p, project_id)

    def set_separation_policy(self, p: Principal, policy: str) -> dict[str, str]:
        require(p, "admin")
        if policy not in ("log", "enforce"):
            raise ValueError("policy must be 'log' or 'enforce'")
        self.store.set_setting("separation_policy", policy)
        self.log(p, "set_separation_policy", "settings", "separation_policy", policy=policy)
        return {"separation_policy": policy}

    # ---- webhooks ---------------------------------------------------------------------------
    def add_webhook(self, p: Principal, url: str, events: list[str], secret: str | None) -> dict[str, Any]:
        require(p, "admin")
        wid = uuid.uuid4().hex[:12]
        with self.store.conn() as c:
            c.execute(
                "INSERT INTO webhooks VALUES (?,?,?,?,?,?)", (wid, url, json.dumps(events), secret or "", 1, _now())
            )
        self.log(p, "add_webhook", "webhook", wid, url=url, events=events)
        return self.webhook_out(wid)

    def webhook_out(self, wid: str) -> dict[str, Any]:
        with self.store.conn() as c:
            r = c.execute("SELECT id, url, events_json, active, created_at FROM webhooks WHERE id=?", (wid,)).fetchone()
        if r is None:
            raise NotFoundError("webhook not found")
        return {"id": r[0], "url": r[1], "events": json.loads(r[2]), "active": bool(r[3]), "created_at": r[4]}

    def list_webhooks(self, p: Principal) -> list[dict[str, Any]]:
        require(p, "admin")
        with self.store.conn() as c:
            ids = [r[0] for r in c.execute("SELECT id FROM webhooks ORDER BY created_at").fetchall()]
        return [self.webhook_out(i) for i in ids]

    def deactivate_webhook(self, p: Principal, wid: str) -> dict[str, Any]:
        require(p, "admin")
        with self.store.conn() as c:
            c.execute("UPDATE webhooks SET active=0 WHERE id=?", (wid,))
        self.log(p, "deactivate_webhook", "webhook", wid)
        return self.webhook_out(wid)

    def dispatch_webhook(self, event: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """POST the event to every active subscriber (HMAC-SHA256 signed); failures are logged, never raised."""
        with self.store.conn() as c:
            rows = c.execute("SELECT id, url, events_json, secret FROM webhooks WHERE active=1").fetchall()
        deliveries: list[dict[str, Any]] = []
        body = json.dumps({"event": event, "at": _now(), "payload": payload}, default=str).encode()
        for wid, url, ev_json, secret in rows:
            if event not in json.loads(ev_json):
                continue
            headers = {"Content-Type": "application/json", "X-WFO-Event": event}
            if secret:
                headers["X-WFO-Signature"] = sign(body, str(secret))
            deliveries.append({"webhook_id": wid, "url": url, "event": event})
            self._post(url, body, headers, wid, event)
        return deliveries

    def _post(self, url: str, body: bytes, headers: dict[str, str], wid: str, event: str) -> None:
        import httpx

        def send() -> None:
            try:
                httpx.post(
                    url, content=body, headers=headers, timeout=float(self.base_cfg.get("api.webhook_timeout_s", 5))
                )
            except Exception as exc:
                self.audit.record(
                    "system",
                    "admin",
                    "webhook_failed",
                    "webhook",
                    wid,
                    event=event,
                    error=f"{type(exc).__name__}: {exc}",
                )

        if self.jobs.sync:
            send()
        else:
            threading.Thread(target=send, daemon=True).start()


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _deep_update(base: dict[str, Any], upd: dict[str, Any]) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
