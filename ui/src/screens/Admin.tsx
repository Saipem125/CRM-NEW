/* Screen 6 — Admin: users and roles, assets, connections, thresholds, validation dashboard (§16 Tier 3), audit log. */
import { useEffect, useState } from "react";
import { api, explain } from "../api/client";
import type { AuditEntry, Connection, Project, Role, Thresholds, User, Webhook } from "../api/types";
import { useApp } from "../App";
import { Msg, Num, Skeleton } from "../components/Common";
import { DataTable, type Col } from "../components/DataTable";

const ROLES: Role[] = ["admin", "approver", "reviewer", "engineer", "operations", "viewer"];
type Err = { message: string; action: string } | null;

export default function AdminScreen() {
  const { projects } = useApp();
  const [tab, setTab] = useState<"users" | "connections" | "thresholds" | "validation" | "audit" | "webhooks">("users");
  const [err, setErr] = useState<Err>(null);
  return (
    <div className="stack">
      <h1>Admin</h1>
      <div className="tabs" role="tablist">{(["users", "connections", "thresholds", "validation", "audit", "webhooks"] as const).map((t) => <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{t === "validation" ? "Validation dashboard" : t[0].toUpperCase() + t.slice(1)}</button>)}</div>
      {err && <Msg severity="error" message={err.message} action={err.action} />}
      {tab === "users" && <Users onErr={setErr} />}
      {tab === "connections" && <Connections projects={projects} onErr={setErr} />}
      {tab === "thresholds" && <ThresholdsPanel projects={projects} onErr={setErr} />}
      {tab === "validation" && <Validation onErr={setErr} />}
      {tab === "audit" && <Audit onErr={setErr} />}
      {tab === "webhooks" && <Webhooks onErr={setErr} />}
    </div>
  );
}

function Users({ onErr }: { onErr: (e: Err) => void }) {
  const [users, setUsers] = useState<User[] | null>(null);
  const [form, setForm] = useState({ username: "", password: "", roles: ["engineer"] as Role[], assets: "", display_name: "" });
  const load = () => api.users.list().then(setUsers).catch((e) => onErr(explain(e)));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const create = async () => {
    try { await api.users.create({ username: form.username, password: form.password || null, roles: form.roles, assets: form.assets.split(",").map((s) => s.trim()).filter(Boolean), display_name: form.display_name }); setForm({ username: "", password: "", roles: ["engineer"], assets: "", display_name: "" }); await load(); } catch (e) { onErr(explain(e)); }
  };
  const cols: Col<User>[] = [
    { key: "username", header: "account / e-mail", mono: true, filter: true },
    { key: "display_name", header: "name" },
    { key: "roles", header: "roles", render: (u) => u.roles.map((r) => <span key={r} className="tag" style={{ marginRight: 4 }}>{r}</span>), value: (u) => u.roles.join(",") },
    { key: "assets", header: "assets", render: (u) => u.assets.join(", ") || <span className="dim">none</span>, value: (u) => u.assets.join(",") },
    { key: "active", header: "status", render: (u) => (u.active ? <span className="tag ok">active</span> : <span className="tag bad">deactivated</span>) },
    { key: "act", header: "", render: (u) => u.active ? <button className="sm danger" onClick={async (e) => { e.stopPropagation(); try { await api.users.deactivate(u.id); await load(); } catch (ex) { onErr(explain(ex)); } }}>Deactivate</button> : <button className="sm" onClick={async (e) => { e.stopPropagation(); try { await api.users.update(u.id, { active: true }); await load(); } catch (ex) { onErr(explain(ex)); } }}>Reactivate</button> },
  ];
  return (
    <div className="stack">
      <section className="card stack" aria-label="Add user">
        <h3>Add user <span className="sub">by e-mail or account name; deactivate, never delete (§3.1)</span></h3>
        <div className="row">
          <label className="field"><span className="f">Account / e-mail</span><input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} data-testid="user-name" /></label>
          <label className="field"><span className="f">Name</span><input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} /></label>
          <label className="field"><span className="f">Password (blank = SSO only)</span><input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></label>
          <label className="field"><span className="f">Assets (comma-separated)</span><input value={form.assets} onChange={(e) => setForm({ ...form, assets: e.target.value })} /></label>
          <div className="field"><span className="f">Roles</span><div className="row" style={{ gap: 6 }}>{ROLES.map((r) => <label key={r} className="tag" style={{ cursor: "pointer" }}><input type="checkbox" checked={form.roles.includes(r)} onChange={(e) => setForm({ ...form, roles: e.target.checked ? [...form.roles, r] : form.roles.filter((x) => x !== r) })} /> {r}</label>)}</div></div>
          <button className="primary" onClick={create} disabled={!form.username || !form.roles.length} data-testid="user-add">Add</button>
        </div>
      </section>
      <section className="card">{users === null ? <Skeleton lines={4} height={24} /> : <DataTable rows={users} columns={cols} rowKey={(u) => u.id} ariaLabel="Users" />}</section>
    </div>
  );
}

