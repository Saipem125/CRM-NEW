# Changelog

## [0.0.1] — Milestone 0 (2026-09-03)
- Repository scaffold per §19; pyproject with ruff / mypy --strict / pytest; GitHub Actions CI.
- `docs/spec/` holds the four source-of-truth HTML documents; `docs/BUILD_PROMPT.md` the build prompt.
- Synthetic ground-truth suite (§16 Tier 1): `streak_5x4` (noise-free + 5 % noise), `aquifer_6x9`, `converted_wells`, `allocated_noisy`, `sectored_60`, each frozen as Parquet with `truth.json`, produced by `waterflood_app/validation/synthetic_suite/generate.py`.
- `tests/test_fixtures.py`: pywaterflood recovers `streak_5x4` f_ij within 10 % and τ within 20 %.
