"""Report figures (architecture §19 L5 ``outputs/plots`` + ``outputs/maps``; prompt §4.2–§4.3).

The build machine has no matplotlib and the report must render identically in the PDF (HTML +
SVG) and in the DOCX (PNG). Every figure is therefore built as a tiny scene graph of primitives
(rect, line, polyline, polygon, circle, text) that serialises to SVG and rasterises with Pillow.
Figures take the *result bundle* dictionaries (display units, the same data the UI draws), so a
report can be produced for any stored run without the in-memory engine objects.

Colours follow the design tokens for the semantic roles (injection blue, production orange,
mixed violet, HIGH/MEDIUM/LOW = good/oil/danger) on a paper-white ground for print.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

# ---- palette (print) --------------------------------------------------------------------------
WATER = "#4C9BD4"
OIL = "#E0973F"
ACCENT = "#5CC2AE"
DANGER = "#D2685A"
GOOD = "#7DB88A"
VIOLET = "#9F8FD0"
TEXT = "#0B121C"
DIM = "#5E6C82"
FAINT = "#93A0B4"
GRID = "#D5DBE4"
PANEL = "#F3F5F8"
PAPER = "#FFFFFF"
BADGE = {"HIGH": GOOD, "MEDIUM": OIL, "LOW": DANGER}
METHOD_COLOUR = {
    "crmt": FAINT,
    "crmp": WATER,
    "crmip": VIOLET,
    "aquifer": ACCENT,
    "twophase": OIL,
    "crossflow": "#C4A35A",
    "mpi": "#7A8FA6",
    "koval": "#B07AA1",
}

FONT_BODY = "IBM Plex Sans, Arial, Helvetica, sans-serif"
FONT_MONO = "IBM Plex Mono, Consolas, monospace"


def _hex(c: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), round(255 * alpha)


@dataclass
class Fig:
    """Scene graph with SVG and PNG back-ends."""

    width: int
    height: int
    ops: list[dict[str, Any]] = field(default_factory=list)
    title: str = ""

    # ---- primitives ---------------------------------------------------------------------------
    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str | None = PANEL,
        stroke: str | None = None,
        sw: float = 1.0,
        opacity: float = 1.0,
        rx: float = 0.0,
    ) -> None:
        self.ops.append(
            {
                "t": "rect",
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "fill": fill,
                "stroke": stroke,
                "sw": sw,
                "o": opacity,
                "rx": rx,
            }
        )

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke: str = GRID,
        sw: float = 1.0,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        self.ops.append(
            {"t": "line", "p": [(x1, y1), (x2, y2)], "stroke": stroke, "sw": sw, "dash": dash, "o": opacity}
        )

    def polyline(
        self,
        pts: list[tuple[float, float]],
        stroke: str = TEXT,
        sw: float = 1.5,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        if len(pts) >= 2:
            self.ops.append({"t": "line", "p": pts, "stroke": stroke, "sw": sw, "dash": dash, "o": opacity})

    def polygon(
        self,
        pts: list[tuple[float, float]],
        fill: str | None = ACCENT,
        stroke: str | None = None,
        sw: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        if len(pts) >= 3:
            self.ops.append({"t": "poly", "p": pts, "fill": fill, "stroke": stroke, "sw": sw, "o": opacity})

    def circle(
        self,
        cx: float,
        cy: float,
        r: float,
        fill: str | None = OIL,
        stroke: str | None = None,
        sw: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        self.ops.append(
            {"t": "circle", "cx": cx, "cy": cy, "r": r, "fill": fill, "stroke": stroke, "sw": sw, "o": opacity}
        )

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 11,
        fill: str = TEXT,
        anchor: str = "start",
        weight: str = "normal",
        mono: bool = False,
        rotate: float = 0.0,
        opacity: float = 1.0,
    ) -> None:
        self.ops.append(
            {
                "t": "text",
                "x": x,
                "y": y,
                "s": s,
                "size": size,
                "fill": fill,
                "anchor": anchor,
                "w": weight,
                "mono": mono,
                "rot": rotate,
                "o": opacity,
            }
        )

    # ---- SVG ----------------------------------------------------------------------------------
    def to_svg(self) -> str:
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}" role="img" aria-label="{_esc(self.title)}">'
        ]
        out.append(f'<rect x="0" y="0" width="{self.width}" height="{self.height}" fill="{PAPER}"/>')
        for o in self.ops:
            op = f' opacity="{o["o"]:.3f}"' if o.get("o", 1.0) < 1.0 else ""
            if o["t"] == "rect":
                out.append(
                    f'<rect x="{o["x"]:.2f}" y="{o["y"]:.2f}" width="{o["w"]:.2f}" height="{o["h"]:.2f}" '
                    f'rx="{o["rx"]}" '
                    f'fill="{o["fill"] or "none"}" stroke="{o["stroke"] or "none"}" stroke-width="{o["sw"]}"{op}/>'
                )
            elif o["t"] == "line":
                pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in o["p"])
                dash = f' stroke-dasharray="{o["dash"]}"' if o.get("dash") else ""
                out.append(
                    f'<polyline points="{pts}" fill="none" stroke="{o["stroke"]}" stroke-width="{o["sw"]}" '
                    f'stroke-linejoin="round" stroke-linecap="round"{dash}{op}/>'
                )
            elif o["t"] == "poly":
                pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in o["p"])
                out.append(
                    f'<polygon points="{pts}" fill="{o["fill"] or "none"}" stroke="{o["stroke"] or "none"}" '
                    f'stroke-width="{o["sw"]}"{op}/>'
                )
            elif o["t"] == "circle":
                out.append(
                    f'<circle cx="{o["cx"]:.2f}" cy="{o["cy"]:.2f}" r="{o["r"]:.2f}" fill="{o["fill"] or "none"}" '
                    f'stroke="{o["stroke"] or "none"}" stroke-width="{o["sw"]}"{op}/>'
                )
            elif o["t"] == "text":
                fam = FONT_MONO if o["mono"] else FONT_BODY
                tr = f' transform="rotate({o["rot"]} {o["x"]:.2f} {o["y"]:.2f})"' if o["rot"] else ""
                out.append(
                    f'<text x="{o["x"]:.2f}" y="{o["y"]:.2f}" font-family="{fam}" font-size="{o["size"]}" '
                    f'font-weight="{o["w"]}" fill="{o["fill"]}" text-anchor="{o["anchor"]}"{tr}{op}>'
                    f"{_esc(o['s'])}</text>"
                )
        out.append("</svg>")
        return "\n".join(out)

    # ---- PNG (Pillow) -------------------------------------------------------------------------
    def to_png(self, scale: float = 2.0) -> bytes:
        from PIL import Image, ImageDraw

        W, H = int(self.width * scale), int(self.height * scale)
        img = Image.new("RGBA", (W, H), _hex(PAPER))
        base = ImageDraw.Draw(img, "RGBA")

        def sc(v: float) -> float:
            return v * scale

        for o in self.ops:
            a = float(o.get("o", 1.0))
            layer = img if a >= 1.0 else Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = base if a >= 1.0 else ImageDraw.Draw(layer, "RGBA")
            if o["t"] == "rect":
                fill = _hex(o["fill"]) if o["fill"] else None
                outline = _hex(o["stroke"]) if o["stroke"] else None
                box = (sc(o["x"]), sc(o["y"]), sc(o["x"] + o["w"]), sc(o["y"] + o["h"]))
                if o["rx"]:
                    d.rounded_rectangle(
                        box, radius=sc(o["rx"]), fill=fill, outline=outline, width=max(1, int(sc(o["sw"])))
                    )
                else:
                    d.rectangle(box, fill=fill, outline=outline, width=max(1, int(sc(o["sw"]))) if outline else 0)
            elif o["t"] == "line":
                pts = [(sc(x), sc(y)) for x, y in o["p"]]
                w = max(1, round(sc(o["sw"])))
                if o.get("dash"):
                    _dashed(d, pts, _hex(o["stroke"]), w, sc(4.0))
                else:
                    d.line(pts, fill=_hex(o["stroke"]), width=w, joint="curve")
            elif o["t"] == "poly":
                pts = [(sc(x), sc(y)) for x, y in o["p"]]
                d.polygon(
                    pts, fill=_hex(o["fill"]) if o["fill"] else None, outline=_hex(o["stroke"]) if o["stroke"] else None
                )
            elif o["t"] == "circle":
                r = sc(o["r"])
                box = (sc(o["cx"]) - r, sc(o["cy"]) - r, sc(o["cx"]) + r, sc(o["cy"]) + r)
                d.ellipse(
                    box,
                    fill=_hex(o["fill"]) if o["fill"] else None,
                    outline=_hex(o["stroke"]) if o["stroke"] else None,
                    width=max(1, int(sc(o["sw"]))),
                )
            elif o["t"] == "text":
                fnt = _font(o["size"] * scale, o["mono"], o["w"] == "bold")
                anchor = {"start": "ls", "middle": "ms", "end": "rs"}[o["anchor"]]
                if o["rot"]:
                    tw, th = _text_size(fnt, o["s"])
                    tmp = Image.new("RGBA", (int(tw + 4), int(th + 4)), (0, 0, 0, 0))
                    ImageDraw.Draw(tmp).text((2, 2), o["s"], font=fnt, fill=_hex(o["fill"]))
                    tmp = tmp.rotate(-o["rot"], expand=True)
                    # anchor middle of the rotated text on (x, y)
                    px, py = sc(o["x"]) - tmp.width / 2, sc(o["y"]) - tmp.height / 2
                    layer.alpha_composite(tmp, (int(px), int(py)))
                else:
                    d.text((sc(o["x"]), sc(o["y"])), o["s"], font=fnt, fill=_hex(o["fill"]), anchor=anchor)
            if a < 1.0:
                alpha = layer.split()[3].point(lambda v, a=a: int(v * a))
                layer.putalpha(alpha)
                img.alpha_composite(layer)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _dashed(d: Any, pts: list[tuple[float, float]], fill: Any, width: int, dash: float) -> None:
    for (x1, y1), (x2, y2) in pairwise(pts):
        L = math.hypot(x2 - x1, y2 - y1)
        if L == 0:
            continue
        n = int(L / dash)
        for k in range(0, n + 1, 2):
            t0, t1 = k * dash / L, min((k + 1) * dash / L, 1.0)
            d.line(
                [(x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0), (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)],
                fill=fill,
                width=width,
            )


_FONT_CACHE: dict[tuple[int, bool, bool], Any] = {}
_FONT_CANDIDATES = {
    (False, False): [
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "Arial.ttf",
    ],
    (False, True): [
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "Arial Bold.ttf",
    ],
    (True, False): [
        "DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "Menlo.ttc",
    ],
    (True, True): [
        "DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "C:/Windows/Fonts/consolab.ttf",
        "Menlo.ttc",
    ],
}


def _font(size: float, mono: bool, bold: bool) -> Any:
    from PIL import ImageFont

    key = (round(size), mono, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    f: Any = None
    for cand in _FONT_CANDIDATES[(mono, bold)]:
        try:
            f = ImageFont.truetype(cand, key[0])
            break
        except OSError:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size=key[0])
        except TypeError:  # pragma: no cover — very old Pillow
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _text_size(fnt: Any, s: str) -> tuple[float, float]:
    box = fnt.getbbox(s)
    return box[2] - box[0], box[3] - box[1]


# ---- axes helper ------------------------------------------------------------------------------
def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return [0.0]
    if hi <= lo:
        hi = lo + 1.0
    raw = (hi - lo) / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=10) * mag
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 1e-9:
        if v >= lo - step * 1e-9:
            ticks.append(round(v, 10))
        v += step
    return ticks or [lo]


def fmt_num(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    if a >= 100:
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{v:.2f}"


@dataclass
class Axes:
    """Cartesian axes inside a figure: data → pixel mapping with gridlines and titled axes."""

    fig: Fig
    x0: float
    y0: float
    w: float
    h: float
    xlim: tuple[float, float]
    ylim: tuple[float, float]

    def px(self, x: float) -> float:
        a, b = self.xlim
        return self.x0 + (x - a) / (b - a or 1.0) * self.w

    def py(self, y: float) -> float:
        a, b = self.ylim
        return self.y0 + self.h - (y - a) / (b - a or 1.0) * self.h

    def frame(
        self,
        xlabel: str = "",
        ylabel: str = "",
        xticks: list[tuple[float, str]] | None = None,
        yticks: list[float] | None = None,
        title: str = "",
    ) -> None:
        f = self.fig
        f.rect(self.x0, self.y0, self.w, self.h, fill=PAPER, stroke=GRID)
        for v in yticks if yticks is not None else nice_ticks(*self.ylim):
            y = self.py(v)
            if self.y0 - 0.5 <= y <= self.y0 + self.h + 0.5:
                f.line(self.x0, y, self.x0 + self.w, y, GRID, 0.8)
                f.text(self.x0 - 6, y + 3.5, fmt_num(v), 9, DIM, "end", mono=True)
        for v, lab in xticks or [(t, fmt_num(t)) for t in nice_ticks(*self.xlim)]:
            x = self.px(v)
            if self.x0 - 0.5 <= x <= self.x0 + self.w + 0.5:
                f.line(x, self.y0, x, self.y0 + self.h, GRID, 0.8)
                f.text(x, self.y0 + self.h + 14, lab, 9, DIM, "middle", mono=True)
        if xlabel:
            f.text(self.x0 + self.w / 2, self.y0 + self.h + 30, xlabel, 10, DIM, "middle", mono=True)
        if ylabel:
            f.text(self.x0 - 44, self.y0 + self.h / 2, ylabel, 10, DIM, "middle", mono=True, rotate=-90)
        if title:
            f.text(self.x0, self.y0 - 8, title, 12, TEXT, "start", "bold")


def _date_ticks(dates: list[str], n: int = 6) -> list[tuple[float, str]]:
    if not dates:
        return []
    step = max(1, len(dates) // n)
    return [(float(i), dates[i][:7]) for i in range(0, len(dates), step)]


def _lim(vals: list[float], pad: float = 0.08, zero: bool = True) -> tuple[float, float]:
    v = [x for x in vals if x is not None and math.isfinite(x)]
    if not v:
        return (0.0, 1.0)
    lo, hi = min(v), max(v)
    if zero:
        lo = min(lo, 0.0)
    span = (hi - lo) or abs(hi) or 1.0
    return (lo - (0 if zero and lo == 0 else pad * span), hi + pad * span)


def _legend(f: Fig, x: float, y: float, items: list[tuple[str, str, str]]) -> None:
    """items: (label, colour, kind) with kind in line|band|dot|tri|dash."""
    cx = x
    for label, colour, kind in items:
        if kind == "band":
            f.rect(cx, y - 8, 16, 9, fill=colour, opacity=0.35)
        elif kind == "line":
            f.line(cx, y - 4, cx + 16, y - 4, colour, 2)
        elif kind == "dash":
            f.line(cx, y - 4, cx + 16, y - 4, colour, 2, dash="4,3")
        elif kind == "dot":
            f.circle(cx + 8, y - 4, 4, fill=colour)
        elif kind == "tri":
            f.polygon([(cx + 3, y - 9), (cx + 13, y - 9), (cx + 8, y + 1)], fill=colour)
        f.text(cx + 21, y, label, 9.5, DIM)
        cx += 21 + 6.2 * len(label) + 14


# ---- figures ----------------------------------------------------------------------------------
def well_map(
    s: dict[str, Any], well_types: dict[str, str], f_min: float = 0.02, width: int = 620, height: int = 460
) -> Fig:
    """Well map with f_ij arrows (width ∝ f_ij, opacity ∝ pair confidence), §4.3."""
    fig = Fig(width, height, title="Well map with connectivity")
    inj = [w for w in s["injectors"] if w.get("xy")]
    prod = [w for w in s["producers"] if w.get("xy")]
    m = s.get("model") or {}
    F = m.get("f_ij") or []
    C = m.get("pair_confidence") or []
    if not inj or not prod:
        fig.text(width / 2, height / 2, "No coordinates available — map not drawn.", 12, DIM, "middle")
        return fig
    xs = [w["xy"][0] for w in inj + prod]
    ys = [w["xy"][1] for w in inj + prod]
    x0, y0, w_, h_ = 40, 30, width - 60, height - 90
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    sc = min(w_ / (max(xs) - min(xs) or span), h_ / (max(ys) - min(ys) or span)) * 0.88
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    def pt(xy: list[float]) -> tuple[float, float]:
        return x0 + w_ / 2 + (xy[0] - cx) * sc, y0 + h_ / 2 - (xy[1] - cy) * sc

    fig.rect(x0, y0, w_, h_, fill=PANEL, stroke=GRID)
    ii = {w["id"]: k for k, w in enumerate(s["injectors"])}
    jj = {w["id"]: k for k, w in enumerate(s["producers"])}
    for wi in inj:
        for wp in prod:
            i, j = ii[wi["id"]], jj[wp["id"]]
            fval = float(F[i][j]) if F and i < len(F) and j < len(F[i]) else 0.0
            if fval < f_min:
                continue
            conf = float(C[i][j]) if C and i < len(C) and j < len(C[i]) else 0.7
            (ax, ay), (bx, by) = pt(wi["xy"]), pt(wp["xy"])
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            ex, ey = bx - ux * 9, by - uy * 9
            fig.line(
                ax + ux * 8, ay + uy * 8, ex, ey, WATER, 0.6 + 7 * fval, opacity=0.25 + 0.75 * max(0.0, min(1.0, conf))
            )
            hx, hy = ex - ux * 7, ey - uy * 7
            fig.polygon(
                [(ex, ey), (hx - uy * 3.5, hy + ux * 3.5), (hx + uy * 3.5, hy - ux * 3.5)],
                fill=WATER,
                opacity=0.25 + 0.75 * conf,
            )
    for w in prod:
        x, y = pt(w["xy"])
        t = well_types.get(w["well"], "PROD")
        fig.circle(x, y, 6, fill=VIOLET if t == "MIXED" else OIL, stroke=PAPER, sw=1.2)
        fig.text(x + 8, y + 4, w["well"], 8.5, TEXT, mono=True)
    for w in inj:
        x, y = pt(w["xy"])
        t = well_types.get(w["well"], "INJ")
        fig.polygon(
            [(x - 6.5, y - 5.5), (x + 6.5, y - 5.5), (x, y + 6.5)],
            fill=VIOLET if t == "MIXED" else WATER,
            stroke=PAPER,
            sw=1.2,
        )
        fig.text(x + 8, y + 4, w["well"], 8.5, TEXT, mono=True)
    # scale bar and north arrow
    bar_m = float(nice_ticks(0, span / 4, 1)[-1] or 100)
    bx0, by0 = x0 + 12, y0 + h_ - 14
    fig.line(bx0, by0, bx0 + bar_m * sc, by0, TEXT, 2)
    fig.text(bx0, by0 - 5, f"{fmt_num(bar_m)} m", 9, DIM, mono=True)
    nx, ny = x0 + w_ - 18, y0 + 26
    fig.polygon([(nx, ny - 14), (nx - 5, ny + 4), (nx + 5, ny + 4)], fill=TEXT)
    fig.text(nx, ny + 15, "N", 9, DIM, "middle", "bold")
    _legend(
        fig,
        x0,
        height - 14,
        [
            ("injector", WATER, "tri"),
            ("producer", OIL, "dot"),
            ("mixed", VIOLET, "dot"),
            ("f_ij (width ∝ f, opacity ∝ confidence)", WATER, "line"),
        ],
    )
    return fig


def connectivity_matrix(s: dict[str, Any], width: int = 620) -> Fig:
    """Heat map of f_ij: rows injectors, columns producers; τ per producer in the footer."""
    m = s.get("model") or {}
    F = m.get("f_ij") or []
    inj = [w["well"] for w in s["injectors"]]
    prod = [w["well"] for w in s["producers"]]
    left, top = 70, 40
    cw = max(18, min(46, (width - left - 20) // max(len(prod), 1)))
    ch = max(14, min(24, cw))
    height = top + ch * len(inj) + 60
    fig = Fig(width, height, title="Connectivity matrix f_ij")
    fig.text(left, 18, "Connectivity f_ij (rows: injectors, columns: producers)", 12, TEXT, "start", "bold")
    for j, p in enumerate(prod):
        fig.text(left + j * cw + cw / 2, top - 6, p, 8, DIM, "middle", mono=True, rotate=-45 if cw < 34 else 0)
    for i, w in enumerate(inj):
        fig.text(left - 6, top + i * ch + ch / 2 + 3, w, 8.5, DIM, "end", mono=True)
        for j in range(len(prod)):
            v = float(F[i][j]) if F and i < len(F) and j < len(F[i]) else 0.0
            a = max(0.0, min(1.0, v / 0.6))
            fig.rect(left + j * cw, top + i * ch, cw - 1, ch - 1, fill=WATER, opacity=0.08 + 0.92 * a)
            if cw >= 30 and v >= 0.005:
                fig.text(
                    left + j * cw + cw / 2,
                    top + i * ch + ch / 2 + 3,
                    f"{v:.2f}",
                    7.5,
                    TEXT if a < 0.55 else PAPER,
                    "middle",
                    mono=True,
                )
    taus = m.get("tau_days") or []
    yb = top + ch * len(inj) + 14
    fig.text(left - 6, yb + 3, "τ [d]", 8.5, DIM, "end", mono=True)
    for j in range(len(prod)):
        if j < len(taus) and taus[j] is not None:
            fig.text(left + j * cw + cw / 2, yb + 3, fmt_num(float(taus[j])), 7.5, DIM, "middle", mono=True)
    sums = m.get("sum_f_per_injector") or []
    fig.text(
        left,
        height - 10,
        "Σ_j f_ij per injector: " + ", ".join(f"{w} {float(v):.2f}" for w, v in zip(inj, sums, strict=False)),
        8.5,
        DIM,
        mono=True,
    )
    return fig


def forecast_fan(s: dict[str, Any], units: dict[str, str], width: int = 620, height: int = 300) -> Fig:
    """Field oil rate: history (dim), blind window shaded, P10–P90 band (accent 18 %), P50, base dashed."""
    fig = Fig(width, height, title="Forecast fan")
    fc = s.get("forecast")
    dates = list(s.get("dates") or [])
    hist = [sum(float(p["oil"][t] or 0.0) for p in s["producers"]) for t in range(len(dates))] if dates else []
    if not fc or not hist:
        fig.text(width / 2, height / 2, "No forecast for this sector.", 12, DIM, "middle")
        return fig

    def _ser(xs: Any) -> list[float]:  # JSON nulls (NaN forecasts of a broken well) → NaN, drawn as gaps
        return [float("nan") if v is None else float(v) for v in xs]

    fp = {k: _ser(v) for k, v in fc["field_plan"].items()}
    fb = {k: _ser(v) for k, v in fc["field_base"].items()}
    hist = [float("nan") if v is None else float(v) for v in hist]
    # show the last five years of history so the forecast band stays readable
    keep = min(len(hist), max(60, 3 * len(fc["dates"])))
    hist, dates = hist[-keep:], dates[-keep:]
    b0_shift = len(s.get("dates") or []) - keep
    n_h, n_f = len(hist), len(fc["dates"])
    vals = hist + list(fp["p10"]) + list(fp["p90"]) + list(fb["p50"])
    ax = Axes(fig, 60, 30, width - 80, height - 90, (0.0, float(n_h + n_f - 1)), _lim(vals))
    ticks = _date_ticks(dates + list(fc["dates"]), 7)
    ax.frame(
        "month", f"field oil rate [{units.get('rate', 'bbl/d')}]", ticks, title="Field oil rate: history and forecast"
    )
    b0 = int(s.get("blind_start_index", n_h)) - b0_shift
    if 0 < b0 < n_h:
        fig.rect(ax.px(b0), ax.y0, ax.px(n_h - 1) - ax.px(b0), ax.h, fill=VIOLET, opacity=0.12)
        fig.text(ax.px(b0) + 3, ax.y0 + 12, "blind test", 8.5, DIM)
    fig.line(ax.px(n_h - 1), ax.y0, ax.px(n_h - 1), ax.y0 + ax.h, DIM, 1, dash="3,3")
    band = [(ax.px(n_h - 1 + k), ax.py(v)) for k, v in enumerate(fp["p90"]) if math.isfinite(v)] + [
        (ax.px(n_h - 1 + k), ax.py(v)) for k, v in reversed(list(enumerate(fp["p10"]))) if math.isfinite(v)
    ]
    fig.polygon(band, fill=ACCENT, opacity=0.18)
    fig.polyline([(ax.px(t), ax.py(v)) for t, v in enumerate(hist) if math.isfinite(v)], DIM, 1.5)
    fig.polyline([(ax.px(n_h - 1 + k), ax.py(v)) for k, v in enumerate(fp["p50"]) if math.isfinite(v)], ACCENT, 2)
    fig.polyline(
        [(ax.px(n_h - 1 + k), ax.py(v)) for k, v in enumerate(fb["p50"]) if math.isfinite(v)], OIL, 1.5, dash="5,4"
    )
    _legend(
        fig,
        ax.x0,
        height - 10,
        [
            ("history", DIM, "line"),
            ("plan P50", ACCENT, "line"),
            ("plan P10–P90", ACCENT, "band"),
            ("hold-current P50", OIL, "dash"),
        ],
    )
    return fig


def leaderboard(s: dict[str, Any], width: int = 620) -> Fig:
    """Horizontal score bars; excluded methods greyed with their reason."""
    rows = list(s.get("leaderboard") or [])
    rh = 26
    height = 50 + rh * max(len(rows), 1) + 20
    fig = Fig(width, height, title="Tournament leaderboard")
    fig.text(
        20,
        22,
        "Which method won — composite score (blind R², MAPE, AICc, stability, plausibility, runtime)",
        11.5,
        TEXT,
        "start",
        "bold",
    )
    left, bw = 190, width - 190 - 200
    scores = [float(r["score"]) for r in rows if r.get("score") is not None]
    smax = max(scores) if scores else 1.0
    for k, r in enumerate(rows):
        y = 44 + k * rh
        col = METHOD_COLOUR.get(str(r["variant"]), FAINT)
        fig.text(left - 8, y + 12, str(r.get("label", r["variant"])), 9.5, TEXT, "end")
        if r.get("score") is not None:
            w = bw * float(r["score"]) / (smax or 1.0)
            fig.rect(left, y + 2, max(w, 1), rh - 8, fill=col, rx=2)
            fig.text(
                left + w + 6,
                y + 13,
                f"{float(r['score']):.3f}  R² {_f2(r.get('blind_r2'))}  MAPE {_f1(r.get('blind_mape'))} %  "
                f"p {r.get('n_params', '')}",
                8.5,
                DIM,
                mono=True,
            )
        else:
            fig.rect(left, y + 2, bw * 0.15, rh - 8, fill=GRID, rx=2)
            fig.text(left + bw * 0.15 + 6, y + 13, f"excluded — {r.get('excluded', '')}", 8.5, FAINT)
    return fig


def _f2(v: Any) -> str:
    return "—" if v is None else f"{float(v):.3f}"


def _f1(v: Any) -> str:
    return "—" if v is None else f"{float(v):.1f}"


def injector_efficiency(s: dict[str, Any], units: dict[str, str], width: int = 620, height: int = 280) -> Fig:
    """Oil per unit water injected, sorted; current vs optimized side by side."""
    fig = Fig(width, height, title="Injector efficiency")
    fc = s.get("forecast") or {}
    eff = fc.get("injector_efficiency")
    if not eff:
        fig.text(width / 2, height / 2, "No injector-efficiency data (no plan for this sector).", 12, DIM, "middle")
        return fig
    order = sorted(range(len(eff["injectors"])), key=lambda k: -float(eff["oil_per_bbl"][k]))
    n = len(order)
    vol = units.get("volume", "bbl")
    vals = [float(eff["current"][k]) for k in order] + [float(eff["optimized"][k]) for k in order]
    ax = Axes(fig, 60, 30, width - 80, height - 90, (-0.5, n - 0.5), _lim([0.0, max(vals) if vals else 1.0]))
    ax.frame(
        "injector",
        f"rate [{units.get('rate', 'bbl/d')}]",
        [(float(i), eff["injectors"][k]) for i, k in enumerate(order)],
        title=f"Injector efficiency (oil per {vol} injected) — current vs optimized rate",
    )
    bw = ax.w / n * 0.36
    for i, k in enumerate(order):
        cxp = ax.px(float(i))
        c, o = float(eff["current"][k]), float(eff["optimized"][k])
        fig.rect(cxp - bw - 1, ax.py(c), bw, ax.py(0) - ax.py(c), fill=FAINT)
        fig.rect(cxp + 1, ax.py(o), bw, ax.py(0) - ax.py(o), fill=WATER)
        fig.text(cxp, ax.y0 + 12, f"{float(eff['oil_per_bbl'][k]):.3f}", 8, OIL, "middle", mono=True)
    _legend(
        fig,
        ax.x0,
        height - 10,
        [("current", FAINT, "band"), ("optimized", WATER, "band"), (f"oil per {vol} injected (label)", OIL, "line")],
    )
    return fig


def history_match_grid(s: dict[str, Any], units: dict[str, str], max_wells: int = 12, width: int = 620) -> Fig:
    """Small multiples: raw (faint points), cleaned (dim), model (oil), residual strip, blind shaded, R²/MAPE."""
    prods = list(s["producers"])[:max_wells]
    cols = 3 if len(prods) > 4 else 2 if len(prods) > 1 else 1
    rows = math.ceil(len(prods) / cols) if prods else 1
    pw, ph = (width - 20) / cols, 150
    height = int(rows * ph + 40)
    fig = Fig(width, height, title="History match per well")
    dates = list(s.get("dates") or [])
    b0 = int(s.get("blind_start_index", len(dates)))
    for k, p in enumerate(prods):
        r, c = divmod(k, cols)
        x0, y0 = 20 + c * pw + 42, 30 + r * ph
        w, h = pw - 60, ph - 62
        raw, clean, model = (
            [float(v) for v in p["raw_liquid"]],
            [float(v) for v in p["liquid"]],
            [float(v) for v in p["model"]],
        )
        ax = Axes(fig, x0, y0, w, h, (0.0, float(max(len(dates) - 1, 1))), _lim(raw + clean + model))
        ax.frame(
            "",
            "",
            _date_ticks(dates, 3),
            title=f"{p['well']}  R² {_f2(p.get('blind_r2'))}  MAPE {_f1(p.get('blind_mape'))} %",
        )
        if 0 < b0 < len(dates):
            fig.rect(ax.px(b0), ax.y0, ax.px(len(dates) - 1) - ax.px(b0), ax.h, fill=VIOLET, opacity=0.12)
        for t, v in enumerate(raw):
            if math.isfinite(v):
                fig.circle(ax.px(t), ax.py(v), 1.3, fill=FAINT, opacity=0.7)
        fig.polyline([(ax.px(t), ax.py(v)) for t, v in enumerate(clean) if math.isfinite(v)], DIM, 1.2)
        fig.polyline([(ax.px(t), ax.py(v)) for t, v in enumerate(model) if math.isfinite(v)], OIL, 1.6)
        # residual strip
        res = [
            (m_ - c_) if math.isfinite(m_) and math.isfinite(c_) else 0.0 for m_, c_ in zip(model, clean, strict=False)
        ]
        rmax = max((abs(v) for v in res), default=1.0) or 1.0
        ry0, rh = y0 + h + 4, 20
        fig.rect(x0, ry0, w, rh, fill=PANEL)
        fig.line(x0, ry0 + rh / 2, x0 + w, ry0 + rh / 2, GRID, 0.8)
        fig.polyline([(ax.px(t), ry0 + rh / 2 - v / rmax * rh / 2) for t, v in enumerate(res)], DANGER, 1)
        fig.text(x0 + w, ry0 + rh - 4, "residual", 7.5, FAINT, "end")
    fig.text(
        20,
        height - 8,
        f"liquid rate [{units.get('rate', 'bbl/d')}] · raw (points), cleaned (grey), model (orange); "
        "blind window shaded",
        8.5,
        DIM,
    )
    return fig


def dt_tau(s: dict[str, Any], width: int = 620, height: int = 260) -> Fig:
    """Exponential step response with sampling dots coloured by adequacy (Selector reference)."""
    fig = Fig(width, height, title="Δt versus τ")
    d = s.get("dt_tau") or {}
    dt = float(d.get("dt_days") or 30.0)
    tau = d.get("tau_estimate_days")
    if tau is None:
        taus = [float(t) for t in (d.get("tau_fitted_days") or []) if t is not None]
        tau = sum(taus) / len(taus) if taus else None
    if tau is None:
        fig.text(width / 2, height / 2, "No τ estimate available.", 12, DIM, "middle")
        return fig
    tau = float(tau)
    tmax = max(5 * tau, 4 * dt)
    ax = Axes(fig, 60, 30, width - 80, height - 90, (0.0, tmax), (0.0, 1.05))
    ax.frame(
        "time since injection step [d]",
        "response fraction",
        [(v, fmt_num(v)) for v in nice_ticks(0, tmax, 6)],
        [0, 0.25, 0.5, 0.75, 1.0],
        title="Step response 1 − e^(−t/τ) and the sampling interval",
    )
    curve = [(ax.px(t), ax.py(1 - math.exp(-t / tau))) for t in [tmax * k / 200 for k in range(201)]]
    fig.polyline(curve, WATER, 2)
    ratio = tau / dt if dt > 0 else float("inf")
    col = GOOD if ratio >= 3 else OIL if ratio >= 1 else DANGER
    k = 1
    while k * dt <= tmax:
        t = k * dt
        fig.circle(ax.px(t), ax.py(1 - math.exp(-t / tau)), 4, fill=col, stroke=PAPER, sw=1)
        k += 1
    fig.line(ax.px(tau), ax.y0, ax.px(tau), ax.y0 + ax.h, DIM, 1, dash="3,3")
    fig.text(ax.px(tau) + 4, ax.y0 + 14, f"τ ≈ {fmt_num(tau)} d", 9, DIM, mono=True)
    verdict = (
        "adequate (τ/Δt ≥ 3)" if ratio >= 3 else "marginal (1 ≤ τ/Δt < 3)" if ratio >= 1 else "too coarse (τ/Δt < 1)"
    )
    fig.text(
        ax.x0 + ax.w - 6,
        ax.y0 + ax.h - 8,
        f"Δt = {fmt_num(dt)} d · τ/Δt = {ratio:.1f} · {verdict}",
        9.5,
        col,
        "end",
        "bold",
    )
    _legend(fig, ax.x0, height - 10, [("response", WATER, "line"), ("sampling points (colour = adequacy)", col, "dot")])
    return fig


def tornado(items: list[dict[str, Any]], base: float, unit: str, title: str = "Sensitivity", width: int = 620) -> Fig:
    """Centred tornado: each item has label, low, high (objective values); bars are deviations from base."""
    rh = 28
    height = 60 + rh * max(len(items), 1) + 30
    fig = Fig(width, height, title=title)
    fig.text(20, 22, title, 12, TEXT, "start", "bold")
    if not items:
        fig.text(width / 2, height / 2, "No sensitivity available.", 12, DIM, "middle")
        return fig
    left, bw = 220, width - 220 - 40
    dev = max((abs(float(i["low"]) - base) for i in items), default=1.0)
    dev = max(dev, max((abs(float(i["high"]) - base) for i in items), default=1.0)) or 1.0
    mid = left + bw / 2
    sc = (bw / 2) / dev
    fig.line(mid, 40, mid, height - 30, DIM, 1)
    fig.text(mid, 36, f"base {fmt_num(base)} {unit}", 8.5, DIM, "middle", mono=True)
    for k, it in enumerate(sorted(items, key=lambda i: -abs(float(i["high"]) - float(i["low"])))):
        y = 46 + k * rh
        lo, hi = float(it["low"]) - base, float(it["high"]) - base
        fig.text(left - 8, y + 13, str(it["label"]), 9.5, TEXT, "end")
        for v, col in ((lo, DANGER if lo < 0 else GOOD), (hi, DANGER if hi < 0 else GOOD)):
            x1, x2 = sorted((mid, mid + v * sc))
            fig.rect(x1, y + 3, max(x2 - x1, 1), rh - 10, fill=col, opacity=0.8)
        fig.text(mid + min(lo, hi) * sc - 4, y + 14, fmt_num(base + min(lo, hi)), 8, DIM, "end", mono=True)
        fig.text(mid + max(lo, hi) * sc + 4, y + 14, fmt_num(base + max(lo, hi)), 8, DIM, "start", mono=True)
    fig.text(
        20, height - 10, f"objective value [{unit}] under each perturbation; bar length = change from base", 8.5, DIM
    )
    return fig


def outcome_range(rec: dict[str, Any], unit: str, width: int = 620) -> Fig:
    """Plan vs hold-current cumulative oil, P10–P90 with P50 marks."""
    fig = Fig(width, 150, title="Outcome range")
    fig.text(
        20, 22, "Cumulative oil over the horizon: plan vs hold-current (P10 – P50 – P90)", 12, TEXT, "start", "bold"
    )
    plan, base = rec.get("cum_oil_plan_p10_p50_p90") or [], rec.get("cum_oil_base_p10_p50_p90") or []
    if len(plan) < 3 or len(base) < 3:
        fig.text(width / 2, 90, "No forecast band.", 12, DIM, "middle")
        return fig
    vals = [float(v) for v in plan + base]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or abs(hi) or 1.0
    lo, hi = lo - 0.1 * span, hi + 0.1 * span
    left, bw = 150, width - 190

    def px(v: float) -> float:
        return left + (v - lo) / (hi - lo) * bw

    for k, (label, band, col) in enumerate((("plan", plan, ACCENT), ("hold-current", base, FAINT))):
        y = 50 + k * 34
        fig.text(left - 10, y + 12, label, 10, TEXT, "end")
        fig.rect(px(float(band[0])), y, px(float(band[2])) - px(float(band[0])), 18, fill=col, opacity=0.35, rx=3)
        fig.line(px(float(band[1])), y - 2, px(float(band[1])), y + 20, col, 2.5)
        fig.text(px(float(band[0])) - 4, y + 13, fmt_num(float(band[0])), 8, DIM, "end", mono=True)
        fig.text(px(float(band[2])) + 4, y + 13, fmt_num(float(band[2])), 8, DIM, "start", mono=True)
    fig.text(20, 140, f"[{unit}]; bars span P10–P90, the tick is P50", 8.5, DIM)
    return fig
