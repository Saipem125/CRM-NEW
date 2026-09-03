"""Pydantic request/response models for the API (architecture §18 "API", §3.1 users)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["admin", "approver", "reviewer", "engineer", "operations", "viewer"]


# ---- auth / users ------------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    roles: list[Role]


class UserCreate(BaseModel):
    username: str = Field(min_length=2, description="e-mail or account name")
    password: str | None = Field(default=None, description="omit when SSO pass-through is used")
    roles: list[Role] = Field(min_length=1)
    assets: list[str] = Field(default_factory=list, description="assets the user may see; empty = none")
    display_name: str = ""


class UserUpdate(BaseModel):
    roles: list[Role] | None = None
    assets: list[str] | None = None
    active: bool | None = None
    password: str | None = None
    display_name: str | None = None


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    roles: list[Role]
    assets: list[str]
    active: bool
    created_at: str


# ---- projects / connections / mapping ------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str
    asset: str
    unit_system: Literal["field", "metric"] = "field"
    pvt: dict[str, float] = Field(default_factory=dict, description="Bo, Bw, Rs, Bg constants (§6)")
    threshold_overrides: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: str
    name: str
    asset: str
    unit_system: str
    config: dict[str, Any]
    created_at: str


class ConnectionCreate(BaseModel):
    project_id: str
    kind: Literal["files", "sqlite", "postgresql", "mssql", "oracle", "mysql"]
    host: str = ""
    port: int | None = None
    database: str = Field(description="database name, or folder / workbook path for files and sqlite")
    user: str = ""
    password: str | None = Field(default=None, description="stored in the secrets store, never returned")
    schema_name: str = ""
    options: dict[str, str] = Field(default_factory=dict)


class ConnectionOut(BaseModel):
    id: str
    project_id: str
    kind: str
    host: str
    port: int | None
    database: str
    user: str
    secret_ref: str
    schema_name: str
    options: dict[str, str]
    created_at: str
    query_override: str | None = None


class QueryOverride(BaseModel):
    sql: str = Field(description="SELECT statement; reviewer/admin only, logged (§18)")


class DatasetMapping(BaseModel):
    table: str
    columns: dict[str, str] = Field(description="canonical field → source column")
    units: dict[str, str] = Field(default_factory=dict)
    date_format: str | None = None


class MappingIn(BaseModel):
    project_id: str
    connection_id: str
    datasets: dict[str, DatasetMapping]
    aliases: dict[str, str] = Field(default_factory=dict, description="well-id alias table (§6)")


class WellFilter(BaseModel):
    field: str | None = None
    reservoir: str | None = None
    block: str | None = None


class WellOut(BaseModel):
    well: str
    type: str
    conversions: list[dict[str, str]]
    first: str | None
    last: str | None
    n_active: int
    n_simultaneous: int
    has_coords: bool
    has_pressure: bool


# ---- runs / scenarios ----------------------------------------------------------------------
class RunRequest(BaseModel):
    project_id: str
    wells: list[str] = Field(default_factory=list, description="empty = all wells in scope")
    objective: Literal["oil", "npv", "min_water"] = "oil"
    posture: Literal["aggressive", "balanced", "robust"] | None = None
    horizon_months: int | None = None
    target_oil: float | None = None
    economics: dict[str, float] = Field(default_factory=dict)
    seed: int | None = None
    variants: list[str] | None = Field(default=None, description="advanced mode only (reviewer/admin)")
    threshold_overrides: dict[str, Any] = Field(default_factory=dict, description="advanced mode only")


class JobOut(BaseModel):
    id: str
    kind: str
    status: Literal["queued", "running", "done", "failed"]
    progress: float
    message: str
    created_at: str
    updated_at: str
    result_id: str | None = None
    error: str | None = None


class ScenarioRequest(BaseModel):
    run_id: str
    sector: str | None = None
    kind: Literal["base", "reallocation", "water_budget", "shut_in", "new_well", "conversion", "pattern_balancing"]
    params: dict[str, Any] = Field(default_factory=dict)
    posture: Literal["aggressive", "balanced", "robust"] | None = None
    objective: Literal["oil", "npv", "min_water"] = "oil"


# ---- recommendations / evaluations ---------------------------------------------------------
class RecommendationCreate(BaseModel):
    run_id: str
    sector: str | None = None


class TransitionRequest(BaseModel):
    note: str = ""
    override_reason: str | None = None
    actual_rates: dict[str, float] | None = None
    implemented_at: str | None = None


class EvaluationIn(BaseModel):
    recommendation_id: str
    months_after: int
    realised_oil: float
    evaluated_on: str | None = None
    note: str = ""


class ThresholdPatch(BaseModel):
    overrides: dict[str, Any] = Field(description="nested overrides merged onto config/thresholds.yaml")
    project_id: str | None = Field(default=None, description="omit for the global (asset-wide) overrides")


class WebhookCreate(BaseModel):
    url: str
    events: list[str] = Field(default_factory=lambda: ["recommendation.state_changed", "run.finished", "alert"])
    secret: str | None = None


class WebhookOut(BaseModel):
    id: str
    url: str
    events: list[str]
    active: bool
    created_at: str
