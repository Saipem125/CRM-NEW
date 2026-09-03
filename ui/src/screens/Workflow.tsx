/* Screen 5 — Workflow (§3): recommendation list with states, approval dialog (reason required for LOW override),
   implementation entry for Operations, evaluation view. */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, explain } from "../api/client";
import type { RecommendationRecord } from "../api/types";
import { canAct, useApp } from "../App";
import { Badge, Empty, KV, Msg, Num, Skeleton } from "../components/Common";
import { DataTable, type Col } from "../components/DataTable";
import { StateDiagram } from "../components/Schematics";

type Err = { message: string; action: string } | null;

export default function WorkflowScreen() {
  const { recId } = useParams();
  const { project, role, user } = useApp();
  const nav = useNavigate();
  const [list, setList] = useState<RecommendationRecord[] | null>(null);
  const [rec, setRec] = useState<RecommendationRecord | null>(null);
  const [err, setErr] = useState<Err>(null);
  const [note, setNote] = useState("");
  const [override, setOverride] = useState("");
  const [actual, setActual] = useState<Record<string, string>>({});
  const [evalForm, setEvalForm] = useState({ months_after: 3, realised_oil: "" });
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    if (!project) return;
    try { setList(await api.recommendations.list(project.id)); } catch (e) { setErr(explain(e)); }
    if (recId) {
      try { const r = await api.recommendations.get(recId); setRec(r); setActual(Object.fromEntries(Object.entries(r.recommended_rates).map(([w, v]) => [w, String(Math.round(v))]))); } catch (e) { setErr(explain(e)); }
    } else setRec(null);
  }, [project, recId]);
  useEffect(() => { reload(); }, [reload]);

  const act = async (action: "review" | "approve" | "reject" | "implement") => {
    if (!rec) return;
    setBusy(true); setErr(null);
    try {
      const body: Record<string, unknown> = { note };
      if (action === "approve" && override) body.override_reason = override;
      if (action === "implement") body.actual_rates = Object.fromEntries(Object.entries(actual).map(([w, v]) => [w, Number(v)]));
      const r = await api.recommendations.transition(rec.id, action, body);
      setRec(r); setNote(""); setOverride("");
      await reload();
    } catch (e) { setErr(explain(e)); } finally { setBusy(false); }
  };

  const evaluate = async () => {
    if (!rec) return;
    setBusy(true); setErr(null);
    try { await api.evaluations.add({ recommendation_id: rec.id, months_after: evalForm.months_after, realised_oil: Number(evalForm.realised_oil) }); await reload(); } catch (e) { setErr(explain(e)); } finally { setBusy(false); }
  };

  if (!project) return <Empty text="Pick a project first." />;
  const cols: Col<RecommendationRecord>[] = [
    { key: "id", header: "id", mono: true, width: 120 },
    { key: "state", header: "state", render: (r) => <span className={`tag ${r.state === "APPROVED" || r.state === "IMPLEMENTED" || r.state === "EVALUATED" ? "ok" : r.state === "REJECTED" ? "bad" : ""}`}>{r.state}</span>, filter: true },
    { key: "confidence", header: "confidence", render: (r) => <Badge level={r.confidence} /> },
    { key: "originator", header: "originator", mono: true },
    { key: "sector", header: "sector", mono: true },
    { key: "n", header: "changes", num: true, value: (r) => r.recommendation?.actions?.length ?? 0 },
    { key: "gain", header: "gain vs hold", unit: "%", num: true, digits: 1, value: (r) => r.recommendation?.optimization?.gain_vs_base_pct },
    { key: "approved_by_originator", header: "same-person", render: (r) => (r.approved_by_originator ? <span className="tag warn">approved by originator</span> : "—") },
    { key: "updated", header: "last action", mono: true, value: (r) => r.history.at(-1)?.at.slice(0, 16).replace("T", " ") ?? "" },
  ];

  const isLow = rec?.confidence === "LOW";
  const isOriginator = rec?.originator === user.username;
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}><h1>Workflow</h1><span className="sub">Draft → Reviewed → Approved → Implemented → Evaluated. The person who runs cannot approve (policy: log or enforce, §3).</span></div>
      {err && <Msg severity="error" message={err.message} action={err.action} />}
      <section className="card" aria-label="Recommendations">
        {list === null ? <Skeleton lines={5} height={24} /> : list.length === 0 ? <Empty text="No recommendations yet. Create one from a Result." action={<button onClick={() => nav("/result")}>Go to Result</button>} /> : (
          <DataTable rows={list} columns={cols} rowKey={(r) => r.id} selected={new Set(rec ? [rec.id] : [])} onToggle={(k) => nav(`/workflow/${k}`)} maxHeight={280} ariaLabel="Recommendations" />
        )}
      </section>
      {rec && (
        <div className="grid grid-sidebar">
          <section className="card stack" aria-label="Recommendation">
            <div className="row" style={{ justifyContent: "space-between" }}><h2 className="mono">{rec.id}</h2><Badge level={rec.confidence} /></div>
            <StateDiagram state={rec.state} />
            <KV rows={[["Originator", rec.originator], ["Asset / sector", `${rec.asset} / ${rec.sector}`], ["Data hash", <span title={rec.data_hash}>{rec.data_hash.slice(0, 12)}…</span>], ["Config hash", <span title={rec.config_hash}>{rec.config_hash.slice(0, 12)}…</span>], ["Model version", rec.model_version], ["Snapshot (frozen at approval)", rec.snapshot ? <span title={rec.snapshot.digest}>{rec.snapshot.digest.slice(0, 12)}…</span> : "—"], ["Override reason", rec.override_reason ?? "—"], ["Approved by originator", rec.approved_by_originator ? "yes (visible in reports)" : "no"]]} />
            {Object.keys(rec.advanced_overrides ?? {}).length > 0 && <Msg severity="warning" message="Advanced-mode overrides were used for this recommendation." action={JSON.stringify(rec.advanced_overrides)} />}
            <h3>Recommended rates <span className="sub">{rec.units?.rate ?? "bbl/d"}</span></h3>
            <table className="data"><thead><tr><th>injector</th><th className="num">recommended</th><th className="num">implemented</th><th className="num">deviation</th></tr></thead>
              <tbody>{Object.entries(rec.recommended_rates).map(([w, v]) => { const imp = rec.implemented_rates?.[w]; return <tr key={w}><td className="mono">{w}</td><td className="num"><Num v={v} /></td><td className="num"><Num v={imp} /></td><td className="num">{imp !== undefined ? <Num v={imp - v} digits={0} /> : "—"}</td></tr>; })}</tbody></table>
            <h3>History</h3>
            <table className="data"><thead><tr><th>when</th><th>action</th><th>actor</th><th>acting role</th><th>from → to</th><th>note</th></tr></thead>
              <tbody>{rec.history.map((h, k) => <tr key={k}><td className="mono">{h.at.slice(0, 16).replace("T", " ")}</td><td>{h.action}</td><td className="mono">{h.actor}</td><td>{h.acting_role}</td><td className="mono">{h.from_state} → {h.to_state}</td><td>{h.note}</td></tr>)}{rec.history.length === 0 && <tr><td colSpan={6} className="dim">Draft — no actions yet.</td></tr>}</tbody></table>
            {rec.evaluations.length > 0 && <><h3>Evaluations</h3><table className="data"><thead><tr><th className="num">months</th><th>date</th><th className="num">P10</th><th className="num">P50</th><th className="num">P90</th><th className="num">realised</th><th>outcome</th></tr></thead>
              <tbody>{rec.evaluations.map((e, k) => <tr key={k}><td className="num">{String(e.months_after)}</td><td className="mono">{String(e.evaluated_on)}</td><td className="num"><Num v={Number(e.forecast_p10)} /></td><td className="num"><Num v={Number(e.forecast_p50)} /></td><td className="num"><Num v={Number(e.forecast_p90)} /></td><td className="num"><Num v={Number(e.realised)} /></td><td><span className={`tag ${e.outcome === "worse" ? "bad" : e.outcome === "better" ? "ok" : ""}`}>{String(e.outcome)}</span></td></tr>)}</tbody></table></>}
          </section>
          <section className="card stack" aria-label="Actions">
            <h3>Actions <span className="sub">acting as {role}</span></h3>
            <label className="field"><span className="f">Note</span><textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} /></label>
            {rec.state === "DRAFT" && canAct(role, "reviewer") && <button className="primary" onClick={() => act("review")} disabled={busy} data-testid="btn-review">Mark reviewed</button>}
            {rec.state === "REVIEWED" && canAct(role, "approver") && (
              <div className="stack">
                {isLow && <><Msg severity="warning" message="LOW confidence: screening only." action="Approval needs a stated override reason." /><label className="field"><span className="f">Override reason (required)</span><input value={override} onChange={(e) => setOverride(e.target.value)} required data-testid="override-reason" /></label></>}
                {isOriginator && <Msg severity="info" message="You are the originator of this recommendation." action="Under the 'log' policy the approval is recorded as approved-by-originator; under 'enforce' it is blocked." />}
                <button className="primary" onClick={() => act("approve")} disabled={busy || (isLow && !override)} data-testid="btn-approve">Approve</button>
              </div>
            )}
            {(rec.state === "DRAFT" || rec.state === "REVIEWED") && canAct(role, "reviewer", "approver") && <button className="danger" onClick={() => act("reject")} disabled={busy} data-testid="btn-reject">Reject</button>}
            {rec.state === "APPROVED" && canAct(role, "operations") && (
              <div className="stack">
                <span className="sub">Enter what was actually set in the field (deviations from the plan are stored, §3).</span>
                {Object.entries(actual).map(([w, v]) => <label key={w} className="field"><span className="f mono">{w} [{rec.units?.rate ?? "bbl/d"}]</span><input className="mono" value={v} onChange={(e) => setActual({ ...actual, [w]: e.target.value })} /></label>)}
                <button className="primary" onClick={() => act("implement")} disabled={busy} data-testid="btn-implement">Record implementation</button>
              </div>
            )}
            {(rec.state === "IMPLEMENTED" || rec.state === "EVALUATED") && canAct(role, "engineer", "reviewer", "operations") && (
              <div className="stack">
                <span className="sub">Evaluation at 3 and 6 months: realised incremental oil vs the forecast P10–P90 band (§3, §16).</span>
                <div className="row"><label className="field"><span className="f">Months after</span><select value={evalForm.months_after} onChange={(e) => setEvalForm({ ...evalForm, months_after: Number(e.target.value) })}><option value={3}>3</option><option value={6}>6</option></select></label>
                  <label className="field"><span className="f">Realised oil [{rec.units?.volume ?? "bbl"}]</span><input className="mono" value={evalForm.realised_oil} onChange={(e) => setEvalForm({ ...evalForm, realised_oil: e.target.value })} /></label></div>
                <button className="primary" onClick={evaluate} disabled={busy || !evalForm.realised_oil} data-testid="btn-evaluate">Record evaluation</button>
              </div>
            )}
            {rec.state === "REJECTED" && <Msg severity="info" message="This recommendation was rejected." action="Run again with new data or settings to create a new draft." />}
            {!canAct(role, "reviewer", "approver", "operations", "engineer") && <Msg severity="info" message="Read-only view." action="Switch to a role you hold to act." />}
          </section>
        </div>
      )}
    </div>
  );
}
