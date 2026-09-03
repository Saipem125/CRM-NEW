/* §4.2 Plotly plots on the shared template. Every plot: axis titles with units, legend, hover, PNG/SVG export, copy data. */
import { useEffect, useRef, type ReactNode } from "react";
import type { SectorBundle } from "../api/types";
import { CONFIG, METHOD_COLOUR, Plotly, T, baseLayout, blindShape, copyData, eventMarkers, exportPng, exportSvg, hover } from "../plotTemplate";

interface PlotProps { data: Plotly.Data[]; layout: Partial<Plotly.Layout>; name: string; rows: Array<Record<string, unknown>>; title: ReactNode; height?: number; corner?: ReactNode }

export function Plot({ data, layout, name, rows, title, height = 300, corner }: PlotProps) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    Plotly.react(el, data, baseLayout({ ...layout, height }), CONFIG);
    return () => { Plotly.purge(el); };
  }, [data, layout, height]);
  return (
    <div className="plot-block">
      <div className="plot-head">
        <h3 style={{ margin: 0 }}>{title}</h3>
        <div className="row" style={{ gap: 10 }}>
          {corner && <span className="mono dim" style={{ fontSize: 11.5 }}>{corner}</span>}
          <div className="plot-tools">
            <button className="sm ghost" onClick={() => ref.current && exportPng(ref.current, name)} title="Export PNG (2×)">PNG</button>
            <button className="sm ghost" onClick={() => ref.current && exportSvg(ref.current, name)} title="Export SVG">SVG</button>
            <button className="sm ghost" onClick={() => copyData(rows)} title="Copy data (TSV)">Copy data</button>
          </div>
        </div>
      </div>
      <div ref={ref} className="plot" style={{ height }} data-testid={`plot-${name}`} />
    </div>
  );
}

const fmt = (v: number | null) => (v === null || v === undefined ? "" : v.toFixed(1));

