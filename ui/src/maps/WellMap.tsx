/* §4.3 D3/SVG well map and connectivity matrix. CSS variables for colour so exports theme correctly. */
import * as d3 from "d3";
import { useEffect, useMemo, useRef, useState } from "react";
import type { SectorBundle } from "../api/types";

export interface Alert { injector?: string | null; producer: string; text: string }

interface MapProps {
  sector: SectorBundle;
  selected: string | null;
  onSelect: (id: string | null) => void;
  alerts?: Alert[];
  sectorsPolys?: number[][][];
  faults?: number[][][];
}

const TRI = (x: number, y: number, r: number) => `M${x - r},${y - r * 0.8} L${x + r},${y - r * 0.8} L${x},${y + r} Z`;

export function WellMap({ sector, selected, onSelect, alerts = [], sectorsPolys = [], faults = [] }: MapProps) {
  const ref = useRef<SVGSVGElement>(null);
  const [layers, setLayers] = useState({ arrows: true, tau: false, labels: true, sectors: true, alerts: true });
  const m = sector.model;
  const pts = useMemo(() => [
    ...sector.injectors.map((w, k) => ({ id: w.id, well: w.well, kind: "INJ" as const, k, xy: w.xy })),
    ...sector.producers.map((w, k) => ({ id: w.id, well: w.well, kind: "PROD" as const, k, xy: w.xy })),
  ].filter((p) => p.xy), [sector]);
  const noCoords = pts.length === 0;

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    if (!ref.current || noCoords) return;
    const W = ref.current.clientWidth || 720, H = ref.current.clientHeight || 420, pad = 44;
    const xs = pts.map((p) => p.xy![0]), ys = pts.map((p) => p.xy![1]);
    const [x0, x1] = d3.extent(xs) as [number, number], [y0, y1] = d3.extent(ys) as [number, number];
    // true coordinates, no distortion: one scale for both axes
    const span = Math.max(x1 - x0, y1 - y0, 1);
    const s = Math.min((W - 2 * pad) / span, (H - 2 * pad) / span);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const X = (x: number) => W / 2 + (x - cx) * s, Y = (y: number) => H / 2 - (y - cy) * s;
    const g = svg.append("g").attr("class", "zoom");
    svg.call(d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.5, 8]).on("zoom", (e) => g.attr("transform", e.transform)) as never);
    const defs = svg.append("defs");
    defs.append("marker").attr("id", "arrowhead").attr("viewBox", "0 0 10 10").attr("refX", 9).attr("refY", 5).attr("markerWidth", 6).attr("markerHeight", 6).attr("orient", "auto-start-reverse").append("path").attr("d", "M0,0 L10,5 L0,10z").attr("fill", "var(--accent)");
    const tauScale = m ? d3.scaleSequential(d3.interpolateRgb("var(--accent)", "var(--violet)")).domain(d3.extent(m.tau_days) as [number, number]) : null;
    // sector boundaries / faults
    if (layers.sectors) {
      sectorsPolys.forEach((poly) => g.append("path").attr("d", d3.line()(poly.map(([x, y]) => [X(x), Y(y)]) as [number, number][]) + "Z").attr("fill", "none").attr("stroke", "var(--faint)").attr("stroke-dasharray", "5,4"));
      faults.forEach((line) => g.append("path").attr("d", d3.line()(line.map(([x, y]) => [X(x), Y(y)]) as [number, number][])).attr("fill", "none").attr("stroke", "var(--danger)").attr("stroke-width", 2));
    }
    // arrows: width ∝ f_ij, opacity ∝ pair confidence
    if (m && layers.arrows) {
      const maxF = d3.max(m.f_ij.flat()) ?? 1;
      sector.injectors.forEach((inj, i) => sector.producers.forEach((prod, j) => {
        const f = m.f_ij[i][j];
        if (!inj.xy || !prod.xy || f < 0.02) return;
        const conf = m.pair_confidence[i]?.[j] ?? 1;
        const dim = selected && selected !== inj.id && selected !== prod.id;
        const [ax, ay, bx, by] = [X(inj.xy[0]), Y(inj.xy[1]), X(prod.xy[0]), Y(prod.xy[1])];
        const dx = bx - ax, dy = by - ay, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L;
        g.append("line").attr("x1", ax + ux * 12).attr("y1", ay + uy * 12).attr("x2", bx - ux * 14).attr("y2", by - uy * 14)
          .attr("stroke", layers.tau && tauScale ? tauScale(m.tau_days[j]) : "var(--accent)").attr("stroke-width", 1 + 6 * (f / maxF)).attr("opacity", dim ? 0.08 : 0.25 + 0.75 * conf).attr("marker-end", "url(#arrowhead)")
          .append("title").text(`${inj.well} → ${prod.well}\nf = ${f.toFixed(3)} · τ = ${m.tau_days[j].toFixed(0)} d · confidence ${(conf * 100).toFixed(0)} %`);
      }));
    }
    // wells
    pts.forEach((p) => {
      const x = X(p.xy![0]), y = Y(p.xy![1]);
      const type = sector.well_types[p.id] ?? p.kind;
      const node = g.append("g").attr("cursor", "pointer").attr("tabindex", 0).attr("role", "button").attr("aria-label", `${p.well} ${type}`)
        .on("click", () => onSelect(selected === p.id ? null : p.id)).on("keydown", (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") onSelect(selected === p.id ? null : p.id); });
      const sel = selected === p.id;
      if (type === "MIXED") {
        node.append("path").attr("d", `M${x},${y} m-8,0 a8,8 0 0,1 16,0 Z`).attr("fill", "var(--violet)");
        node.append("path").attr("d", TRI(x, y + 2, 7)).attr("fill", "var(--violet)").attr("opacity", 0.7);
      } else if (p.kind === "INJ") node.append("path").attr("d", TRI(x, y, 9)).attr("fill", "var(--water)").attr("stroke", sel ? "var(--text)" : "var(--bg)").attr("stroke-width", sel ? 2.5 : 1);
      else node.append("circle").attr("cx", x).attr("cy", y).attr("r", 8).attr("fill", "var(--oil)").attr("stroke", sel ? "var(--text)" : "var(--bg)").attr("stroke-width", sel ? 2.5 : 1);
      node.append("title").text(`${p.well} · ${type}`);
      if (layers.labels) g.append("text").attr("x", x + 11).attr("y", y - 9).attr("fill", "var(--text)").attr("font-family", "IBM Plex Mono, monospace").attr("font-size", 11).text(p.well);
    });
    // change alerts: pulsing rings on the affected pair
    if (layers.alerts) alerts.forEach((a) => {
      [a.injector, a.producer].forEach((id) => {
        const p = pts.find((q) => q.id === id || q.well === id);
        if (!p) return;
        g.append("circle").attr("cx", X(p.xy![0])).attr("cy", Y(p.xy![1])).attr("r", 10).attr("fill", "none").attr("stroke", "var(--danger)").attr("stroke-width", 2).attr("class", "pulse").append("title").text(a.text);
      });
    });
    // scale bar + north arrow
    const barM = Math.pow(10, Math.floor(Math.log10(span / 4)));
    const bx0 = 16, by0 = H - 16;
    g.append("line").attr("x1", bx0).attr("x2", bx0 + barM * s).attr("y1", by0).attr("y2", by0).attr("stroke", "var(--text)").attr("stroke-width", 2);
    g.append("text").attr("x", bx0).attr("y", by0 - 6).attr("fill", "var(--dim)").attr("font-family", "IBM Plex Mono, monospace").attr("font-size", 10).text(`${barM.toLocaleString()} m`);
    const nx = W - 24, ny = 30;
    g.append("path").attr("d", `M${nx},${ny - 14} L${nx + 6},${ny + 4} L${nx},${ny} L${nx - 6},${ny + 4} Z`).attr("fill", "var(--dim)");
    g.append("text").attr("x", nx).attr("y", ny + 16).attr("text-anchor", "middle").attr("fill", "var(--dim)").attr("font-size", 10).attr("font-family", "IBM Plex Mono, monospace").text("N");
  }, [sector, pts, selected, layers, alerts, sectorsPolys, faults, m, noCoords, onSelect]);

  return (
    <div className="stack" style={{ height: "100%" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="legend">
          <span><i style={{ background: "var(--water)", clipPath: "polygon(0 0,100% 0,50% 100%)" }} />injector ▼</span>
          <span><i style={{ background: "var(--oil)", borderRadius: "50%" }} />producer ●</span>
          <span><i style={{ background: "var(--violet)" }} />mixed ◐</span>
          <span><i style={{ background: "var(--accent)", height: 3 }} />arrow width ∝ f<sub>ij</sub>, opacity ∝ confidence</span>
        </div>
        <div className="row" style={{ gap: 4 }}>
          {(["arrows", "tau", "labels", "sectors", "alerts"] as const).map((k) => (
            <button key={k} className={`sm ${layers[k] ? "primary" : "ghost"}`} onClick={() => setLayers({ ...layers, [k]: !layers[k] })} aria-pressed={layers[k]}>{k === "tau" ? "τ colour" : k}</button>
          ))}
          <button className="sm ghost" onClick={() => ref.current && exportSvg(ref.current)}>SVG</button>
        </div>
      </div>
      {noCoords ? <div className="msg info">No well coordinates were loaded, so the map cannot be drawn. <span className="action">Add the wells to the coordinates table.</span></div> : <svg ref={ref} className="map" role="img" aria-label="Well map with connectivity" data-testid="well-map" />}
    </div>
  );
}

function exportSvg(svg: SVGSVGElement) {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  const vars: Record<string, string> = { "--bg": "#0B121C", "--bg2": "#0F1826", "--panel": "#131E2E", "--border": "#25344B", "--text": "#E8E6DC", "--dim": "#93A0B4", "--faint": "#5E6C82", "--water": "#4C9BD4", "--oil": "#E0973F", "--accent": "#5CC2AE", "--danger": "#D2685A", "--violet": "#9F8FD0" };
  let s = new XMLSerializer().serializeToString(clone);
  Object.entries(vars).forEach(([k, v]) => { s = s.split(`var(${k})`).join(v); });
  const url = URL.createObjectURL(new Blob([s], { type: "image/svg+xml" }));
  const a = document.createElement("a"); a.href = url; a.download = "well-map.svg"; a.click();
}

/** Connectivity matrix: heat-map f_ij, τ in tooltip; rows injectors, columns producers; sortable; excluded pairs hatched. */
export function ConnectivityMatrix({ sector, onSelect }: { sector: SectorBundle; onSelect?: (id: string) => void }) {
  const [sortBy, setSortBy] = useState<"name" | "sum">("name");
  const m = sector.model;
  if (!m) return <div className="msg info">No fitted model for this sector yet.</div>;
  const rows = sector.injectors.map((w, i) => ({ w, i, sum: m.sum_f_per_injector[i] })).sort((a, b) => (sortBy === "sum" ? b.sum - a.sum : a.w.well.localeCompare(b.w.well)));
  const maxF = Math.max(...m.f_ij.flat(), 0.01);
  const cell = 34;
  return (
    <div className="stack">
      <div className="row" style={{ justifyContent: "space-between" }}><span className="sub">f<sub>ij</sub> — rows injectors, columns producers; hover for τ</span><span className="seg"><button className={sortBy === "name" ? "on" : ""} onClick={() => setSortBy("name")}>by name</button><button className={sortBy === "sum" ? "on" : ""} onClick={() => setSortBy("sum")}>by Σf</button></span></div>
      <div style={{ overflow: "auto" }}>
        <svg width={90 + cell * sector.producers.length} height={40 + cell * rows.length} role="img" aria-label="Connectivity matrix" data-testid="connectivity-matrix">
          <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" stroke="var(--faint)" strokeWidth="1" /></pattern></defs>
          {sector.producers.map((p, j) => <text key={p.id} x={90 + j * cell + cell / 2} y={26} textAnchor="middle" fill="var(--dim)" fontFamily="IBM Plex Mono" fontSize={11}>{p.well}</text>)}
          {rows.map(({ w, i }, r) => (
            <g key={w.id}>
              <text x={84} y={40 + r * cell + cell / 2 + 4} textAnchor="end" fill="var(--text)" fontFamily="IBM Plex Mono" fontSize={11} style={{ cursor: "pointer" }} onClick={() => onSelect?.(w.id)}>{w.well}</text>
              {sector.producers.map((p, j) => {
                const f = m.f_ij[i][j];
                const excluded = sector.distances && f === 0 && (m.pair_confidence[i]?.[j] ?? 1) === 1 && sector.distances[i][j] > 0 && f < 0.001;
                return (
                  <g key={p.id}>
                    <rect x={90 + j * cell} y={40 + r * cell} width={cell - 2} height={cell - 2} rx={3} fill={excluded ? "url(#hatch)" : `rgba(92,194,174,${0.12 + 0.88 * (f / maxF)})`} stroke="var(--border)">
                      <title>{`${w.well} → ${p.well}\nf = ${f.toFixed(3)}\nτ = ${m.tau_days[j].toFixed(0)} d`}</title>
                    </rect>
                    {f >= 0.05 && <text x={90 + j * cell + cell / 2 - 1} y={40 + r * cell + cell / 2 + 3} textAnchor="middle" fill={f / maxF > 0.6 ? "var(--bg)" : "var(--text)"} fontFamily="IBM Plex Mono" fontSize={10}>{f.toFixed(2)}</text>}
                  </g>
                );
              })}
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}
