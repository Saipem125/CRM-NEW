/* Screen 1 — Loader (§5; reference: Data Loader): source tabs, connection test, mapping with auto-suggest and
   required-field highlighting, cascading field filter, well multi-select table with derived type, conversion
   dates, timeline bars, XY/pressure coverage, project config save. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, explain } from "../api/client";
import type { Connection, Suggestion, TableInfo, WellRow } from "../api/types";
import { canAct, useApp } from "../App";
import { Empty, Msg, Skeleton, Steps, TypeTag } from "../components/Common";
import { DataTable, type Col } from "../components/DataTable";

type Err = { message: string; action: string } | null;
const DATASET_LABELS: Record<string, string> = { rates: "Rates (production / injection) *", pressure: "Pressure", coords: "Well coordinates", category: "Category (field / reservoir / block)", events: "Events" };

export default function LoaderScreen() {
  const { project, projects, setProject, refreshProjects, role, user } = useApp();
  const nav = useNavigate();
  const [step, setStep] = useState(0);
  const [err, setErr] = useState<Err>(null);
  // step 0: project + source
  const [pname, setPname] = useState("");
  const [asset, setAsset] = useState(user.assets[0] ?? "");
  const [src, setSrc] = useState<"files" | "sqlite" | "postgresql" | "mssql" | "oracle">("files");
  const [conn, setConn] = useState({ host: "", port: "", database: "", user: "", password: "", schema_name: "" });
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connection, setConnection] = useState<Connection | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; tables?: number; error?: string } | null>(null);
  const [tables, setTables] = useState<TableInfo[]>([]);
  // step 1: mapping
  const [sug, setSug] = useState<Suggestion | null>(null);
  const [mapping, setMapping] = useState<Record<string, { table: string | null; columns: Record<string, string>; units: Record<string, string> }>>({});
  const [columnsByTable, setColumnsByTable] = useState<Record<string, string[]>>({});
  // step 2/3: wells
  const [cats, setCats] = useState<Record<string, Record<string, string[]>>>({});
  const [filter, setFilter] = useState<{ field?: string; reservoir?: string; block?: string }>({});
  const [wells, setWells] = useState<WellRow[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const canEdit = canAct(role, "engineer", "reviewer");

  useEffect(() => {
    if (!project) return;
    api.connections.list(project.id).then((cs) => { setConnections(cs); if (cs.length && !connection) setConnection(cs[cs.length - 1]); }).catch((e) => setErr(explain(e)));
  }, [project]); // eslint-disable-line react-hooks/exhaustive-deps

  const createProject = async () => {
    setErr(null);
    try {
      const p = await api.projects.create({ name: pname || `${asset} study`, asset });
      await refreshProjects();
      setProject(p);
    } catch (e) { setErr(explain(e)); }
  };

  const connect = async () => {
    if (!project) return;
    setBusy(true); setErr(null); setTestResult(null);
    try {
      const c = await api.connections.create({ project_id: project.id, kind: src, host: conn.host, port: conn.port ? Number(conn.port) : null, database: conn.database, user: conn.user, password: conn.password || null, schema_name: conn.schema_name });
      const t = await api.connections.test(c.id);
      setTestResult(t);
      setConnections([...connections, c]);
      setConnection(c);
      if (t.ok) { setTables(await api.connections.tables(c.id)); }
    } catch (e) { setErr(explain(e)); } finally { setBusy(false); }
  };

  const suggest = useCallback(async (c: Connection) => {
    setBusy(true); setErr(null);
    try {
      const s = await api.mapping.suggest(c.id);
      setSug(s);
      const m: typeof mapping = {};
      Object.entries(s.datasets).forEach(([ds, d]) => { m[ds] = { table: d.table, columns: { ...d.columns }, units: { ...d.units } }; });
      setMapping(m);
      const ts = await api.connections.tables(c.id);
      setTables(ts);
      const cols: Record<string, string[]> = {};
      ts.forEach((t) => { cols[t.name] = t.columns; });
      setColumnsByTable(cols);
      setStep(1);
    } catch (e) { setErr(explain(e)); } finally { setBusy(false); }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const missingRequired = useMemo(() => {
    if (!sug) return {} as Record<string, string[]>;
    const out: Record<string, string[]> = {};
    Object.entries(sug.datasets).forEach(([ds, d]) => {
      const m = mapping[ds];
      if (!m?.table) { if (d.required) out[ds] = d.fields.filter((f) => f.required).map((f) => f.name); return; }
      out[ds] = d.fields.filter((f) => f.required && !m.columns[f.name]).map((f) => f.name);
    });
    return out;
  }, [sug, mapping]);
  const ratesOk = mapping.rates?.table && !missingRequired.rates?.length && ["q_oil", "q_water", "q_inj"].some((k) => mapping.rates?.columns[k]);

  const saveMapping = async () => {
    if (!project || !connection) return;
    setBusy(true); setErr(null);
    try {
      const datasets: Record<string, unknown> = {};
      Object.entries(mapping).forEach(([ds, m]) => { if (m.table) datasets[ds] = { table: m.table, columns: Object.fromEntries(Object.entries(m.columns).filter(([, v]) => v)), units: m.units }; });
      await api.mapping.save({ project_id: project.id, connection_id: connection.id, datasets });
      const c = await api.wells.categories(project.id);
      setCats(c.fields);
      const firstField = Object.keys(c.fields)[0];
      setFilter(firstField ? { field: firstField } : {});
      setStep(2);
      await loadWells(firstField ? { field: firstField } : {});
    } catch (e) { setErr(explain(e)); } finally { setBusy(false); }
  };

  const loadWells = async (f: typeof filter) => {
    if (!project) return;
    setWells(null);
    try {
      const ws = await api.wells.list(project.id, f);
      setWells(ws);
      setSelected(new Set(ws.filter((w) => w.type !== "NONE").map((w) => w.well)));
      setStep(3);
    } catch (e) { setErr(explain(e)); }
  };

  const saveConfig = () => {
    if (!project) return;
    sessionStorage.setItem(`wfo_wells_${project.id}`, JSON.stringify([...selected]));
    sessionStorage.setItem(`wfo_filter_${project.id}`, JSON.stringify(filter));
    nav("/run");
  };

  const wellCols: Col<WellRow>[] = [
    { key: "well", header: "well", mono: true, filter: true },
    { key: "type", header: "type", render: (r) => <TypeTag type={r.type} />, filter: true },
    { key: "first", header: "first", mono: true },
    { key: "last", header: "last", mono: true },
    { key: "n_active", header: "steps", num: true },
    { key: "conversions", header: "conversions", render: (r) => r.conversions.map((c) => `${c.date.slice(0, 7)}: ${c.from}→${c.to}`).join("; ") || "—", value: (r) => r.conversions.length },
    { key: "timeline", header: "timeline", render: (r) => <Timeline row={r} /> },
    { key: "has_coords", header: "xy", render: (r) => (r.has_coords ? <span className="tag ok">✓ xy</span> : <span className="tag bad">— xy</span>) },
    { key: "has_pressure", header: "pressure", render: (r) => (r.has_pressure ? <span className="tag ok">✓ p</span> : <span className="tag">—</span>) },
  ];

  const reservoirs = filter.field ? Object.keys(cats[filter.field] ?? {}) : [];
  const blocks = filter.field ? (filter.reservoir ? cats[filter.field]?.[filter.reservoir] ?? [] : [...new Set(Object.values(cats[filter.field] ?? {}).flat())]) : [];

  return (
    <div className="page-narrow stack">
      <div className="row" style={{ justifyContent: "space-between" }}><h1>Load data</h1><span className="sub">Connect → confirm mapping → pick wells. Mapping, filter and well list are saved with the project (§5).</span></div>
      <Steps items={["Connect / drop file", "Confirm mapping", "Field & wells", "Select wells"]} active={step} />
      {err && <Msg severity="error" message={err.message} action={err.action} />}

      {/* ---- step 0 ---- */}
      <section className="card stack" aria-label="Source">
        <h3>1 · Project and source</h3>
        {!project ? (
          <div className="row">
            <label className="field"><span className="f">Project name</span><input value={pname} onChange={(e) => setPname(e.target.value)} placeholder="e.g. ALPHA reallocation" /></label>
            <label className="field"><span className="f">Asset</span><input value={asset} onChange={(e) => setAsset(e.target.value)} list="assets" /><datalist id="assets">{user.assets.map((a) => <option key={a}>{a}</option>)}</datalist></label>
            <button className="primary" onClick={createProject} disabled={!canEdit || !asset}>Create project</button>
            {projects.length > 0 && <span className="sub">or pick an existing project in the top bar</span>}
          </div>
        ) : (
          <>
            <div className="tabs" role="tablist">
              {(["files", "sqlite", "postgresql", "mssql", "oracle"] as const).map((k) => <button key={k} role="tab" aria-selected={src === k} className={`tab ${src === k ? "on" : ""}`} onClick={() => setSrc(k)}>{k === "files" ? "CSV / Excel / Parquet" : k === "mssql" ? "SQL Server" : k === "postgresql" ? "PostgreSQL" : k[0].toUpperCase() + k.slice(1)}</button>)}
            </div>
            <div className="row">
              {src !== "files" && src !== "sqlite" && <>
                <label className="field"><span className="f">Host</span><input value={conn.host} onChange={(e) => setConn({ ...conn, host: e.target.value })} /></label>
                <label className="field"><span className="f">Port</span><input value={conn.port} onChange={(e) => setConn({ ...conn, port: e.target.value })} style={{ width: 80 }} /></label>
                <label className="field"><span className="f">User</span><input value={conn.user} onChange={(e) => setConn({ ...conn, user: e.target.value })} autoComplete="off" /></label>
                <label className="field"><span className="f">Password (stored in the secrets store)</span><input type="password" value={conn.password} onChange={(e) => setConn({ ...conn, password: e.target.value })} autoComplete="off" /></label>
                <label className="field"><span className="f">Schema</span><input value={conn.schema_name} onChange={(e) => setConn({ ...conn, schema_name: e.target.value })} /></label>
              </>}
              <label className="field" style={{ minWidth: 380 }}><span className="f">{src === "files" ? "Folder or workbook path on the server" : src === "sqlite" ? "SQLite file path" : "Database"}</span><input value={conn.database} onChange={(e) => setConn({ ...conn, database: e.target.value })} placeholder={src === "files" ? "C:\\data\\field_export" : ""} /></label>
              <button className="primary" onClick={connect} disabled={!canEdit || busy || !conn.database}>{busy ? "Testing…" : "Connect & test"}</button>
            </div>
            {testResult && (testResult.ok ? <Msg severity="info" message={`Connected: ${testResult.tables} table${testResult.tables === 1 ? "" : "s"} found.`} /> : <Msg severity="error" message="The connection test failed." action={testResult.error ?? "Check the path or credentials."} />)}
            {connections.length > 0 && (
              <div className="row">
                <label className="f" htmlFor="conn">Saved connections</label>
                <select id="conn" value={connection?.id ?? ""} onChange={(e) => setConnection(connections.find((c) => c.id === e.target.value) ?? null)}>{connections.map((c) => <option key={c.id} value={c.id}>{c.kind} · {c.database}</option>)}</select>
                <button onClick={() => connection && suggest(connection)} disabled={!connection || busy}>{busy ? "Reading…" : "Read tables & suggest mapping →"}</button>
                {tables.length > 0 && <span className="sub">{tables.map((t) => t.name).join(", ")}</span>}
              </div>
            )}
          </>
        )}
      </section>

      {/* ---- step 1 ---- */}
      {sug && (
        <section className="card stack" aria-label="Mapping">
          <h3>2 · Column mapping <span className="sub">required fields are highlighted until mapped</span></h3>
          <div className="grid grid-2">
            {Object.entries(sug.datasets).map(([ds, d]) => {
              const m = mapping[ds] ?? { table: null, columns: {}, units: {} };
              const cols = m.table ? columnsByTable[m.table] ?? [] : [];
              return (
                <div key={ds} className="card" style={{ background: "var(--bg2)" }}>
                  <h4>{DATASET_LABELS[ds] ?? ds}</h4>
                  <label className="f" htmlFor={`t-${ds}`}>Source table / sheet</label>
                  <select id={`t-${ds}`} value={m.table ?? ""} onChange={(e) => setMapping({ ...mapping, [ds]: { ...m, table: e.target.value || null, columns: {} } })}>
                    <option value="">— none —</option>{tables.map((t) => <option key={t.name}>{t.name}</option>)}
                  </select>
                  <table className="data" style={{ marginTop: 8 }}>
                    <tbody>
                      {d.fields.map((f) => {
                        const missing = f.required && m.table && !m.columns[f.name];
                        return (
                          <tr key={f.name}>
                            <td style={{ width: "48%" }}>{f.label} <span className={`tag ${f.required ? (missing ? "bad" : "ok") : ""}`}>{f.required ? "required" : "optional"}</span></td>
                            <td>
                              <select aria-label={`${ds} ${f.label}`} value={m.columns[f.name] ?? ""} disabled={!m.table} style={{ borderColor: missing ? "var(--danger)" : undefined }} onChange={(e) => setMapping({ ...mapping, [ds]: { ...m, columns: { ...m.columns, [f.name]: e.target.value } } })}>
                                <option value="">—</option>{cols.map((c) => <option key={c}>{c}</option>)}
                              </select>
                              {m.units[f.name] && <span className="unit"> {m.units[f.name]}</span>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              );
            })}
          </div>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="sub">{ratesOk ? "Rates mapped ✓" : "Rates: well id, date and at least one of oil/water or injection are required."} · category {mapping.category?.table ? "✓" : "— (field filter disabled, all wells shown)"} · coordinates {mapping.coords?.table ? "✓" : "— (map disabled)"} · pressure {mapping.pressure?.table ? "✓" : "— (constant-pressure mode)"}</span>
            <button className="primary" onClick={saveMapping} disabled={!ratesOk || busy || !canEdit}>{busy ? "Saving…" : "Save mapping & derive wells →"}</button>
          </div>
        </section>
      )}

      {/* ---- step 2/3 ---- */}
      {step >= 2 && (
        <section className="card stack" aria-label="Wells">
          <h3>3 · Field, reservoir, block → wells</h3>
          {Object.keys(cats).length > 0 ? (
            <div className="row">
              <label className="field"><span className="f">Field *</span><select value={filter.field ?? ""} onChange={(e) => { const f = { field: e.target.value }; setFilter(f); loadWells(f); }}>{Object.keys(cats).map((f) => <option key={f}>{f}</option>)}</select></label>
              <label className="field"><span className="f">Reservoir</span><select value={filter.reservoir ?? ""} onChange={(e) => { const f = { field: filter.field, reservoir: e.target.value || undefined }; setFilter(f); loadWells(f); }}><option value="">— all —</option>{reservoirs.map((r) => <option key={r}>{r}</option>)}</select></label>
              <label className="field"><span className="f">Block / pattern</span><select value={filter.block ?? ""} onChange={(e) => { const f = { ...filter, block: e.target.value || undefined }; setFilter(f); loadWells(f); }}><option value="">— all —</option>{blocks.map((b) => <option key={b}>{b}</option>)}</select></label>
            </div>
          ) : <span className="sub">No category table mapped — all wells in the rates table are available.</span>}
          {wells === null ? <Skeleton lines={6} height={22} /> : wells.length === 0 ? <Empty text="No wells in this scope." /> : (
            <>
              <div className="row">
                {(["PROD", "INJ", "MIXED"] as const).map((t) => <button key={t} className="sm" onClick={() => setSelected(new Set(wells.filter((w) => w.type === t).map((w) => w.well)))}>{t === "PROD" ? "producers" : t === "INJ" ? "injectors" : "mixed"} only</button>)}
                <button className="sm" onClick={() => { const last = wells.reduce((m, w) => (w.last && w.last > m ? w.last : m), ""); const cut = new Date(last); cut.setMonth(cut.getMonth() - 12); setSelected(new Set(wells.filter((w) => w.last && new Date(w.last) >= cut).map((w) => w.well))); }}>active last 12 months</button>
                <button className="sm" onClick={() => setSelected(new Set(wells.filter((w) => w.type !== "NONE").map((w) => w.well)))}>all</button>
              </div>
              <DataTable rows={wells} columns={wellCols} rowKey={(r) => r.well} selected={selected} onToggle={(k) => { const s = new Set(selected); s.has(k) ? s.delete(k) : s.add(k); setSelected(s); }} onToggleAll={(keys) => setSelected(keys.every((k) => selected.has(k)) ? new Set() : new Set(keys))} ariaLabel="Wells" />
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="sub">{selected.size} wells selected · type is derived from the rates, not a master table (§5)</span>
                <button className="primary" onClick={saveConfig} disabled={!selected.size}>Save project config & go to Run →</button>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}

function Timeline({ row }: { row: WellRow }) {
  // compressed P/I/B timeline: role segments between conversion dates
  const segs: { cls: string; w: number }[] = [];
  if (!row.first || !row.last) return <div className="tl" />;
  const t0 = new Date(row.first).getTime(), t1 = new Date(row.last).getTime() || t0 + 1;
  const marks = [t0, ...row.conversions.map((c) => new Date(c.date).getTime()), t1];
  let role = row.type === "INJ" ? "i" : row.type === "PROD" ? "p" : row.conversions[0]?.from === "I" ? "i" : "p";
  for (let k = 0; k < marks.length - 1; k++) {
    segs.push({ cls: role, w: Math.max(2, ((marks[k + 1] - marks[k]) / (t1 - t0)) * 100) });
    role = role === "p" ? "i" : "p";
  }
  return <div className="tl" title={`${row.first} → ${row.last}`}>{segs.map((s, k) => <i key={k} className={s.cls} style={{ width: `${s.w}%` }} />)}</div>;
}