/** Forecast fan (§4.2): P10–P90 band accent 18 %, P50 line, history dim, blind window shaded, implemented plan dashed, event ticks. */
export function ForecastFan({ sector, producer, unit = "bbl/d", implemented, events = [] }: { sector: SectorBundle; producer?: string; unit?: string; implemented?: number[]; events?: { date: string; label: string }[] }) {
  const fc = sector.forecast;
  const j = producer ? sector.producers.findIndex((p) => p.id === producer) : -1;
  const pick = (m: number[][] | number[], key: "p10" | "p50" | "p90") => (j >= 0 ? (m as number[][]).map((r) => r[j]) : (fc?.[key === "p10" ? "field_plan" : "field_plan"] as unknown as number[]));
  const hist = j >= 0 ? sector.producers[j].oil : sector.producers.reduce((acc, p) => acc.map((v, i) => v + (p.oil[i] ?? 0)), new Array(sector.dates.length).fill(0) as number[]);
  const name = j >= 0 ? sector.producers[j].well : "Field";
  const data: Plotly.Data[] = [{ x: sector.dates, y: hist, type: "scatter", mode: "lines", name: "history (oil)", line: { color: T.dim, width: 1.5 }, hovertemplate: hover(name, unit) }];
  const rows: Array<Record<string, unknown>> = sector.dates.map((d, i) => ({ date: d, history: fmt(hist[i]) }));
  if (fc) {
    const p10 = j >= 0 ? pick(fc.plan.p10, "p10") : fc.field_plan.p10;
    const p50 = j >= 0 ? pick(fc.plan.p50, "p50") : fc.field_plan.p50;
    const p90 = j >= 0 ? pick(fc.plan.p90, "p90") : fc.field_plan.p90;
    const base = j >= 0 ? fc.base.p50.map((r) => r[j]) : fc.field_base.p50;
    data.push(
      { x: fc.dates, y: p90, type: "scatter", mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
      { x: fc.dates, y: p10, type: "scatter", mode: "lines", fill: "tonexty", fillcolor: "rgba(92,194,174,0.18)", line: { width: 0 }, name: "P10–P90", hoverinfo: "skip" },
      { x: fc.dates, y: p50, type: "scatter", mode: "lines", name: "plan P50", line: { color: T.accent, width: 2 }, hovertemplate: hover(name + " plan", unit) },
      { x: fc.dates, y: base, type: "scatter", mode: "lines", name: "hold current P50", line: { color: T.faint, width: 1.5, dash: "dot" }, hovertemplate: hover(name + " hold", unit) },
    );
    if (implemented) data.push({ x: fc.dates, y: implemented, type: "scatter", mode: "lines", name: "implemented plan", line: { color: T.oil, width: 2, dash: "dash" } });
    fc.dates.forEach((d, i) => rows.push({ date: d, p10: fmt(p10[i]), p50: fmt(p50[i]), p90: fmt(p90[i]), hold: fmt(base[i]) }));
  }
  const shapes = [blindShape(sector.dates[sector.blind_start_index], sector.dates[sector.dates.length - 1]), ...eventMarkers(events)];
  const layout: Partial<Plotly.Layout> = { shapes, xaxis: { title: { text: "date" } }, yaxis: { title: { text: `oil rate [${unit}]` }, rangemode: "tozero" }, annotations: [{ x: sector.dates[sector.blind_start_index], y: 1, yref: "paper", text: "blind test →", showarrow: false, xanchor: "left", font: { color: T.violet, size: 10 } }] };
  return <Plot data={data} layout={layout} name={`forecast-${name}`} rows={rows} title={<>Forecast · {name} {fc ? <span className="dim" style={{ fontWeight: 400 }}>({fc.n_members} realizations)</span> : null}</>} height={320} />;
}

/** History match per well: raw faint points, cleaned dim line, model oil line, residual sub-panel, blind shaded, R²/MAPE in the corner. */
export function HistoryMatch({ sector, producerId, unit = "bbl/d" }: { sector: SectorBundle; producerId: string; unit?: string }) {
  const p = sector.producers.find((x) => x.id === producerId);
  if (!p) return null;
  const resid = p.liquid.map((v, i) => v - (p.model[i] ?? 0));
  const data: Plotly.Data[] = [
    { x: sector.dates, y: p.raw_liquid, type: "scatter", mode: "markers", name: "raw", marker: { color: T.faint, size: 4 }, hovertemplate: hover(p.well + " raw", unit) },
    { x: sector.dates, y: p.liquid, type: "scatter", mode: "lines", name: "cleaned", line: { color: T.dim, width: 1.5 }, hovertemplate: hover(p.well, unit) },
    { x: sector.dates, y: p.model, type: "scatter", mode: "lines", name: "model", line: { color: T.oil, width: 2 }, hovertemplate: hover(p.well + " model", unit) },
    { x: sector.dates, y: resid, type: "bar", name: "residual", marker: { color: T.water }, yaxis: "y2", hovertemplate: hover(p.well + " residual", unit) },
  ];
  const blind = sector.dates[sector.blind_start_index];
  const layout: Partial<Plotly.Layout> = {
    grid: { rows: 2, columns: 1, roworder: "top to bottom" },
    yaxis: { title: { text: `liquid [${unit}]` }, domain: [0.36, 1] },
    yaxis2: { title: { text: `residual [${unit}]` }, domain: [0, 0.28], gridcolor: T.border, tickfont: { family: "IBM Plex Mono", size: 10, color: T.dim }, zerolinecolor: T.faint },
    xaxis: { title: { text: "date" } },
    shapes: [blindShape(blind, sector.dates[sector.dates.length - 1]), { ...blindShape(blind, sector.dates[sector.dates.length - 1]), yref: "y2 domain" as never }],
  };
  const rows = sector.dates.map((d, i) => ({ date: d, raw: fmt(p.raw_liquid[i]), cleaned: fmt(p.liquid[i]), model: fmt(p.model[i]), residual: fmt(resid[i]) }));
  return <Plot data={data} layout={layout} name={`fit-${p.well}`} rows={rows} title={<>History match · {p.well}</>} height={340} corner={`blind R² ${p.blind_r2?.toFixed(3) ?? "—"} · MAPE ${p.blind_mape?.toFixed(1) ?? "—"} %`} />;
}

/** Δt/τ plot exactly as the Selector reference: exponential step response with sampling dots coloured by adequacy. */
export function DtTauPlot({ dt, tau }: { dt: number; tau: number | null }) {
  const tauV = tau ?? 3 * dt;
  const ratio = tauV / dt;
  const Tmax = Math.max(tauV * 4, dt * 8);
  const t = Array.from({ length: 201 }, (_, i) => (Tmax * i) / 200);
  const step = (x: number) => (x < Tmax * 0.1 ? 0 : 1 - Math.exp(-(x - Tmax * 0.1) / tauV));
  const dots: number[] = [];
  for (let x = 0; x <= Tmax; x += dt) dots.push(x);
  const colour = ratio >= 5 ? T.good : ratio >= 3 ? T.oil : T.danger;
  const data: Plotly.Data[] = [
    { x: t, y: t.map(step), type: "scatter", mode: "lines", name: "producer response", line: { color: T.oil, width: 2 }, hovertemplate: "t %{x:.0f} d · q/q∞ %{y:.2f}<extra></extra>" },
    { x: dots, y: dots.map(step), type: "scatter", mode: "markers", name: `samples every Δt = ${dt.toFixed(0)} d`, marker: { color: colour, size: 7 }, hovertemplate: "sample at %{x:.0f} d<extra></extra>" },
  ];
  const layout: Partial<Plotly.Layout> = {
    xaxis: { title: { text: "time [days]" } }, yaxis: { title: { text: "q / q∞" }, range: [-0.05, 1.05] },
    shapes: [{ type: "line", x0: Tmax * 0.1, x1: Tmax * 0.1, y0: 0, y1: 1, yref: "paper", line: { color: T.water, dash: "dot" } }, { type: "line", x0: Tmax * 0.1, x1: Tmax * 0.1 + tauV, y0: 0.632, y1: 0.632, line: { color: T.accent, dash: "dot" } }],
    annotations: [{ x: Tmax * 0.1, y: 0.98, yref: "paper", text: "injection step", showarrow: false, xanchor: "left", font: { color: T.water, size: 10 } }, { x: Tmax * 0.1 + tauV, y: 0.632, text: `τ = ${tauV.toFixed(0)} d (63 %)`, showarrow: false, xanchor: "left", font: { color: T.accent, size: 10 } }],
  };
  const verdict = ratio >= 5 ? "Sampling adequate" : ratio >= 3 ? "Marginal — τ resolvable but noisy" : "Too coarse — τ collapses toward Δt";
  return <Plot data={data} layout={layout} name="dt-tau" rows={dots.map((x) => ({ t_days: x.toFixed(0), response: step(x).toFixed(3) }))} title={<>Δt / τ check</>} height={260} corner={`τ/Δt = ${ratio.toFixed(1)} · ${verdict}`} />;
}

/** Leaderboard: horizontal bars in method colour; excluded methods greyed with reason on hover. */
export function LeaderboardPlot({ sector }: { sector: SectorBundle }) {
  const fitted = sector.leaderboard.filter((r) => r.rank !== null && r.rank !== undefined) as Array<{ rank: number; variant: string; label: string; score: number; blind_r2: number | null; blind_mape: number | null }>;
  const excluded = sector.eligibility.filter((e) => !e.eligible || !e.available);
  const labels = [...fitted.map((r) => r.label), ...excluded.map((e) => e.variant.toUpperCase())];
  const values = [...fitted.map((r) => r.score), ...excluded.map(() => 0.02)];
  const colours = [...fitted.map((r) => METHOD_COLOUR[r.variant] ?? T.dim), ...excluded.map(() => T.border)];
  const texts = [...fitted.map((r) => `score ${r.score.toFixed(3)} · blind R² ${(r.blind_r2 ?? NaN).toFixed(3)} · MAPE ${(r.blind_mape ?? NaN).toFixed(1)} %`), ...excluded.map((e) => `excluded: ${e.reason}`)];
  const data: Plotly.Data[] = [{ type: "bar", orientation: "h", y: labels, x: values, marker: { color: colours }, text: texts, hovertemplate: "<b>%{y}</b><br>%{text}<extra></extra>", textposition: "none" }];
  const layout: Partial<Plotly.Layout> = { xaxis: { title: { text: "composite score" }, range: [0, 1] }, yaxis: { autorange: "reversed", tickfont: { family: "IBM Plex Mono", size: 11, color: T.text } }, margin: { l: 170 } };
  return <Plot data={data} layout={layout} name="leaderboard" rows={fitted.map((r) => ({ rank: r.rank, method: r.label, score: r.score.toFixed(3), blind_r2: fmt(r.blind_r2), mape: fmt(r.blind_mape) }))} title="Model tournament" height={Math.max(220, 26 * labels.length + 90)} />;
}

/** Injector efficiency: oil per bbl injected, sorted, current vs optimized side by side. */
export function InjectorEfficiency({ sector, unit = "bbl/d" }: { sector: SectorBundle; unit?: string }) {
  const e = sector.forecast?.injector_efficiency;
  if (!e) return null;
  const order = e.injectors.map((_, i) => i).sort((a, b) => e.oil_per_bbl[b] - e.oil_per_bbl[a]);
  const names = order.map((i) => sector.injectors[i]?.well ?? e.injectors[i]);
  const data: Plotly.Data[] = [
    { type: "bar", x: names, y: order.map((i) => e.current_oil[i]), name: `current (oil ${unit})`, marker: { color: T.faint }, hovertemplate: "%{x}<br>%{y:,.0f} " + unit + "<extra>current</extra>" },
    { type: "bar", x: names, y: order.map((i) => e.optimized_oil[i]), name: `optimized (oil ${unit})`, marker: { color: T.accent }, hovertemplate: "%{x}<br>%{y:,.0f} " + unit + "<extra>optimized</extra>" },
    { type: "scatter", x: names, y: order.map((i) => e.oil_per_bbl[i]), name: "oil per bbl injected", yaxis: "y2", mode: "lines+markers", marker: { color: T.oil, size: 8 }, line: { color: T.oil, width: 1 }, hovertemplate: "%{x}<br>%{y:.3f} bbl/bbl<extra></extra>" },
  ];
  const layout: Partial<Plotly.Layout> = { barmode: "group", xaxis: { title: { text: "injector" }, tickfont: { family: "IBM Plex Mono", size: 11 } }, yaxis: { title: { text: `oil supported [${unit}]` } }, yaxis2: { title: { text: "oil / bbl injected" }, overlaying: "y", side: "right", showgrid: false, tickfont: { family: "IBM Plex Mono", size: 10, color: T.oil } } };
  return <Plot data={data} layout={layout} name="injector-efficiency" rows={order.map((i) => ({ injector: names[order.indexOf(i)], oil_per_bbl: e.oil_per_bbl[i].toFixed(3), current: e.current[i].toFixed(0), optimized: e.optimized[i].toFixed(0) }))} title="Injector efficiency" height={300} />;
}

/** Tornado: economic sensitivity, centred, labelled. rows: [{label, low, high}] as Δ vs base. */
export function Tornado({ rows, unit = "$" }: { rows: { label: string; low: number; high: number }[]; unit?: string }) {
  const sorted = [...rows].sort((a, b) => Math.abs(b.high - b.low) - Math.abs(a.high - a.low));
  const data: Plotly.Data[] = [
    { type: "bar", orientation: "h", y: sorted.map((r) => r.label), x: sorted.map((r) => Math.min(r.low, 0)), name: "downside", marker: { color: T.danger }, hovertemplate: "%{y}: %{x:,.0f} " + unit + "<extra></extra>" },
    { type: "bar", orientation: "h", y: sorted.map((r) => r.label), x: sorted.map((r) => Math.max(r.high, 0)), name: "upside", marker: { color: T.good }, hovertemplate: "%{y}: %{x:,.0f} " + unit + "<extra></extra>" },
  ];
  const layout: Partial<Plotly.Layout> = { barmode: "overlay", xaxis: { title: { text: `Δ NPV vs base [${unit}]` }, zeroline: true, zerolinecolor: T.text }, yaxis: { autorange: "reversed", tickfont: { family: "IBM Plex Mono", size: 11, color: T.text } }, margin: { l: 150 } };
  return <Plot data={data} layout={layout} name="tornado" rows={sorted.map((r) => ({ driver: r.label, low: r.low.toFixed(0), high: r.high.toFixed(0) }))} title="Economic sensitivity" height={Math.max(200, 30 * sorted.length + 90)} />;
}
