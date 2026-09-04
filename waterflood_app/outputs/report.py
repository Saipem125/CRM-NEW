"""Reports — architecture §14 "Report (PDF/DOCX)", §18 "exports watermarked with run ID".

``build_context`` assembles everything a report needs from stored objects (registry row, result
bundle, optional recommendation record and audit history) — never from in-memory engine objects,
so a report can be produced for any past run. ``render_html`` fills the Jinja2 template with the
figures as inline SVG; ``html_to_pdf`` uses WeasyPrint when it can be imported (Linux images) or
a headless Chromium/Edge print (Windows workstations without the GTK stack); ``render_docx``
mirrors the same sections with PNG figures through python-docx.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from waterflood_app.config import Config
from waterflood_app.outputs import figures as F

TEMPLATES = Path(__file__).with_name("templates")
APP_VERSION = "0.5.0"


class ReportError(Exception):
    """A report could not be rendered in the requested format (message is user-facing)."""


@dataclass
class SectorFigures:
    key: str
    sector_id: str
    well_map: F.Fig
    matrix: F.Fig
    fan: F.Fig
    leaderboard: F.Fig
    efficiency: F.Fig
    fits: F.Fig
    dt_tau: F.Fig
    tornado: F.Fig | None
    outcome: F.Fig | None


# ---- context ----------------------------------------------------------------------------------
def _fmt(v: Any, digits: int = 0) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if digits == 0:
        return f"{x:,.0f}"
    return f"{x:,.{digits}f}"


def _pct(v: Any) -> str:
    return "—" if v is None else f"{float(v):+.1f} %"


def sector_figures(s: dict[str, Any], well_types: dict[str, str], units: dict[str, str]) -> SectorFigures:
    rec = s.get("recommendation") or {}
    tor = rec.get("tornado") or {}
    items = tor.get("items") or []
    unit = str(tor.get("unit") or units.get("volume", "bbl"))
    return SectorFigures(
        key=str(s["key"]),
        sector_id=str(s["id"]),
        well_map=F.well_map(s, well_types),
        matrix=F.connectivity_matrix(s),
        fan=F.forecast_fan(s, units),
        leaderboard=F.leaderboard(s),
        efficiency=F.injector_efficiency(s, units),
        fits=F.history_match_grid(s, units),
        dt_tau=F.dt_tau(s),
        tornado=F.tornado(items, float(tor.get("base", 0.0)), unit, "Sensitivity of the gain over hold-current")
        if items
        else None,
        outcome=F.outcome_range(rec, units.get("volume", "bbl")) if rec else None,
    )


def build_context(
    run_row: dict[str, Any],
    bundle: dict[str, Any],
    project: dict[str, Any],
    recommendation: dict[str, Any] | None = None,
    audit: list[dict[str, Any]] | None = None,
    generated_by: str = "",
) -> dict[str, Any]:
    """Everything the templates need. Display units are already applied in the bundle / record."""
    units = dict(bundle.get("units") or {"rate": "bbl/d", "volume": "bbl", "pressure": "psi"})
    types = {w: t["type"] for w, t in (bundle.get("well_types") or {}).items()}
    latest = int(bundle.get("latest_window_index", 0))
    sectors = [s for s in bundle.get("sectors", []) if int(s.get("window_index", 0)) == latest]
    earlier = [s for s in bundle.get("sectors", []) if int(s.get("window_index", 0)) != latest]
    if recommendation is not None:
        sid = recommendation.get("sector")
        chosen = [s for s in sectors if s["id"] == sid]
        sectors = chosen or sectors
        # the recommendation record is the authoritative plan for its sector
        for s in sectors:
            if s["id"] == sid and recommendation.get("recommendation"):
                s = dict(s)
                s["recommendation"] = recommendation["recommendation"]
    figs = {s["id"]: sector_figures(s, types, units) for s in sectors}
    dq = bundle.get("data_quality") or {}
    coverage = dq.get("coverage") or {}
    wells = sorted(
        (
            {
                "well": w,
                "type": types.get(w, ""),
                "coverage_pct": round(100.0 * float(c), 0),
                "pressure": (bundle.get("pressure") or {}).get("per_producer", {}).get(w, ""),
                "conversions": ", ".join(
                    f"{c_['date']} {c_['from']}→{c_['to']}"
                    for c_ in (bundle.get("well_types") or {}).get(w, {}).get("conversions", [])
                ),
            }
            for w, c in coverage.items()
        ),
        key=lambda r: (r["type"], r["well"]),
    )
    conds = [c for c in bundle.get("conditions", []) if c.get("severity") != "info"] + [
        c for c in bundle.get("conditions", []) if c.get("severity") == "info"
    ]
    summary = run_row.get("summary") or {}
    req = bundle.get("request") or summary.get("request") or {}
    ctx: dict[str, Any] = {
        "app_version": APP_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "generated_by": generated_by,
        "project": project,
        "run": run_row,
        "run_id": run_row.get("id", ""),
        "watermark": f"run {run_row.get('id', '')} · data {str(bundle.get('data_hash', ''))[:12]} · "
        f"config {str(bundle.get('config_hash', ''))[:12]} · code {run_row.get('code_version', '')}",
        "units": units,
        "confidence": bundle.get("confidence", "LOW"),
        "request": req,
        "objective": {
            "oil": "maximum cumulative oil",
            "npv": "maximum NPV",
            "min_water": "minimum water for target oil",
        }.get(str(req.get("objective", "oil")), str(req.get("objective", "oil"))),
        "sectors": sectors,
        "earlier_windows": earlier,
        "windows": bundle.get("windows", []),
        "figures": figs,
        "data_quality": dq,
        "wells": wells,
        "conditions": conds,
        "pressure": bundle.get("pressure") or {},
        "recommendation": recommendation,
        "audit": audit or [],
        "fmt": _fmt,
        "pct": _pct,
        "badge_colour": F.BADGE,
        "seed": bundle.get("seed"),
        "runtime_s": bundle.get("runtime_s"),
    }
    return ctx


# ---- HTML -------------------------------------------------------------------------------------
def render_html(ctx: dict[str, Any]) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from markupsafe import Markup

    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]))
    env.filters["svg"] = lambda fig: Markup(fig.to_svg()) if fig is not None else ""
    css = (TEMPLATES / "report.css").read_text(encoding="utf-8")
    return env.get_template("report.html.j2").render(css=css, **ctx)


# ---- PDF --------------------------------------------------------------------------------------
_CHROMIUM_CANDIDATES = [
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
    "msedge",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_chromium(cfg: Config | None = None) -> str | None:
    explicit = os.environ.get("WFO_CHROMIUM") or (cfg.get("integration.chromium_path") if cfg else None)
    if explicit:
        return str(explicit) if Path(str(explicit)).exists() or shutil.which(str(explicit)) else None
    for c in _CHROMIUM_CANDIDATES:
        if Path(c).exists():
            return c
        w = shutil.which(c)
        if w:
            return w
    return None


_WEASY: bool | None = None


def _weasyprint_available() -> bool:
    """Import probe, cached; WeasyPrint prints a long GTK hint on stderr when its libraries are missing."""
    global _WEASY
    if _WEASY is None:
        import contextlib

        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            try:
                import weasyprint  # noqa: F401

                _WEASY = True
            except Exception:
                _WEASY = False
    return _WEASY


def pdf_renderer(cfg: Config | None = None) -> str | None:
    """Which PDF back-end this host can use: 'weasyprint' | 'chromium' | None."""
    mode = str((cfg.get("integration.report_pdf_renderer") if cfg else None) or "auto")
    if mode == "none":
        return None
    if mode in ("auto", "weasyprint") and _weasyprint_available():
        return "weasyprint"
    if mode in ("auto", "chromium") and find_chromium(cfg):
        return "chromium"
    return None


def html_to_pdf(html: str, cfg: Config | None = None, timeout_s: float = 180.0) -> bytes:
    r = pdf_renderer(cfg)
    if r == "weasyprint":
        from weasyprint import HTML

        return bytes(HTML(string=html, base_url=str(TEMPLATES)).write_pdf())
    if r == "chromium":
        exe = find_chromium(cfg)
        assert exe is not None
        # A per-call temp dir; the browser's child process may still hold profile files for a moment
        # after the PDF is written, so cleanup is best-effort (leftovers live under the OS temp dir).
        td = tempfile.mkdtemp(prefix="wfo_report_")
        try:
            src = Path(td) / "report.html"
            out = Path(td) / "report.pdf"
            src.write_text(html, encoding="utf-8")
            # A dedicated profile dir keeps the print job from being handed to a running browser instance.
            cmd = [
                exe,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-first-run",
                "--disable-extensions",
                "--no-pdf-header-footer",
                f"--user-data-dir={Path(td) / 'profile'}",
                f"--print-to-pdf={out}",
                src.as_uri(),
            ]
            try:
                subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=False)
            except subprocess.TimeoutExpired as exc:  # pragma: no cover
                raise ReportError("The PDF renderer timed out.") from exc
            # On Windows the launcher returns at once and the PDF is written by a child process:
            # wait until the file exists and its size is stable.
            t0, last = time.monotonic(), -1
            while time.monotonic() - t0 < timeout_s:
                if out.exists():
                    size = out.stat().st_size
                    if size > 0 and size == last:
                        return out.read_bytes()
                    last = size
                time.sleep(0.25)
            raise ReportError("The PDF renderer did not produce a file.")
        finally:
            shutil.rmtree(td, ignore_errors=True)
    raise ReportError(
        "No PDF renderer is available on this server (WeasyPrint or a Chromium/Edge browser). "
        "Download the DOCX or HTML report instead, or ask the app owner to install one."
    )


# ---- DOCX -------------------------------------------------------------------------------------
def render_docx(ctx: dict[str, Any]) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10)
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Inches(0.8)
    sec.top_margin = sec.bottom_margin = Inches(0.7)
    footer = sec.footer.paragraphs[0]
    footer.text = f"Waterflood Optimizer {ctx['app_version']} · {ctx['watermark']} · generated {ctx['generated_at']}"
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in footer.runs:
        r.font.size = Pt(7)
        r.font.color.rgb = RGBColor(0x5E, 0x6C, 0x82)

    fmt, pct = ctx["fmt"], ctx["pct"]
    units = ctx["units"]
    proj = ctx["project"]
    rec = ctx.get("recommendation")

    def table(headers: list[str], rows: list[list[Any]], widths: list[float] | None = None) -> None:
        t = doc.add_table(rows=1, cols=len(headers))
        t.style = "Light Grid Accent 1"
        for k, h in enumerate(headers):
            cell = t.rows[0].cells[k]
            cell.text = str(h)
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.bold = True
                    r.font.size = Pt(8.5)
        for row in rows:
            cells = t.add_row().cells
            for k, v in enumerate(row):
                cells[k].text = "" if v is None else str(v)
                for p in cells[k].paragraphs:
                    for r in p.runs:
                        r.font.size = Pt(8.5)
        if widths:
            for row_ in t.rows:
                for k, w in enumerate(widths):
                    row_.cells[k].width = Inches(w)
        doc.add_paragraph()

    def picture(fig: F.Fig | None, width_in: float = 6.4) -> None:
        if fig is None:
            return
        doc.add_picture(io.BytesIO(fig.to_png(2.0)), width=Inches(width_in))

    title = "Waterflood injection recommendation" if rec else "Waterflood evaluation run"
    h = doc.add_heading(title, 0)
    h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    doc.add_paragraph(
        f"{proj.get('name', proj.get('id', ''))} · asset {proj.get('asset', '')} · "
        f"unit system {proj.get('unit_system', '')}"
    )
    p = doc.add_paragraph()
    r = p.add_run(f"Confidence: {ctx['confidence']}")
    r.font.bold = True
    r.font.color.rgb = RGBColor.from_string(F.BADGE.get(ctx["confidence"], F.DIM).lstrip("#"))
    doc.add_paragraph(
        f"Objective: {ctx['objective']} · posture: {ctx['request'].get('posture') or 'balanced'} · "
        f"horizon: {ctx['request'].get('horizon_months') or 24} months · generated {ctx['generated_at']}"
        + (f" by {ctx['generated_by']}" if ctx["generated_by"] else "")
    )

    for s in ctx["sectors"]:
        figs: SectorFigures = ctx["figures"][s["id"]]
        r_ = s.get("recommendation") or {}
        opt = r_.get("optimization") or {}
        m_ = s.get("model") or {}
        doc.add_heading(f"Sector {s['id']} — summary", 1)
        table(
            ["item", "value"],
            [
                ["confidence", f"{s.get('confidence')} — " + "; ".join(s.get("confidence_reasons") or [])],
                ["method", (s.get("model") or {}).get("label", "—")],
                [
                    "blind-test R² / MAPE",
                    f"{fmt(m_.get('blind_r2'), 3)} / {fmt(m_.get('blind_mape'), 1)} %",
                ],
                ["gain vs hold-current", pct(opt.get("gain_vs_base_pct"))],
                ["gain vs equal split", pct(opt.get("gain_vs_equal_split_pct"))],
                [
                    "plan value (P10 / P50 / P90)",
                    " / ".join(fmt(v) for v in r_.get("cum_oil_plan_p10_p50_p90", [])) + f" {units.get('volume', '')}",
                ],
                [
                    "hold-current (P10 / P50 / P90)",
                    " / ".join(fmt(v) for v in r_.get("cum_oil_base_p10_p50_p90", [])) + f" {units.get('volume', '')}",
                ],
                ["changes this week", str(len(r_.get("actions") or []))],
                [
                    "posture",
                    f"{r_.get('posture', '—')} {('— ' + r_['posture_note']) if r_.get('posture_note') else ''}",
                ],
            ],
            [2.2, 4.2],
        )
        if r_.get("actions"):
            doc.add_heading("Change list", 2)
            table(
                [
                    "well",
                    f"from [{units['rate']}]",
                    f"to [{units['rate']}]",
                    "step this week",
                    "target date",
                    "setting hint",
                    f"expected oil [{units['volume']}]",
                    "revert if",
                ],
                [
                    [
                        a["well"],
                        fmt(a["rate_from"]),
                        fmt(a["rate_to"]),
                        fmt(a["step_this_week"]),
                        a["target_date"],
                        a["setting_hint"],
                        fmt(a["expected_oil_gain"]),
                        a["revert_if"],
                    ]
                    for a in r_["actions"]
                ],
            )
        picture(figs.outcome)
        picture(figs.tornado)
        doc.add_heading("Connectivity", 2)
        picture(figs.well_map)
        picture(figs.matrix)
        doc.add_heading("Forecast and injector efficiency", 2)
        picture(figs.fan)
        picture(figs.efficiency)
        doc.add_heading("Which method won", 2)
        picture(figs.leaderboard)
        table(
            ["gate", "result"],
            [[g, "pass" if ok else "fail / not applicable"] for g, ok in (s.get("gates") or {}).items()],
            [3.0, 3.0],
        )
        doc.add_heading("History match per well", 2)
        picture(figs.fits)
        doc.add_heading("Sampling and pressure", 2)
        picture(figs.dt_tau)
        d = s.get("dt_tau") or {}
        doc.add_paragraph(
            f"Δt = {fmt(d.get('dt_days'))} d · τ estimate = {fmt(d.get('tau_estimate_days'))} d · "
            f"τ/Δt = {fmt(d.get('tau_over_dt'), 1)} · "
            f"O_d = {fmt(d.get('od'), 1)} · pressure source: {ctx['pressure'].get('field_source', '—')}"
        )

    doc.add_heading("Data quality", 1)
    dq = ctx["data_quality"]
    doc.add_paragraph(
        f"Traffic light: {dq.get('traffic_light', '—')} · steps: {dq.get('n_steps')} · "
        f"outliers removed: {dq.get('outliers_removed')} · "
        f"simultaneous P+I steps: {dq.get('simultaneous_pi_steps')} · unmatched IDs: {dq.get('unmatched_ids')} · "
        f"warnings: {dq.get('n_warnings')} · errors: {dq.get('n_errors')}"
    )
    table(
        ["well", "type", "coverage %", "pressure", "conversions"],
        [[w["well"], w["type"], fmt(w["coverage_pct"]), w["pressure"], w["conversions"]] for w in ctx["wells"]],
    )
    doc.add_heading("Conditions (plain language)", 1)
    table(
        ["severity", "what it means", "suggested action", "scope"],
        [[c.get("severity"), c.get("message"), c.get("action"), c.get("scope")] for c in ctx["conditions"]]
        or [["—", "No conditions raised.", "", ""]],
        [0.8, 2.8, 2.0, 0.9],
    )
    if rec:
        doc.add_heading("Workflow", 1)
        rows = [
            ["recommendation", rec.get("id")],
            ["state", rec.get("state")],
            ["originator", rec.get("originator")],
            [
                "approved by originator",
                "YES — same person ran and approved" if rec.get("approved_by_originator") else "no",
            ],
            ["override reason", rec.get("override_reason") or "—"],
            ["advanced overrides", str(rec.get("advanced_overrides") or "none")],
            ["snapshot digest", (rec.get("snapshot") or {}).get("digest", "—")],
            ["implemented at", rec.get("implemented_at") or "—"],
        ]
        table(["item", "value"], rows, [2.2, 4.2])
        table(
            ["when", "action", "actor", "acting role", "from → to", "note"],
            [
                [
                    h["at"][:16].replace("T", " "),
                    h["action"],
                    h["actor"],
                    h["acting_role"],
                    f"{h['from_state']} → {h['to_state']}",
                    h.get("note", ""),
                ]
                for h in rec.get("history", [])
            ],
        )
        if rec.get("recommended_rates"):
            table(
                ["injector", f"recommended [{units['rate']}]", f"implemented [{units['rate']}]"],
                [
                    [w, fmt(v), fmt((rec.get("implemented_rates") or {}).get(w))]
                    for w, v in rec["recommended_rates"].items()
                ],
            )
        if rec.get("evaluations"):
            table(
                ["months", "date", "P10", "P50", "P90", "realised", "outcome"],
                [
                    [
                        e.get("months_after"),
                        e.get("evaluated_on"),
                        fmt(e.get("forecast_p10")),
                        fmt(e.get("forecast_p50")),
                        fmt(e.get("forecast_p90")),
                        fmt(e.get("realised")),
                        e.get("outcome"),
                    ]
                    for e in rec["evaluations"]
                ],
            )
    doc.add_heading("Reproducibility", 1)
    run = ctx["run"]
    table(
        ["item", "value"],
        [
            ["run id", run.get("id")],
            ["data snapshot hash", run.get("data_hash")],
            ["config hash", run.get("config_hash")],
            ["code version", run.get("code_version")],
            ["seed", str(ctx.get("seed"))],
            ["engine runtime", f"{fmt(ctx.get('runtime_s'), 1)} s"],
            ["windows", "; ".join(f"{w['start']} → {w['end']} ({w['reason']})" for w in ctx["windows"])],
            ["application", f"Waterflood Optimizer {ctx['app_version']}"],
        ],
        [2.2, 4.2],
    )
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
