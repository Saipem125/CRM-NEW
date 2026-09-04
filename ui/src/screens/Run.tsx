/* Screen 2 — Run (§2): objective, posture, horizon; one button. Progress with plain-language stages. */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, explain } from "../api/client";
import type { Job, RunRow } from "../api/types";
import { canAct, useApp } from "../App";
import { Empty, Msg } from "../components/Common";
import { PipelineSchematic } from "../components/Schematics";

const STAGES = ["Loading data", "Cleaning data", "Testing models", "Optimizing", "Preparing results"];
const stageOf = (j: Job): number => (j.status === "done" ? 5 : j.progress < 0.1 ? 0 : j.progress < 0.15 ? 1 : j.progress < 0.7 ? 2 : j.progress < 0.9 ? 3 : 4);

export default function RunScreen() {
  const { project, role } = useApp();
  const nav = useNavigate();
  const [objective, setObjective] = useState<"oil" | "npv" | "min_water">("oil");
  const [posture, setPosture] = useState<"aggressive" | "balanced" | "robust">("balanced");
  const [horizon, setHorizon] = useState(24);
  const [econ, setEcon] = useState({ oil_price_usd_per_bbl: "70", water_handling_usd_per_bbl: "1.5", injection_usd_per_bbl: "0.8", discount_rate_per_year: "0.10" });
  const [targetOil, setTargetOil] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<{ message: string; action: string } | null>(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const timer = useRef<number | null>(null);
  const wells: string[] = project ? JSON.parse(sessionStorage.getItem(`wfo_wells_${project.id}`) ?? "[]") : [];
  const advanced = project ? JSON.parse(sessionStorage.getItem(`wfo_advanced_${project.id}`) ?? "null") : null;

  useEffect(() => { if (project) api.runs.list(project.id).then(setRuns).catch(() => setRuns([])); }, [project]);
  useEffect(() => () => { if (timer.current) window.clearInterval(timer.current); }, []);

  const start = async () => {
    if (!project) return;
    setErr(null);
    try {
      const body: Record<string, unknown> = { project_id: project.id, wells, objective, posture, horizon_months: horizon };
      if (objective === "npv") body.economics = Object.fromEntries(Object.entries(econ).map(([k, v]) => [k, Number(v)]));
      if (objective === "min_water" && targetOil) body.target_oil = Number(targetOil);
      if (advanced && canAct(role, "reviewer")) Object.assign(body, advanced);
      const j = await api.runs.submit(body);
      setJob(j);
      timer.current = window.setInterval(async () => {
        try {
          const s = await api.runs.job(j.id);
          setJob(s);
          if (s.status === "done" || s.status === "failed") {
            if (timer.current) window.clearInterval(timer.current);
            if (s.status === "done" && s.result_id) nav(`/result/${s.result_id}`);
          }
        } catch (e) { setErr(explain(e)); if (timer.current) window.clearInterval(timer.current); }
      }, 1200);
    } catch (e) { setErr(explain(e)); }
  };

  if (!project) return <Empty text="Pick or create a project first." action={<button onClick={() => nav("/load")}>Go to Load</button>} />;
  const running = job && (job.status === "queued" || job.status === "running");
  return (
    <div className="page-narrow stack">
      <div className="row" style={{ justifyContent: "space-between" }}><h1>Run</h1><span className="sub">{project.name} · {project.asset} · {wells.length ? `${wells.length} wells selected` : "all wells in scope"}{advanced ? " · advanced overrides active" : ""}</span></div>
      <section className="card stack" aria-label="Run settings">
        <div className="row" style={{ gap: 24 }}>
          <div className="field"><span className="f">Objective</span>
            <span className="seg" role="radiogroup" aria-label="Objective">{(["oil", "npv", "min_water"] as const).map((o) => <button key={o} role="radio" aria-checked={objective === o} className={objective === o ? "on" : ""} onClick={() => setObjective(o)}>{o === "oil" ? "Max oil" : o === "npv" ? "Max NPV" : "Min water for target oil"}</button>)}</span>
          </div>
          <div className="field"><span className="f">Risk posture</span>
            <span className="seg" role="radiogroup" aria-label="Risk posture">{(["aggressive", "balanced", "robust"] as const).map((p) => <button key={p} role="radio" aria-checked={posture === p} className={posture === p ? "on" : ""} onClick={() => setPosture(p)}>{p}</button>)}</span>
          </div>
          <label className="field"><span className="f">Horizon [months]</span><input type="number" min={6} max={24} value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} style={{ width: 90 }} className="mono" /></label>
          <span className="spacer" style={{ flex: 1 }} />
          <button className="primary" onClick={start} disabled={!!running || !canAct(role, "engineer", "reviewer")} style={{ padding: "10px 22px", fontSize: 15 }} data-testid="run-button">{running ? "Running…" : "Run"}</button>
        </div>
        <div className="sub">Balanced optimises expected oil minus half a standard deviation across the model ensemble; robust maximises the worst case and never accepts a plan that loses oil in any realization (§13). LOW confidence forces robust.</div>
        {objective === "npv" && (
          <div className="row" style={{ gap: 16, flexWrap: "wrap" }} data-testid="economics">
            {([["oil_price_usd_per_bbl", "Oil price [USD/bbl]"], ["water_handling_usd_per_bbl", "Water handling [USD/bbl]"], ["injection_usd_per_bbl", "Injection cost [USD/bbl]"], ["discount_rate_per_year", "Discount rate [1/yr]"]] as const).map(([k, label]) => (
              <label key={k} className="field"><span className="f">{label}</span><input className="mono" style={{ width: 110 }} value={econ[k]} onChange={(e) => setEcon({ ...econ, [k]: e.target.value })} inputMode="decimal" /></label>
            ))}
            <span className="sub">Used by the NPV objective and the sensitivity tornado in the report (§12).</span>
          </div>
        )}
        {objective === "min_water" && (
          <label className="field"><span className="f">Target cumulative oil over the horizon [{project.unit_system === "metric" ? "m³" : "bbl"}]</span><input className="mono" style={{ width: 160 }} value={targetOil} onChange={(e) => setTargetOil(e.target.value)} inputMode="decimal" placeholder="hold-current oil if empty" /></label>
        )}
      </section>
      {(job || err) && (
        <section className="card stack" aria-label="Progress" aria-live="polite">
          <PipelineSchematic stage={job ? stageOf(job) : 0} failed={job?.status === "failed"} />
          {job && <div className="progress" aria-hidden><i style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>}
          {job && job.status !== "failed" && <div className="row"><span className="mono dim">{Math.round(job.progress * 100)} %</span><span>{job.status === "done" ? "Finished — opening the result." : STAGES[Math.min(stageOf(job), 4)]}…</span></div>}
          {job?.status === "failed" && <Msg severity="error" message="The run could not be completed." action={job.error?.includes("no mapping") ? "Save a column mapping for this project first." : "Check the data-quality report in Load, or contact the app owner if it happens again."} />}
          {err && <Msg severity="error" message={err.message} action={err.action} />}
        </section>
      )}
      <section className="card stack" aria-label="Previous runs">
        <h3>Previous runs</h3>
        {runs.length === 0 ? <span className="sub">No runs yet for this project.</span> : (
          <table className="data"><thead><tr><th>run</th><th>created</th><th>data hash</th><th>config hash</th><th>code</th><th /></tr></thead>
            <tbody>{runs.slice(0, 12).map((r) => <tr key={r.id}><td className="mono">{r.id}</td><td className="mono">{r.created_at.slice(0, 16).replace("T", " ")}</td><td className="mono">{r.data_hash.slice(0, 10)}…</td><td className="mono">{r.config_hash.slice(0, 10)}…</td><td className="mono">{r.code_version}</td><td><button className="sm" onClick={() => nav(`/result/${r.id}`)}>Open</button></td></tr>)}</tbody></table>
        )}
      </section>
    </div>
  );
}
