/* §4.4 tables: mono IDs/dates/numbers, right-aligned numbers with separators and unit in header,
   sticky header, virtualised beyond 200 rows, sortable, column filters, row selection with count. */
import { useMemo, useRef, useState, type ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

export interface Col<T> {
  key: string;
  header: string;
  unit?: string;
  num?: boolean;
  mono?: boolean;
  digits?: number;
  width?: number;
  render?: (row: T) => ReactNode;
  value?: (row: T) => string | number | null | undefined;
  filter?: boolean;
}

interface Props<T> {
  rows: T[];
  columns: Col<T>[];
  rowKey: (r: T) => string;
  selected?: Set<string>;
  onToggle?: (key: string) => void;
  onToggleAll?: (keys: string[]) => void;
  maxHeight?: number;
  empty?: string;
  ariaLabel?: string;
}

export function DataTable<T>({ rows, columns, rowKey, selected, onToggle, onToggleAll, maxHeight = 540, empty = "No rows.", ariaLabel }: Props<T>) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const val = (c: Col<T>, r: T): string | number | null | undefined => (c.value ? c.value(r) : (r as Record<string, unknown>)[c.key] as string | number | null | undefined);

  const shown = useMemo(() => {
    let out = rows.filter((r) => columns.every((c) => !filters[c.key] || String(val(c, r) ?? "").toLowerCase().includes(filters[c.key].toLowerCase())));
    if (sort) {
      const c = columns.find((x) => x.key === sort.key);
      if (c) out = [...out].sort((a, b) => { const va = val(c, a), vb = val(c, b); if (va == null) return 1; if (vb == null) return -1; return (va < vb ? -1 : va > vb ? 1 : 0) * sort.dir; });
    }
    return out;
  }, [rows, columns, filters, sort]); // eslint-disable-line react-hooks/exhaustive-deps

  const parentRef = useRef<HTMLDivElement>(null);
  const virtual = shown.length > 200;
  const rowVirtualizer = useVirtualizer({ count: shown.length, getScrollElement: () => parentRef.current, estimateSize: () => 33, overscan: 12, enabled: virtual });
  const items = virtual ? rowVirtualizer.getVirtualItems() : shown.map((_, i) => ({ index: i, start: 0, key: i }));
  const padTop = virtual && items.length ? items[0].start : 0;
  const padBottom = virtual && items.length ? rowVirtualizer.getTotalSize() - items[items.length - 1].start - 33 : 0;
  const keys = shown.map(rowKey);
  const allSel = selected ? keys.every((k) => selected.has(k)) && keys.length > 0 : false;

  return (
    <div>
      <div className="table-wrap" ref={parentRef} style={{ maxHeight }}>
        <table className="data" aria-label={ariaLabel}>
          <thead>
            <tr>
              {selected && <th style={{ width: 28 }}><input type="checkbox" aria-label="Select all" checked={allSel} onChange={() => onToggleAll?.(keys)} /></th>}
              {columns.map((c) => (
                <th key={c.key} className={c.num ? "num" : ""} style={{ width: c.width }} onClick={() => setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 1 ? -1 : 1 } : { key: c.key, dir: 1 }))} aria-sort={sort?.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                  {c.header}{c.unit && <span className="unit"> [{c.unit}]</span>}{sort?.key === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                  {c.filter && <input className="filter mono" placeholder="filter" value={filters[c.key] ?? ""} onClick={(e) => e.stopPropagation()} onChange={(e) => setFilters({ ...filters, [c.key]: e.target.value })} aria-label={`Filter ${c.header}`} />}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {padTop > 0 && <tr><td style={{ height: padTop, padding: 0, border: 0 }} colSpan={columns.length + 1} /></tr>}
            {items.map((it) => {
              const r = shown[it.index];
              const k = rowKey(r);
              return (
                <tr key={k} className={selected?.has(k) ? "selected" : ""} onClick={() => onToggle?.(k)} tabIndex={0} onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); onToggle?.(k); } }}>
                  {selected && <td><input type="checkbox" checked={selected.has(k)} onChange={() => onToggle?.(k)} onClick={(e) => e.stopPropagation()} aria-label={`Select ${k}`} /></td>}
                  {columns.map((c) => {
                    const v = val(c, r);
                    const body = c.render ? c.render(r) : c.num && typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: c.digits ?? 0 }) : (v ?? "—");
                    return <td key={c.key} className={`${c.num ? "num" : ""} ${c.mono || c.num ? "mono" : ""}`}>{body}</td>;
                  })}
                </tr>
              );
            })}
            {padBottom > 0 && <tr><td style={{ height: padBottom, padding: 0, border: 0 }} colSpan={columns.length + 1} /></tr>}
            {!shown.length && <tr><td colSpan={columns.length + 1} className="dim" style={{ textAlign: "center", padding: 18 }}>{empty}</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="table-foot"><span>{shown.length.toLocaleString()} rows{rows.length !== shown.length ? ` (of ${rows.length.toLocaleString()})` : ""}</span>{selected && <span>{[...selected].filter((k) => keys.includes(k)).length} selected</span>}</div>
    </div>
  );
}
