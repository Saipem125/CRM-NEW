/* Screen 3 — Result (§2, §14) and Screen 4 — Details as a right drawer (§9; reference: Tournament Selector). */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, explain } from "../api/client";
import type { Bundle, SectorBundle } from "../api/types";
import { canAct, useApp } from "../App";
import { Badge, Conditions, Drawer, Empty, KV, Light, Msg, Num, Skeleton } from "../components/Common";
import { ConnectivityMatrix, WellMap, type Alert } from "../maps/WellMap";
import { DtTauPlot, ForecastFan, HistoryMatch, InjectorEfficiency, LeaderboardPlot, Tornado } from "../plots/Plots";

const GATE_LABEL: Record<string, string> = { history_min: "history ≥ 8 steps", od: "O_d > 6", points_per_param: "data ≥ 4× params", crmip_data: "CRMIP data ≥ 2× params", inj_cv: "injection CV ≥ 0.15", tau_dt: "τ/Δt ≥ 3", influx: "Σf_ij > 1.15 (aquifer)", event_heavy: "events ≥ 4/well", mature: "water cut ≥ 0.5", long_history: "history ≥ 120", has_xy: "coordinates", has_bhp: "producer BHP", has_relperm: "rel-perm curve" };
const NEUTRAL_GATES = new Set(["influx", "event_heavy", "mature", "long_history", "has_relperm"]); // informative, not pass/fail

