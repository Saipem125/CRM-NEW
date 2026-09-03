"""Milestone 0 acceptance — §16 Tier 1 synthetic suite.

1. The frozen fixtures on disk match ``manifest.json`` (immutability).
2. The generator is deterministic (re-generation reproduces the frozen tables).
3. pywaterflood (Phase-1 engine and permanent baseline, §9) recovers ``streak_5x4`` f_ij within
   10 % and τ within 20 % on the noise-free case.  Tolerance definition: DECISIONS.md, M0.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal
from pywaterflood import CRM

from waterflood_app.validation import synthetic_suite as suite
from waterflood_app.validation.synthetic_suite import generate

F_REL_TOL = 0.10  # pairs with true f_ij >= F_SMALL
F_ABS_TOL = 0.02  # pairs below F_SMALL (barriers)
F_SMALL = 0.05
TAU_REL_TOL = 0.20


def _manifest() -> dict[str, str]:
    data = json.loads((suite.SUITE_DIR / "manifest.json").read_text(encoding="utf-8"))
    return dict(data["files"])


def test_manifest_lists_every_case() -> None:
    files = _manifest()
    for name in suite.CASES:
        assert f"{name}/rates.parquet" in files
        assert (suite.case_dir(name) / "truth.json").exists()


def test_fixtures_are_immutable() -> None:
    for rel, digest in _manifest().items():
        path = suite.SUITE_DIR / rel
        assert path.exists(), rel
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"{rel} changed"


def test_generator_is_deterministic(tmp_path: Path) -> None:
    case = generate.build_streak_5x4(0.0)
    generate.write_case(case, tmp_path)
    frozen = suite.load_case("streak_5x4")
    assert_frame_equal(pl.read_parquet(tmp_path / "streak_5x4" / "rates.parquet"), frozen.rates)
    regenerated = json.loads((tmp_path / "streak_5x4" / "truth.json").read_text(encoding="utf-8"))
    assert regenerated["f_ij"] == frozen.truth["f_ij"]
    assert regenerated["tau_days"] == frozen.truth["tau_days"]


@pytest.mark.parametrize("name", suite.CASES)
def test_truth_is_physically_consistent(name: str) -> None:
    case = suite.load_case(name)
    f = case.f_matrix()
    assert np.all(f >= 0.0) and np.all(f <= 1.0)
    assert np.all(f.sum(axis=1) <= 1.0 + 1e-9), "Σ_j f_ij per injector must be ≤ 1"
    assert case.truth["tau_over_dt_min"] >= 3.0, "§8 gate τ/Δt ≥ 3 must hold for the truth"
    assert min(case.truth["injection_cv"].values()) >= 0.15, "§7 gate CV ≥ 0.15"
    assert case.truth["identifiability"]["O_d"] > 6.0
    assert "optimizer" in case.truth and case.truth["optimizer"]["gain_vs_equal_split_pct"] > 0.0


def _pwf_inputs(m: suite.Matrices) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Align the fixture convention with pywaterflood's convolution.

    In the Sayarpour recursion the injection of the interval ending at t_0 is already inside
    q(0); pywaterflood adds a (1−e^{−Δt/τ})·f·i(0) response at the first sample on top of the
    primary term. Zeroing i(0) in the fit input makes the two models identical (DECISIONS.md, M0).
    """
    inj = m.injection.copy()
    inj[0] = 0.0
    return m.production, inj, m.time_days


def _fit_pywaterflood(case: suite.Case) -> CRM:
    m = suite.to_matrices(case.rates, case.injectors, case.producers)
    crm = CRM(primary=True, tau_selection="per-producer", constraints="up-to one")
    crm.fit(*_pwf_inputs(m))
    return crm


def test_pywaterflood_recovers_streak_5x4() -> None:
    case = suite.load_case("streak_5x4")
    crm = _fit_pywaterflood(case)
    gains = np.asarray(crm.gains, dtype=np.float64)
    tau = np.asarray(crm.tau, dtype=np.float64).ravel()
    f_true = case.f_matrix()  # (Ni, Np)
    tau_true = case.tau_vector()
    # pywaterflood gains are (n_producers, n_injectors)
    f_hat = gains.T if gains.shape == f_true.T.shape else gains
    assert f_hat.shape == f_true.shape
    big = f_true >= F_SMALL
    rel = np.abs(f_hat[big] - f_true[big]) / f_true[big]
    absd = np.abs(f_hat[~big] - f_true[~big])
    assert rel.max() <= F_REL_TOL, f"f_ij relative error {rel.max():.3f} > {F_REL_TOL}\n{f_hat}\n{f_true}"
    assert absd.max() <= F_ABS_TOL, f"barrier pairs not recovered: {absd.max():.3f}"
    tau_rel = np.abs(tau - tau_true) / tau_true
    assert tau_rel.max() <= TAU_REL_TOL, f"τ relative error {tau_rel.max():.3f} > {TAU_REL_TOL}\n{tau}\n{tau_true}"
    streak = case.truth["streak"]
    i, j = (
        case.injectors.index(streak["injector"]),
        case.producers.index(streak["producer"]),
    )
    assert f_hat[i, j] == f_hat.max(), "the streak must be the strongest recovered pair"


def test_pywaterflood_streak_noisy_fits_well() -> None:
    """Informative floor for the noisy variant (formal blind R² floors are Milestone 1)."""
    case = suite.load_case("streak_5x4_noise5")
    m = suite.to_matrices(case.rates, case.injectors, case.producers)
    crm = _fit_pywaterflood(case)
    pred = np.asarray(crm.predict(), dtype=np.float64)  # type: ignore[no-untyped-call]
    ss_res = ((pred - m.production) ** 2).sum()
    ss_tot = ((m.production - m.production.mean(axis=0)) ** 2).sum()
    # 5 % noise on oil and water caps the attainable R² at ≈ 0.89 for this signal
    assert 1.0 - ss_res / ss_tot >= 0.8
