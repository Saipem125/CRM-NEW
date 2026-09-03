/* §4.3 SVG schematics using CSS variables: the five-layer pipeline (Run screen) and the workflow state diagram. */

const LAYERS = [
  ["L1", "Loading data"],
  ["L2", "Cleaning data"],
  ["L3", "Testing models"],
  ["L4", "Optimizing"],
  ["L5", "Preparing results"],
];

/** stage: number of layers completed (0–5); the active layer pulses. */
export function PipelineSchematic({ stage, failed }: { stage: number; failed?: boolean }) {
  const w = 720, h = 96, box = 124, gap = (w - 24 - box * 5) / 4;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`Pipeline: ${Math.min(stage, 5)} of 5 stages complete`} style={{ maxWidth: 760 }}>
      {LAYERS.map(([id, label], k) => {
        const x = 12 + k * (box + gap);
        const done = k < stage, active = k === stage && !failed, bad = failed && k === stage;
        const stroke = bad ? "var(--danger)" : done ? "var(--good)" : active ? "var(--accent)" : "var(--border)";
        return (
          <g key={id}>
            {k > 0 && <line x1={x - gap} y1={h / 2} x2={x} y2={h / 2} stroke={done || active ? "var(--accent)" : "var(--border)"} strokeWidth={1.4} />}
            <rect x={x} y={18} width={box} height={60} rx={6} fill={done ? "var(--panel2)" : "var(--panel)"} stroke={stroke} strokeWidth={active ? 2 : 1.2} className={active ? "pulse-box" : ""} />
            <text x={x + 10} y={40} fill="var(--dim)" fontFamily="IBM Plex Mono, monospace" fontSize={11}>{id}</text>
            <text x={x + 10} y={62} fill={done || active ? "var(--text)" : "var(--dim)"} fontFamily="IBM Plex Sans, sans-serif" fontSize={12.5}>{label}</text>
            {done && <text x={x + box - 16} y={40} fill="var(--good)" fontSize={12}>✓</text>}
            {bad && <text x={x + box - 16} y={40} fill="var(--danger)" fontSize={12}>✕</text>}
          </g>
        );
      })}
    </svg>
  );
}

const STATES = ["DRAFT", "REVIEWED", "APPROVED", "IMPLEMENTED", "EVALUATED"] as const;

export function StateDiagram({ state }: { state: string }) {
  const w = 760, h = 70, box = 128, gap = (w - 20 - box * 5) / 4;
  const idx = STATES.indexOf(state as (typeof STATES)[number]);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`Workflow state ${state}`} style={{ maxWidth: 800 }}>
      {STATES.map((s, k) => {
        const x = 10 + k * (box + gap);
        const cur = s === state, done = idx > k;
        return (
          <g key={s}>
            {k > 0 && <line x1={x - gap} y1={h / 2} x2={x - 4} y2={h / 2} stroke={done || cur ? "var(--accent)" : "var(--border)"} strokeWidth={1.4} markerEnd="url(#arr)" />}
            <rect x={x} y={14} width={box} height={42} rx={6} fill={cur ? "var(--panel2)" : "var(--panel)"} stroke={cur ? "var(--accent)" : done ? "var(--good)" : "var(--border)"} strokeWidth={cur ? 2 : 1.2} />
            <text x={x + box / 2} y={40} textAnchor="middle" fill={cur ? "var(--text)" : "var(--dim)"} fontFamily="Space Grotesk, sans-serif" fontSize={12} fontWeight={600}>{s}</text>
          </g>
        );
      })}
      {state === "REJECTED" && <text x={w / 2} y={66} textAnchor="middle" fill="var(--danger)" fontFamily="IBM Plex Mono, monospace" fontSize={11}>REJECTED</text>}
      <defs><marker id="arr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6z" fill="var(--faint)" /></marker></defs>
    </svg>
  );
}