export default function ResultScreen() {
  const { runId } = useParams();
  const { project, role } = useApp();
  const nav = useNavigate();
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [err, setErr] = useState<{ message: string; action: string } | null>(null);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [details, setDetails] = useState(false);
  const [detailTab, setDetailTab] = useState<"quality" | "tournament" | "fits" | "sampling" | "alerts">("quality");
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) {
      if (project) api.runs.list(project.id).then((rs) => rs.length && nav(`/result/${rs[0].id}`, { replace: true })).catch(() => undefined);
      return;
    }
    setBundle(null); setErr(null);
    api.runs.details(runId).then((b) => { setBundle(b); setSectorId(b.sectors.find((s) => s.window_index === b.latest_window_index)?.id ?? b.sectors[0]?.id ?? null); }).catch((e) => setErr(explain(e)));
  }, [runId, project, nav]);

  const sector: SectorBundle | null = useMemo(() => bundle?.sectors.find((s) => s.id === sectorId && s.window_index === bundle.latest_window_index) ?? bundle?.sectors[0] ?? null, [bundle, sectorId]);
  const onSelect = useCallback((id: string | null) => setSelected(id), []);
  const rec = sector?.recommendation ?? null;
  const alerts: Alert[] = useMemo(() => (sector?.conditions ?? []).filter((c) => c.code === "CUSUM_SHIFT").map((c) => ({ injector: String(c.context.injector ?? ""), producer: String(c.context.producer ?? ""), text: c.message })), [sector]);

  const createRec = async () => {
    if (!runId || !sector) return;
    setCreating(true); setErr(null);
    try { const r = await api.recommendations.create(runId, sector.id); setCreated(r.id); } catch (e) { setErr(explain(e)); } finally { setCreating(false); }
  };

  if (err) return <Msg severity="error" message={err.message} action={err.action} />;
  if (!runId) return <Empty text="No run yet for this project." action={<button onClick={() => nav("/run")}>Go to Run</button>} />;
  if (!bundle || !sector) return <div className="stack"><div className="skeleton" style={{ height: 36, width: 420 }} /><div className="grid grid-sidebar"><div className="card"><Skeleton lines={8} height={26} /></div><div className="card"><Skeleton lines={8} /></div></div></div>;

  const unit = bundle.units?.rate ?? "bbl/d";
  const volUnit = bundle.units?.volume ?? "bbl";
  const gainPct = rec?.optimization.gain_vs_base_pct ?? 0;
  const sectorsLatest = bundle.sectors.filter((s) => s.window_index === bundle.latest_window_index);
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="row" style={{ gap: 14 }}>
          <h1>Result</h1>
          <Badge level={sector.confidence} large />
          {sectorsLatest.length > 1 && <span className="seg">{sectorsLatest.map((s) => <button key={s.id} className={s.id === sector.id ? "on" : ""} onClick={() => setSectorId(s.id)}>sector {s.id}</button>)}</span>}
          <span className="sub mono">run {runId} · data {bundle.data_hash.slice(0, 8)} · config {bundle.config_hash.slice(0, 8)}</span>
        </div>
        <div className="row">
          <button onClick={() => setDetails(true)} data-testid="open-details">▸ Details</button>
          {canAct(role, "engineer", "reviewer") && !created && <button className="primary" onClick={createRec} disabled={creating || !rec} data-testid="create-recommendation">{creating ? "Creating…" : "Create recommendation (Draft)"}</button>}
          {created && <button className="primary" onClick={() => nav(`/workflow/${created}`)}>Open in Workflow →</button>}
          {role === "operations" && created && <button onClick={() => nav(`/workflow/${created}`)}>Send to operations →</button>}
        </div>
      </div>
      {sector.confidence === "LOW" && <Msg severity="warning" message="Screening result — not for approval." action="See Details for what limits confidence. An approver can override with a stated reason." />}

      <div className="grid grid-sidebar">
        <div className="stack">
          <section className="card" style={{ height: 460 }} aria-label="Well map">
            <WellMap sector={sector} selected={selected} onSelect={onSelect} alerts={alerts} />
          </section>
          <section className="card"><ForecastFan sector={sector} producer={selected && sector.producers.some((p) => p.id === selected) ? selected : undefined} unit={unit} /></section>
        </div>
        <div className="stack">
          <section className="card stack" aria-label="Change this week">
            <h3>Change this week <span className="sub">{rec ? `${rec.actions.length} changes · ramp ≤ ${sector.confidence === "HIGH" ? "15" : "8"} % per week` : "no recommendation"}</span></h3>
            {rec ? (
              <>
                <KV rows={[["Expected gain vs hold current", <Num v={gainPct} unit="%" digits={1} />], ["Gain vs equal split", <Num v={rec.optimization.gain_vs_equal_split_pct} unit="%" digits={1} />], ["Cumulative oil, plan P10 / P50 / P90", <span className="mono">{rec.cum_oil_plan_p10_p50_p90.map((v) => Math.round(v).toLocaleString()).join(" / ")} <span className="unit">{volUnit}</span></span>], ["Posture", <span>{rec.posture}{rec.posture_note ? ` · ${rec.posture_note}` : ""}</span>]]} />
                <ul className="actions-list" style={{ listStyle: "none", padding: 0, margin: 0 }} data-testid="action-list">
                  {rec.actions.map((a) => (
                    <li key={a.well}>
                      <span className="well">{sector.injectors.find((i) => i.id === a.well)?.well ?? a.well}</span>
                      <span><Num v={a.rate_from} unit={unit} /> → <Num v={a.rate_to} unit={unit} /> <span className="dim">· this week {a.step_this_week >= 0 ? "+" : ""}<Num v={a.step_this_week} unit={unit} /> · target {a.target_date}</span></span>
                      <span className="mono" title="expected oil gain over the horizon"><Num v={a.expected_oil_gain} unit={volUnit} /></span>
                      <span className="rev">Setting: {a.setting_hint}. Revert if {a.revert_if}.</span>
                    </li>
                  ))}
                  {rec.actions.length === 0 && <li className="sub">No change larger than the 5 % / meter-resolution threshold — hold current rates.</li>}
                </ul>
              </>
            ) : <Msg severity="info" message="The optimizer did not produce a plan for this sector." action="Open Details to see which gate or fit limited it." />}
          </section>
          <section className="card stack" aria-label="What limits confidence">
            <h3>Data quality <Light state={bundle.data_quality.traffic_light} label="" /></h3>
            <Conditions items={[...sector.conditions, ...bundle.conditions].filter((c) => c.severity !== "info")} max={4} />
            <button className="sm ghost" onClick={() => { setDetailTab("quality"); setDetails(true); }}>Full report in Details →</button>
          </section>
        </div>
      </div>

      <Drawer open={details} title="Details" onClose={() => setDetails(false)}>
        <div className="tabs" role="tablist">
          {(["quality", "tournament", "fits", "sampling", "alerts"] as const).map((t) => <button key={t} role="tab" aria-selected={detailTab === t} className={`tab ${detailTab === t ? "on" : ""}`} onClick={() => setDetailTab(t)}>{{ quality: "Data quality", tournament: "Which method won", fits: "Fit per well", sampling: "Δt / τ & pressure", alerts: "Change alerts" }[t]}</button>)}
        </div>
        {detailTab === "quality" && (
          <div className="stack">
            <div className="card"><h3>Traffic light <Light state={bundle.data_quality.traffic_light} label="" /></h3>
              <KV rows={[["Time steps", <Num v={bundle.data_quality.n_steps} unit="months" />], ["Outliers replaced", <Num v={bundle.data_quality.outliers_removed} />], ["Simultaneous P+I steps", <Num v={bundle.data_quality.simultaneous_pi_steps} />], ["Unmatched well IDs", <Num v={bundle.data_quality.unmatched_ids} />], ["Pressure source", bundle.data_quality.pressure_source], ["Warnings / errors", `${bundle.data_quality.n_warnings} / ${bundle.data_quality.n_errors}`]]} />
            </div>
            <div className="card"><h3>Gates</h3>
              <div className="grid grid-3">{Object.entries(sector.gates).map(([g, ok]) => <span key={g} className="row" style={{ gap: 6 }}><span className={`tag ${NEUTRAL_GATES.has(g) ? "" : ok ? "ok" : "bad"}`}>{NEUTRAL_GATES.has(g) ? (ok ? "yes" : "no") : ok ? "pass" : "fail"}</span><span className="mono" style={{ fontSize: 12 }}>{GATE_LABEL[g] ?? g}</span></span>)}</div>
            </div>
            <div className="card"><h3>Coverage per well</h3><div className="grid grid-3">{Object.entries(bundle.data_quality.coverage).map(([w, c]) => <span key={w} className="mono" style={{ fontSize: 12 }}>{w.replace("@P", "").replace("@I", "")} <span className={`tag ${c > 0.9 ? "ok" : c > 0.6 ? "warn" : "bad"}`}>{Math.round(c * 100)} %</span></span>)}</div></div>
            <div className="card"><h3>All conditions</h3><Conditions items={[...bundle.conditions, ...sector.conditions]} max={40} /></div>
          </div>
        )}
        {detailTab === "tournament" && (
          <div className="stack">
            <div className="card"><LeaderboardPlot sector={sector} /></div>
            <div className="card"><h3>Excluded — and why</h3><ul>{sector.eligibility.filter((e) => !e.eligible || !e.available).map((e) => <li key={e.variant}><span className="mono">{e.variant.toUpperCase()}</span> — {e.reason}{e.eligible && !e.available ? " (suitable, not available in this release)" : ""}</li>)}</ul></div>
            <div className="card"><h3>Confidence</h3><div className="row"><Badge level={sector.confidence} /><span className="sub">{sector.confidence_reasons.join(" · ")}</span></div>
              {sector.model && <KV rows={[["Winner", sector.model.label], ["Blind R²", <Num v={sector.model.blind_r2} digits={3} />], ["Blind MAPE", <Num v={sector.model.blind_mape} unit="%" digits={1} />], ["Parameter spread", <Num v={sector.model.spread * 100} unit="%" digits={1} />], ["Parameters", <Num v={sector.model.n_params} />]]} />}
            </div>
            <div className="card"><h3>Connectivity matrix</h3><ConnectivityMatrix sector={sector} onSelect={setSelected} /></div>
            {sector.forecast?.injector_efficiency && <div className="card"><InjectorEfficiency sector={sector} unit={unit} /></div>}
          </div>
        )}
        {detailTab === "fits" && (
          <div className="stack">
            <div className="row"><label className="f" htmlFor="pw">Producer</label><select id="pw" value={selected && sector.producers.some((p) => p.id === selected) ? selected : sector.producers[0]?.id} onChange={(e) => setSelected(e.target.value)}>{sector.producers.map((p) => <option key={p.id} value={p.id}>{p.well}</option>)}</select></div>
            <div className="card"><HistoryMatch sector={sector} producerId={selected && sector.producers.some((p) => p.id === selected) ? selected : sector.producers[0].id} unit={unit} /></div>
            <div className="card"><h3>Per producer</h3>
              <table className="data"><thead><tr><th>producer</th><th className="num">blind R²</th><th className="num">blind MAPE [%]</th><th className="num">train R²</th><th className="num">autocorr</th><th className="num">τ [d]</th><th>pressure</th></tr></thead>
                <tbody>{sector.producers.map((p, j) => <tr key={p.id} onClick={() => setSelected(p.id)} style={{ cursor: "pointer" }}><td className="mono">{p.well}</td><td className="num">{p.blind_r2?.toFixed(3) ?? "—"}</td><td className="num">{p.blind_mape?.toFixed(1) ?? "—"}</td><td className="num">{p.train_r2?.toFixed(3) ?? "—"}</td><td className="num">{p.autocorr?.toFixed(2) ?? "—"}</td><td className="num">{sector.model ? sector.model.tau_days[j].toFixed(0) : "—"}</td><td>{p.pressure_source}</td></tr>)}</tbody></table>
            </div>
          </div>
        )}
        {detailTab === "sampling" && (
          <div className="stack">
            <div className="card"><DtTauPlot dt={sector.dt_tau.dt_days} tau={sector.dt_tau.tau_estimate_days} /></div>
            <div className="card"><h3>Verdict</h3>
              <KV rows={[["Δt (sampling)", <Num v={sector.dt_tau.dt_days} unit="days" />], ["τ estimate (quick CRMT)", <Num v={sector.dt_tau.tau_estimate_days} unit="days" />], ["τ / Δt", <Num v={sector.dt_tau.tau_over_dt} digits={1} />], ["O_d = M/(I+1)", <Num v={sector.dt_tau.od} digits={1} />], ["History needed for O_d > 6", <Num v={sector.dt_tau.history_needed_for_od} unit="months" />], ["Production is monthly-allocated", sector.dt_tau.monthly_allocated ? "yes — stay monthly, a finer grid adds noise, not information" : "no"]]} />
            </div>
            <div className="card"><h3>Pressure handling</h3>
              <p className="sub">Source: <span className="mono">{bundle.pressure.field_source}</span>. {bundle.pressure.field_source === "none" ? "No flowing pressure — constant-BHP formulation; lift or choke changes must be in the events table." : bundle.pressure.field_source === "esp" ? "ESP intake pressure: trips removed, shifted to mid-perforation with a water-cut mixture gradient, averaged to the rate grid." : bundle.pressure.field_source === "gauge" ? "Downhole gauge: flowing pressure averaged to the rate grid." : bundle.pressure.field_source === "static" ? "Static surveys only: used to check the aquifer model, not as flowing pressure." : "Wellhead pressure only: calculated BHP is approximate; constant-pressure mode unless it varies strongly."}</p>
              <div className="grid grid-3">{Object.entries(bundle.pressure.per_producer).map(([p, s]) => <span key={p} className="mono" style={{ fontSize: 12 }}>{p.replace("@P", "")} <span className="tag">{s}</span></span>)}</div>
            </div>
          </div>
        )}
        {detailTab === "alerts" && (
          <div className="stack">
            <div className="card" style={{ height: 420 }}><WellMap sector={sector} selected={selected} onSelect={onSelect} alerts={alerts} /></div>
            <div className="card"><h3>Change alerts</h3>{alerts.length === 0 ? <span className="sub">No connectivity shift detected in this run's history.</span> : <Conditions items={sector.conditions.filter((c) => c.code === "CUSUM_SHIFT")} max={20} />}</div>
            <div className="card"><Tornado rows={[{ label: "oil price ±30 %", low: -0.3 * (rec?.optimization.value_plan ?? 0), high: 0.3 * (rec?.optimization.value_plan ?? 0) }, { label: "water handling cost ±50 %", low: -0.05 * (rec?.optimization.value_plan ?? 0), high: 0.05 * (rec?.optimization.value_plan ?? 0) }]} unit="bbl-equivalent" /></div>
          </div>
        )}
      </Drawer>
    </div>
  );
}
