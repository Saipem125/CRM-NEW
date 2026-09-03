/* §4.2 — one Plotly template registered once: dark tokens, grid #25344B, mono axis titles with units,
   legend below, no logo, hover with well ID / date / value + unit; PNG (2×) and SVG export; copy-data. */
import Plotly from "plotly.js-dist-min";

export const T = {
  bg: "#0B121C", bg2: "#0F1826", panel: "#131E2E", panel2: "#17253A", border: "#25344B",
  text: "#E8E6DC", dim: "#93A0B4", faint: "#5E6C82",
  water: "#4C9BD4", oil: "#E0973F", accent: "#5CC2AE", danger: "#D2685A", good: "#7DB88A", violet: "#9F8FD0",
};

export const METHOD_COLOUR: Record<string, string> = {
  crmt: T.dim, crmp: T.oil, crmip: T.accent, aquifer: T.water, twophase: T.oil, crossflow: T.violet, mpi: T.violet, mlr: T.dim, pinn: T.good, rnn: T.faint,
};

export const CONFIDENCE_COLOUR: Record<string, string> = { HIGH: T.good, MEDIUM: T.oil, LOW: T.danger };

const mono = { family: "IBM Plex Mono, ui-monospace, monospace", color: T.dim, size: 11 };

export function baseLayout(partial: Partial<Plotly.Layout> = {}): Partial<Plotly.Layout> {
  return {
    paper_bgcolor: T.panel,
    plot_bgcolor: T.panel,
    font: { family: "IBM Plex Sans, system-ui, sans-serif", color: T.text, size: 12 },
    margin: { l: 56, r: 16, t: 28, b: 56 },
    hovermode: "closest",
    hoverlabel: { bgcolor: T.bg2, bordercolor: T.border, font: { family: mono.family, color: T.text, size: 12 } },
    legend: { orientation: "h", y: -0.22, x: 0, font: { color: T.dim, size: 11 } },
    xaxis: { gridcolor: T.border, zerolinecolor: T.border, linecolor: T.border, tickfont: mono, title: { font: mono } },
    yaxis: { gridcolor: T.border, zerolinecolor: T.border, linecolor: T.border, tickfont: mono, title: { font: mono } },
    ...partial,
  };
}

export const CONFIG: Partial<Plotly.Config> = { displaylogo: false, responsive: true, displayModeBar: false };

/** Hover template: well · date · value + unit (§4.2). */
export function hover(well: string, unit: string): string {
  return `<b>${well}</b><br>%{x|%Y-%m}<br>%{y:,.0f} ${unit}<extra></extra>`;
}

export async function exportPng(el: HTMLElement, name: string): Promise<void> {
  const url = await Plotly.toImage(el, { format: "png", scale: 2, width: el.clientWidth || 900, height: el.clientHeight || 400 });
  download(url, `${name}.png`);
}

export async function exportSvg(el: HTMLElement, name: string): Promise<void> {
  const url = await Plotly.toImage(el, { format: "svg", width: el.clientWidth || 900, height: el.clientHeight || 400 });
  download(url, `${name}.svg`);
}

export function copyData(rows: Array<Record<string, unknown>>): Promise<void> {
  if (!rows.length) return Promise.resolve();
  const cols = Object.keys(rows[0]);
  const tsv = [cols.join("\t"), ...rows.map((r) => cols.map((c) => String(r[c] ?? "")).join("\t"))].join("\n");
  return navigator.clipboard?.writeText(tsv) ?? Promise.resolve();
}

function download(url: string, name: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
}

/** Shaded blind-test window (§4.2). */
export function blindShape(x0: string, x1: string): Partial<Plotly.Shape> {
  return { type: "rect", xref: "x", yref: "paper", x0, x1, y0: 0, y1: 1, fillcolor: T.violet, opacity: 0.08, line: { width: 0 } };
}

export function eventMarkers(events: Array<{ date: string; label: string }>): Partial<Plotly.Shape>[] {
  return events.map((e) => ({ type: "line", xref: "x", yref: "paper", x0: e.date, x1: e.date, y0: 0, y1: 1, line: { color: T.faint, width: 1, dash: "dot" } }));
}

export { Plotly };
