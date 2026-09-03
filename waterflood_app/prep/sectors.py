"""Sectorization — architecture §7 ("distance graph + faults + patterns → hydraulically plausible sectors").

Injector–producer pairs closer than ``cutoff`` (a multiple of the median nearest I–P distance)
are edges of a bipartite graph; connected components are sectors. A category ``block`` is a
hard boundary (a fault/pattern proxy) when ``sectors.respect_blocks`` is set. Automatic above
``sectors.auto_above_wells`` wells; below that, blocks alone split the field. Editable in
advanced mode (M4).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from waterflood_app.config import Config
from waterflood_app.prep.grid import Grid


@dataclass
class Sector:
    id: str
    injectors: list[str]
    producers: list[str]

    @property
    def n_wells(self) -> int:
        return len(self.injectors) + len(self.producers)


def _components(n_inj: int, n_prod: int, edges: np.ndarray) -> list[tuple[list[int], list[int]]]:
    """Connected components of the bipartite graph given a boolean (Ni, Np) adjacency."""
    parent = list(range(n_inj + n_prod))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n_inj):
        for j in range(n_prod):
            if edges[i, j]:
                ra, rb = find(i), find(n_inj + j)
                if ra != rb:
                    parent[ra] = rb
    groups: dict[int, tuple[list[int], list[int]]] = {}
    for k in range(n_inj + n_prod):
        r = find(k)
        g = groups.setdefault(r, ([], []))
        (g[0] if k < n_inj else g[1]).append(k if k < n_inj else k - n_inj)
    return list(groups.values())


def sectorize(grid: Grid, cfg: Config, blocks: dict[str, str] | None = None) -> list[Sector]:
    """Split the grid into sectors. Returns one sector covering everything when no split applies."""
    s = cfg.section("sectors")
    ni, npd = grid.n_inj, grid.n_prod
    if ni == 0 or npd == 0:
        return [Sector("S1", list(grid.injectors), list(grid.producers))]
    d = grid.distances()
    n_wells = ni + npd
    edges = np.ones((ni, npd), dtype=bool)
    if d is not None and n_wells > int(s["auto_above_wells"]):
        nearest = np.minimum(d.min(axis=1).mean(), d.min(axis=0).mean())
        cutoff = (
            float(s["distance_cutoff_factor"]) * float(np.median(np.concatenate([d.min(axis=1), d.min(axis=0)])))
            if nearest > 0
            else np.inf
        )
        edges = d <= cutoff
    if blocks and bool(s["respect_blocks"]):
        bi = [blocks.get(grid.well_of_entity.get(w, w)) for w in grid.injectors]
        bp = [blocks.get(grid.well_of_entity.get(w, w)) for w in grid.producers]
        same = np.array([[(a is None or b is None or a == b) for b in bp] for a in bi], dtype=bool)
        edges &= same
    comps = _components(ni, npd, edges)
    # isolated wells (no edge) join the nearest sector by distance so nothing is dropped silently
    sectors: list[Sector] = []
    isolated_i = [c[0][0] for c in comps if len(c[0]) == 1 and not c[1]]
    isolated_p = [c[1][0] for c in comps if len(c[1]) == 1 and not c[0]]
    real = [c for c in comps if c[0] and c[1]]
    if not real:
        return [Sector("S1", list(grid.injectors), list(grid.producers))]
    for k, (ii, jj) in enumerate(sorted(real, key=lambda c: (min(c[0]), min(c[1])))):
        sectors.append(Sector(f"S{k + 1}", [grid.injectors[a] for a in ii], [grid.producers[b] for b in jj]))
    if d is not None:
        for i in isolated_i:
            j = int(np.argmin(d[i]))
            for sec in sectors:
                if grid.producers[j] in sec.producers:
                    sec.injectors.append(grid.injectors[i])
                    break
        for j in isolated_p:
            i = int(np.argmin(d[:, j]))
            for sec in sectors:
                if grid.injectors[i] in sec.injectors:
                    sec.producers.append(grid.producers[j])
                    break
    else:
        sectors[0].injectors.extend(grid.injectors[i] for i in isolated_i)
        sectors[0].producers.extend(grid.producers[j] for j in isolated_p)
    return sectors


def blocks_from_category(category: object | None) -> dict[str, str] | None:
    """well → block from the category frame (None when the column is absent)."""
    import polars as pl

    if category is None or not isinstance(category, pl.DataFrame) or "block" not in category.columns:
        return None
    out: dict[str, str] = {}
    for w, b in category.select(["well", "block"]).iter_rows():
        if b is not None:
            out[str(w)] = str(b)
    return out or None