function Connections({ projects, onErr }: { projects: Project[]; onErr: (e: Err) => void }) {
  const [rows, setRows] = useState<(Connection & { project: string })[] | null>(null);
  useEffect(() => {
    Promise.all(projects.map((p) => api.connections.list(p.id).then((cs) => cs.map((c) => ({ ...c, project: p.name }))))).then((r) => setRows(r.flat())).catch((e) => onErr(explain(e)));
  }, [projects, onErr]);
  const cols: Col<Connection & { project: string }>[] = [{ key: "project", header: "project" }, { key: "kind", header: "kind", mono: true }, { key: "host", header: "host", mono: true }, { key: "database", header: "database / path", mono: true }, { key: "user", header: "user", mono: true }, { key: "secret_ref", header: "secret", mono: true, render: (c) => c.secret_ref ? <span className="tag">stored · {c.secret_ref}</span> : <span className="dim">—</span> }, { key: "query_override", header: "query override", render: (c) => c.query_override ? <span className="tag warn">yes (logged)</span> : "—" }, { key: "created_at", header: "created", mono: true, value: (c) => c.created_at.slice(0, 10) }];
  return <section className="card">{rows === null ? <Skeleton lines={4} height={24} /> : <DataTable rows={rows} columns={cols} rowKey={(c) => c.id} ariaLabel="Connections" empty="No connections. Passwords are never shown; they live in the secrets store." />}</section>;
}

function ThresholdsPanel({ projects, onErr }: { projects: Project[]; onErr: (e: Err) => void }) {
  const [pid, setPid] = useState<string>("");
  const [th, setTh] = useState<Thresholds | null>(null);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [policy, setPolicy] = useState<"log" | "enforce">("log");
  const load = () => api.admin.thresholds(pid || undefined).then((t) => { setTh(t); setEdit({}); }).catch((e) => onErr(explain(e)));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps
  const gates = (th?.effective as { gates?: Record<string, number>; confidence?: Record<string, Record<string, number>>; ramping?: Record<string, unknown> } | undefined) ?? {};
  const save = async () => {
    const overrides: Record<string, Record<string, number>> = {};
    Object.entries(edit).forEach(([k, v]) => { const [sec, key] = k.split("."); overrides[sec] = { ...(overrides[sec] ?? {}), [key]: Number(v) }; });
    try { setTh(await api.admin.patchThresholds(overrides, pid || undefined)); setEdit({}); } catch (e) { onErr(explain(e)); }
  };
  const field = (sec: string, key: string, v: unknown) => <label key={sec + key} className="field"><span className="f mono">{sec}.{key}</span><input className="mono" value={edit[`${sec}.${key}`] ?? String(v)} onChange={(e) => setEdit({ ...edit, [`${sec}.${key}`]: e.target.value })} style={{ width: 110 }} /></label>;
  return (
    <div className="stack">
      <section className="card stack">
        <div className="row"><label className="field"><span className="f">Scope</span><select value={pid} onChange={(e) => setPid(e.target.value)}><option value="">Global (all assets)</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>{th && <span className="sub mono">effective config hash {th.config_hash.slice(0, 12)}…</span>}</div>
        {!th ? <Skeleton lines={3} /> : (
          <>
            <h3>Identifiability gates (§7)</h3><div className="row">{Object.entries(gates.gates ?? {}).filter(([, v]) => typeof v === "number").map(([k, v]) => field("gates", k, v))}</div>
            <h3>Confidence badge (§9)</h3><div className="row">{Object.entries(gates.confidence?.high ?? {}).map(([k, v]) => field("confidence.high", k, v))}{Object.entries(gates.confidence?.medium ?? {}).map(([k, v]) => field("confidence.medium", k, v))}</div>
            <div className="row" style={{ justifyContent: "space-between" }}><span className="sub">Changes are audited and change the config hash stored with every run.</span><button className="primary" onClick={save} disabled={!Object.keys(edit).length}>Save overrides</button></div>
          </>
        )}
      </section>
      <section className="card stack"><h3>Separation policy (§3)</h3><div className="row"><span className="seg">{(["log", "enforce"] as const).map((p) => <button key={p} className={policy === p ? "on" : ""} onClick={async () => { try { await api.admin.separation(p); setPolicy(p); } catch (e) { onErr(explain(e)); } }}>{p}</button>)}</span><span className="sub">log: originator approvals are recorded as approved-by-originator · enforce: blocked</span></div></section>
    </div>
  );
}

