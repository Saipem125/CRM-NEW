/* Shapes returned by the FastAPI backend (waterflood_app/api). Kept loose where the backend is dict-based. */
export type Role = "admin" | "approver" | "reviewer" | "engineer" | "operations" | "viewer";
export type Confidence = "HIGH" | "MEDIUM" | "LOW";

export interface User { id: string; username: string; display_name: string; roles: Role[]; assets: string[]; active: boolean; created_at: string }
export interface Token { access_token: string; expires_in: number; roles: Role[] }
export interface Project { id: string; name: string; asset: string; unit_system: string; config: Record<string, unknown>; created_at: string }
export interface Connection { id: string; project_id: string; kind: string; host: string; port: number | null; database: string; user: string; secret_ref: string; schema_name: string; options: Record<string, string>; created_at: string; query_override?: string | null }
export interface TableInfo { name: string; rows?: number; columns: string[] }
export interface Suggestion { tables: string[]; datasets: Record<string, { required: boolean; table: string | null; columns: Record<string, string>; units: Record<string, string>; fields: { name: string; label: string; required: boolean }[]; missing_required: string[] }> }
export interface WellRow { well: string; type: "PROD" | "INJ" | "MIXED" | "NONE"; conversions: { date: string; from: string; to: string }[]; first: string | null; last: string | null; n_active: number; n_simultaneous: number; has_coords: boolean; has_pressure: boolean }
export interface Job { id: string; kind: string; status: "queued" | "running" | "done" | "failed"; progress: number; message: string; result_id: string | null; error: string | null }
export interface Condition { code: string; severity: "info" | "warning" | "error"; scope: string; message: string; action: string; technical: string; context: Record<string, unknown> }

export interface ActionItem { well: string; facility: string | null; rate_from: number; rate_to: number; step_this_week: number; weeks_to_target: number; target_date: string; setting_hint: string; expected_oil_gain: number; revert_if: string; marginal_value: number }
export interface Recommendation {
  sector: string; confidence: Confidence; posture: string; posture_note: string | null;
  optimization: { objective: string; posture: string; value_plan: number; value_base: number; gain_vs_base_pct: number; gain_vs_equal_split_pct: number; rates: Record<string, number[]>; base_rates: Record<string, number>; marginal_value: Record<string, number>; converged: boolean; notes: string[] };
  actions: ActionItem[]; cum_oil_plan_p10_p50_p90: number[]; cum_oil_base_p10_p50_p90: number[]; notes: string[];
}
export interface Band { p10: number[][]; p50: number[][]; p90: number[][] }
export interface FieldBand { p10: number[]; p50: number[]; p90: number[] }
export interface SectorBundle {
  id: string; key: string; window_index: number; dates: string[]; blind_start_index: number;
  injectors: { id: string; well: string; rate: number[]; days_on: number[]; xy: number[] | null }[];
  producers: { id: string; well: string; raw_liquid: number[]; liquid: number[]; oil: number[]; water: number[]; model: number[]; oil_model: number[] | null; days_on: number[]; bhp: number[] | null; pressure_source: string; blind_r2: number | null; blind_mape: number | null; train_r2: number | null; autocorr: number | null; xy: number[] | null }[];
  well_types: Record<string, string>;
  model: { variant: string; label: string; f_ij: number[][]; tau_days: number[]; tau_ij_days: number[][] | null; J: number[] | number[][] | null; pair_confidence: number[][]; sum_f_per_injector: number[]; sum_f_per_producer: number[]; extra: Record<string, unknown>; n_params: number; blind_r2: number | null; blind_mape: number | null; aicc: number | null; spread: number; plausible: boolean; plausibility_notes: string[] } | null;
  leaderboard: Array<Record<string, unknown>>;
  eligibility: { variant: string; eligible: boolean; available: boolean; reason: string; suitability: number }[];
  confidence: Confidence; confidence_reasons: string[]; gates: Record<string, boolean>; profile: Record<string, unknown>;
  dt_tau: { dt_days: number; tau_estimate_days: number | null; tau_over_dt: number | null; tau_fitted_days: number[] | null; monthly_allocated: boolean; od: number; history_needed_for_od: number };
  conditions: Condition[]; recommendation: Recommendation | null;
  forecast: { dates: string[]; plan: Band; base: Band; field_plan: FieldBand; field_base: FieldBand; plan_rates: number[]; base_rates: number[]; n_members: number; ramp_weekly: number[][]; injector_efficiency: { injectors: string[]; oil_per_bbl: number[]; current: number[]; optimized: number[]; current_oil: number[]; optimized_oil: number[] } | null } | null;
  distances: number[][] | null;
}
export interface Units { rate: string; volume: string; pressure: string; gas_rate?: string; length?: string }
export interface Bundle {
  project_id: string; units: Units; request: Record<string, unknown>; data_hash: string; config_hash: string; seed: number; runtime_s: number; confidence: Confidence;
  windows: { start: string; end: string; reason: string }[]; pressure: { field_source: string; per_producer: Record<string, string> };
  well_types: Record<string, { type: string; conversions: { date: string; from: string; to: string }[] }>;
  conditions: Condition[]; data_quality: { traffic_light: "green" | "amber" | "red"; coverage: Record<string, number>; outliers_removed: number; simultaneous_pi_steps: number; unmatched_ids: number; n_warnings: number; n_errors: number; n_steps: number; pressure_source: string };
  sectors: SectorBundle[]; latest_window_index: number;
}
export interface RunRow { id: string; data_hash: string; config_hash: string; code_version: string; created_at: string; status: string }
export interface RunResult { id: string; project_id: string; data_hash: string; config_hash: string; code_version: string; created_at: string; seed: number; summary: { confidence: Confidence; sectors: { key: string; winner: string | null; confidence: Confidence; blind_r2: number | null; leaderboard: Array<Record<string, unknown>> }[]; recommendations: Record<string, Recommendation | null>; conditions: Condition[]; runtime_s: number; snapshot_hash: string }; models: { sector: string; variant: string; params: Record<string, unknown>; metrics: Record<string, number | string> }[] }
export interface RecommendationRecord {
  id: string; asset: string; originator: string; state: "DRAFT" | "REVIEWED" | "APPROVED" | "IMPLEMENTED" | "EVALUATED" | "REJECTED"; confidence: Confidence; data_hash: string; config_hash: string; model_version: string;
  recommended_rates: Record<string, number>; implemented_rates: Record<string, number>; implemented_at: string | null; approved_by_originator: boolean; override_reason: string | null; advanced_overrides: Record<string, unknown>;
  snapshot: { digest: string } | null; history: { at: string; action: string; actor: string; acting_role: string; from_state: string; to_state: string; note: string; extra: Record<string, unknown> }[]; evaluations: Array<Record<string, unknown>>;
  project_id: string; run_id: string; sector: string; recommendation: Recommendation; units?: Units;
}
export interface AuditEntry { seq: number; at: string; actor: string; acting_role: string; action: string; object_type: string; object_id: string; data_hash: string; config_hash: string; detail: Record<string, unknown> }
export interface Thresholds { effective: Record<string, unknown>; global_overrides: Record<string, unknown>; project_overrides: Record<string, unknown>; config_hash: string }
export interface Webhook { id: string; url: string; events: string[]; active: boolean; created_at: string }
