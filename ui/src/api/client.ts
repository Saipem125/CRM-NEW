/* Typed fetch client for the FastAPI backend. Token and acting role live in sessionStorage. */
import type * as T from "./types";

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export const session = {
  get token(): string | null { return sessionStorage.getItem("wfo_token"); },
  set token(v: string | null) { v ? sessionStorage.setItem("wfo_token", v) : sessionStorage.removeItem("wfo_token"); },
  get actingRole(): string | null { return sessionStorage.getItem("wfo_role"); },
  set actingRole(v: string | null) { v ? sessionStorage.setItem("wfo_role", v) : sessionStorage.removeItem("wfo_role"); },
};

async function req<R>(method: string, path: string, body?: unknown, query?: Record<string, string | number | undefined | null>): Promise<R> {
  const url = new URL(BASE + path, window.location.origin);
  if (query) Object.entries(query).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && url.searchParams.set(k, String(v)));
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  if (session.actingRole) headers["X-Acting-Role"] = session.actingRole;
  const res = await fetch(url.toString(), { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 204) return undefined as R;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data ?? res.statusText);
  return data as R;
}

export const api = {
  login: (username: string, password: string) => req<T.Token>("POST", "/auth/login", { username, password }),
  me: () => req<T.User>("GET", "/auth/me"),
  users: { list: () => req<T.User[]>("GET", "/users"), create: (b: Record<string, unknown>) => req<T.User>("POST", "/users", b), update: (id: string, b: Record<string, unknown>) => req<T.User>("PATCH", `/users/${id}`, b), deactivate: (id: string) => req<T.User>("POST", `/users/${id}/deactivate`) },
  projects: { list: () => req<T.Project[]>("GET", "/projects"), get: (id: string) => req<T.Project>("GET", `/projects/${id}`), create: (b: Record<string, unknown>) => req<T.Project>("POST", "/projects", b) },
  connections: {
    list: (project_id: string) => req<T.Connection[]>("GET", "/connections", undefined, { project_id }),
    create: (b: Record<string, unknown>) => req<T.Connection>("POST", "/connections", b),
    test: (id: string) => req<{ ok: boolean; tables?: number; error?: string; kind?: string }>("POST", `/connections/${id}/test`),
    tables: (id: string) => req<T.TableInfo[]>("GET", `/connections/${id}/tables`),
    columns: (id: string, table: string) => req<{ name: string; type: string }[]>("GET", `/connections/${id}/tables/${encodeURIComponent(table)}/columns`),
    queryOverride: (id: string, sql: string) => req<T.Connection>("POST", `/connections/${id}/query-override`, { sql }),
  },
  mapping: {
    suggest: (connection_id: string) => req<T.Suggestion>("GET", "/mapping/suggest", undefined, { connection_id }),
    save: (b: Record<string, unknown>) => req<Record<string, unknown>>("POST", "/mapping", b),
    get: (project_id: string) => req<Record<string, unknown>>("GET", `/mapping/${project_id}`),
  },
  wells: { list: (project_id: string, f: { field?: string; reservoir?: string; block?: string } = {}) => req<T.WellRow[]>("GET", "/wells", undefined, { project_id, ...f }), categories: (project_id: string) => req<{ fields: Record<string, Record<string, string[]>> }>("GET", "/wells/categories", undefined, { project_id }) },
  runs: {
    submit: (b: Record<string, unknown>) => req<T.Job>("POST", "/runs", b),
    job: (id: string) => req<T.Job>("GET", `/runs/jobs/${id}`),
    list: (project_id: string) => req<T.RunRow[]>("GET", "/runs", undefined, { project_id }),
    get: (id: string) => req<T.RunResult>("GET", `/runs/${id}`),
    details: (id: string) => req<T.Bundle>("GET", `/runs/${id}/details`),
  },
  scenarios: (b: Record<string, unknown>) => req<Record<string, unknown>>("POST", "/scenarios", b),
  recommendations: {
    create: (run_id: string, sector?: string) => req<T.RecommendationRecord>("POST", "/recommendations", { run_id, sector }),
    list: (project_id: string) => req<T.RecommendationRecord[]>("GET", "/recommendations", undefined, { project_id }),
    get: (id: string) => req<T.RecommendationRecord>("GET", `/recommendations/${id}`),
    transition: (id: string, action: "review" | "approve" | "reject" | "implement", b: Record<string, unknown> = {}) => req<T.RecommendationRecord>("POST", `/recommendations/${id}/${action}`, b),
  },
  evaluations: { add: (b: Record<string, unknown>) => req<Record<string, unknown>>("POST", "/evaluations", b), list: (recommendation_id: string) => req<Array<Record<string, unknown>>>("GET", "/evaluations", undefined, { recommendation_id }), calibration: () => req<Record<string, { n: number; within_band: number; better: number; worse: number }>>("GET", "/evaluations/calibration") },
  admin: {
    thresholds: (project_id?: string) => req<T.Thresholds>("GET", "/admin/thresholds", undefined, { project_id }),
    patchThresholds: (overrides: Record<string, unknown>, project_id?: string) => req<T.Thresholds>("PATCH", "/admin/thresholds", { overrides, project_id }),
    separation: (policy: "log" | "enforce") => req<{ separation_policy: string }>("PUT", `/admin/separation-policy/${policy}`),
    audit: (q: { object_id?: string; actor?: string; limit?: number } = {}) => req<T.AuditEntry[]>("GET", "/admin/audit", undefined, q),
  },
  webhooks: { list: () => req<T.Webhook[]>("GET", "/webhooks"), add: (b: Record<string, unknown>) => req<T.Webhook>("POST", "/webhooks", b), deactivate: (id: string) => req<T.Webhook>("POST", `/webhooks/${id}/deactivate`) },
  health: () => req<{ status: string; version: string }>("GET", "/health"),
};

/** Plain-language rendering of an API failure (§17): never a stack trace. */
export function explain(e: unknown): { message: string; action: string } {
  if (e instanceof ApiError) {
    if (e.status === 401) return { message: typeof e.detail === "string" && e.detail !== "not authenticated" && e.detail !== "token expired" ? e.detail : "Your session has expired.", action: "Sign in again." };
    if (e.status === 403) return { message: typeof e.detail === "string" ? e.detail : "You do not have permission for this action.", action: "Switch to a role you hold, or ask an admin for access." };
    if (e.status === 404) return { message: typeof e.detail === "string" ? e.detail : "Not found.", action: "Check the selection and try again." };
    if (e.status === 409) return { message: typeof e.detail === "string" ? e.detail : "This action is not allowed in the current state.", action: "Refresh the page to see the current state." };
    if (e.status === 422) return { message: typeof e.detail === "string" ? e.detail : "Some of the entered values are not valid.", action: "Correct the highlighted fields." };
    return { message: "The server could not complete the request.", action: "Try again; if it persists, contact the app owner." };
  }
  return { message: "The application could not reach the server.", action: "Check the connection and try again." };
}