function Validation({ onErr }: { onErr: (e: Err) => void }) {
  const [table, setTable] = useState<Record<string, { n: number; within_band: number; better: number; worse: number }> | null>(null);
  useEffect(() => { api.evaluations.calibration().then(setTable).catch((e) => onErr(explain(e))); }, [onErr]);
  return (
    <section className="card stack" aria-label="Validation dashboard">
      <h3>Tier 3 — live calibration <span className="sub">a HIGH badge should land within band ≥ 80 % of the time (§16)</span></h3>
      {table === null ? <Skeleton lines={3} /> : Object.keys(table).length === 0 ? <span className="sub">No evaluated recommendations yet.</span> : (
        <table className="data"><thead><tr><th>badge</th><th className="num">n</th><th className="num">within band</th><th className="num">better</th><th className="num">worse</th><th>target met</th></tr></thead>
          <tbody>{Object.entries(table).map(([b, r]) => <tr key={b}><td><span className={`badge ${b}`}>{b}</span></td><td className="num">{r.n}</td><td className="num"><Num v={r.within_band * 100} unit="%" /></td><td className="num"><Num v={r.better * 100} unit="%" /></td><td className="num"><Num v={r.worse * 100} unit="%" /></td><td>{b === "HIGH" ? (r.within_band + r.better >= 0.8 ? <span className="tag ok">≥ 80 %</span> : <span className="tag bad">below 80 % — raise the R² floor</span>) : "—"}</td></tr>)}</tbody></table>
      )}
    </section>
  );
}

function Audit({ onErr }: { onErr: (e: Err) => void }) {
  const [rows, setRows] = useState<AuditEntry[] | null>(null);
  useEffect(() => { api.admin.audit({ limit: 500 }).then(setRows).catch((e) => onErr(explain(e))); }, [onErr]);
  const cols: Col<AuditEntry>[] = [{ key: "at", header: "when", mono: true, value: (r) => r.at.slice(0, 19).replace("T", " ") }, { key: "actor", header: "actor", mono: true, filter: true }, { key: "acting_role", header: "acting role", filter: true }, { key: "action", header: "action", filter: true }, { key: "object_type", header: "object" }, { key: "object_id", header: "id", mono: true }, { key: "data_hash", header: "data hash", mono: true, value: (r) => r.data_hash.slice(0, 10) }, { key: "config_hash", header: "config hash", mono: true, value: (r) => r.config_hash.slice(0, 10) }];
  return <section className="card">{rows === null ? <Skeleton lines={5} height={24} /> : <DataTable rows={rows} columns={cols} rowKey={(r) => String(r.seq)} ariaLabel="Audit log" />}</section>;
}

function Webhooks({ onErr }: { onErr: (e: Err) => void }) {
  const [rows, setRows] = useState<Webhook[] | null>(null);
  const [url, setUrl] = useState("");
  const load = () => api.webhooks.list().then(setRows).catch((e) => onErr(explain(e)));
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section className="card stack">
      <div className="row"><label className="field"><span className="f">Webhook URL (state changes, finished runs, alerts)</span><input value={url} onChange={(e) => setUrl(e.target.value)} style={{ width: 380 }} /></label><button className="primary" onClick={async () => { try { await api.webhooks.add({ url }); setUrl(""); await load(); } catch (e) { onErr(explain(e)); } }} disabled={!url}>Add</button></div>
      {rows === null ? <Skeleton lines={2} /> : <table className="data"><thead><tr><th>url</th><th>events</th><th>status</th><th /></tr></thead><tbody>{rows.map((w) => <tr key={w.id}><td className="mono">{w.url}</td><td>{w.events.join(", ")}</td><td>{w.active ? <span className="tag ok">active</span> : <span className="tag">off</span>}</td><td>{w.active && <button className="sm danger" onClick={async () => { try { await api.webhooks.deactivate(w.id); await load(); } catch (e) { onErr(explain(e)); } }}>Deactivate</button>}</td></tr>)}{rows.length === 0 && <tr><td colSpan={4} className="dim">No webhooks.</td></tr>}</tbody></table>}
    </section>
  );
}
