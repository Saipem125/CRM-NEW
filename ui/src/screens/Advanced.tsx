/* Screen 7 — Advanced mode (reviewer+): manual variant, solver settings, sector editor, parameter overrides.
   Overrides are stored per project and sent with the next run; they appear in the approval view (§2, §3). */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, explain } from "../api/client";
import type { Thresholds } from "../api/types";
import { useApp } from "../App";
import { Empty, Msg } from "../components/Common";

const VARIANTS = ["crmt", "crmp", "crmip", "aquifer", "twophase", "crossflow"];

export default function AdvancedScreen() {
  const { project } = useApp();
  const nav = useNavigate();
  const key = project ? `wfo_advanced_${project.id}` : "";
  const [variants, setVariants] = useState<string[]>([]);
  const [solver, setSolver] = useState({ multistart: "", tau_max_days: "", sparsity_lambda: "", free_primary: false });
  const [sectors, setSectors] = useState({ auto_above_wells: "", distance_cutoff_factor: "", respect_blocks: true });
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [th, setTh] = useState<Thresholds | null>(null);
  const [err, setErr] = useState<{ message: string; action: string } | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!project) return;
    api.admin.thresholds(project.id).then(setTh).catch((e) => setErr(explain(e)));
    const cur = JSON.parse(sessionStorage.getItem(key) ?? "null");
    if (cur) { setVariants(cur.variants ?? []); const s = cur.threshold_overrides?.solver ?? {}; setSolver({ multistart: s.multistart ?? "", tau_max_days: s.tau_max_days ?? "", sparsity_lambda: s.sparsity_lambda ?? "", free_primary: !!s.free_primary }); const sc = cur.threshold_overrides?.sectors ?? {}; setSectors({ auto_above_wells: sc.auto_above_wells ?? "", distance_cutoff_factor: sc.distance_cutoff_factor ?? "", respect_blocks: sc.respect_blocks ?? true }); }
  }, [project, key]);

  if (!project) return <Empty text="Pick a project first." />;
  const eff = (th?.effective ?? {}) as { solver?: Record<string, unknown>; sectors?: Record<string, unknown>; gates?: Record<string, unknown> };
  const save = () => {
    const to: Record<string, Record<string, unknown>> = {};
    const num = (v: string) => (v === "" ? undefined : Number(v));
    const s: Record<string, unknown> = {};
    if (num(solver.multistart) !== undefined) s.multistart = num(solver.multistart);
    if (num(solver.tau_max_days) !== undefined) s.tau_max_days = num(solver.tau_max_days);
    if (num(solver.sparsity_lambda) !== undefined) s.sparsity_lambda = num(solver.sparsity_lambda);
    if (solver.free_primary) s.free_primary = true;
    if (Object.keys(s).length) to.solver = s;
    const sc: Record<string, unknown> = {};
    if (num(sectors.auto_above_wells) !== undefined) sc.auto_above_wells = num(sectors.auto_above_wells);
    if (num(sectors.distance_cutoff_factor) !== undefined) sc.distance_cutoff_factor = num(sectors.distance_cutoff_factor);
    if (!sectors.respect_blocks) sc.respect_blocks = false;
    if (Object.keys(sc).length) to.sectors = sc;
    Object.entries(overrides).forEach(([k, v]) => { if (v !== "") { to.gates = { ...(to.gates ?? {}), [k]: Number(v) }; } });
    const payload = { variants: variants.length ? variants : null, threshold_overrides: to };
    if (!variants.length && !Object.keys(to).length) sessionStorage.removeItem(key); else sessionStorage.setItem(key, JSON.stringify(payload));
    setSaved(true);
  };
  const clear = () => { sessionStorage.removeItem(key); setVariants([]); setSolver({ multistart: "", tau_max_days: "", sparsity_lambda: "", free_primary: false }); setSectors({ auto_above_wells: "", distance_cutoff_factor: "", respect_blocks: true }); setOverrides({}); setSaved(true); };

  return (
    <div className="page-narrow stack">
      <div className="row" style={{ justifyContent: "space-between" }}><h1>Advanced mode</h1><span className="sub">Reviewer and admin only. Every override travels with the run and is shown in the approval view (§3).</span></div>
      {err && <Msg severity="error" message={err.message} action={err.action} />}
      <section className="card stack"><h3>Manual variant <span className="sub">default: the tournament decides</span></h3>
        <div className="row">{VARIANTS.map((v) => <label key={v} className={`tag ${variants.includes(v) ? "ok" : ""}`} style={{ cursor: "pointer", padding: "4px 10px" }}><input type="checkbox" checked={variants.includes(v)} onChange={(e) => setVariants(e.target.checked ? [...variants, v] : variants.filter((x) => x !== v))} /> {v.toUpperCase()}</label>)}</div>
      </section>
      <section className="card stack"><h3>Solver settings <span className="sub">effective: multistart {String(eff.solver?.multistart ?? "—")}, τ max {String(eff.solver?.tau_max_days ?? "—")} d</span></h3>
        <div className="row">
          <label className="field"><span className="f">Multi-start count</span><input className="mono" value={solver.multistart} onChange={(e) => setSolver({ ...solver, multistart: e.target.value })} placeholder={String(eff.solver?.multistart ?? "")} style={{ width: 100 }} /></label>
          <label className="field"><span className="f">τ max [days]</span><input className="mono" value={solver.tau_max_days} onChange={(e) => setSolver({ ...solver, tau_max_days: e.target.value })} placeholder={String(eff.solver?.tau_max_days ?? "")} style={{ width: 100 }} /></label>
          <label className="field"><span className="f">Sparsity penalty λ</span><input className="mono" value={solver.sparsity_lambda} onChange={(e) => setSolver({ ...solver, sparsity_lambda: e.target.value })} placeholder={String(eff.solver?.sparsity_lambda ?? "0")} style={{ width: 100 }} /></label>
          <label className="tag" style={{ cursor: "pointer", padding: "6px 10px", alignSelf: "end" }}><input type="checkbox" checked={solver.free_primary} onChange={(e) => setSolver({ ...solver, free_primary: e.target.checked })} /> free primary term (pywaterflood form)</label>
        </div>
      </section>
      <section className="card stack"><h3>Sector editor <span className="sub">distance graph + blocks (§7)</span></h3>
        <div className="row">
          <label className="field"><span className="f">Automatic above N wells</span><input className="mono" value={sectors.auto_above_wells} onChange={(e) => setSectors({ ...sectors, auto_above_wells: e.target.value })} placeholder={String(eff.sectors?.auto_above_wells ?? "")} style={{ width: 100 }} /></label>
          <label className="field"><span className="f">Distance cutoff × median nearest</span><input className="mono" value={sectors.distance_cutoff_factor} onChange={(e) => setSectors({ ...sectors, distance_cutoff_factor: e.target.value })} placeholder={String(eff.sectors?.distance_cutoff_factor ?? "")} style={{ width: 100 }} /></label>
          <label className="tag" style={{ cursor: "pointer", padding: "6px 10px", alignSelf: "end" }}><input type="checkbox" checked={sectors.respect_blocks} onChange={(e) => setSectors({ ...sectors, respect_blocks: e.target.checked })} /> block boundaries are hard boundaries</label>
        </div>
      </section>
      <section className="card stack"><h3>Gate overrides <span className="sub">leave blank to keep the configured value</span></h3>
        <div className="row">{Object.entries(eff.gates ?? {}).filter(([, v]) => typeof v === "number").map(([k, v]) => <label key={k} className="field"><span className="f mono">{k}</span><input className="mono" value={overrides[k] ?? ""} onChange={(e) => setOverrides({ ...overrides, [k]: e.target.value })} placeholder={String(v)} style={{ width: 100 }} /></label>)}</div>
      </section>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <button className="ghost" onClick={clear}>Clear all overrides</button>
        <div className="row">{saved && <span className="tag ok">saved for the next run</span>}<button className="primary" onClick={save} data-testid="advanced-save">Save for next run</button><button onClick={() => nav("/run")}>Go to Run →</button></div>
      </div>
    </div>
  );
}
