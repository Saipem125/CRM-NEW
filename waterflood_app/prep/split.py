"""Train / blind split — architecture §7 ("last 20–25 % of each window held out for the blind test")."""

from __future__ import annotations

from dataclasses import dataclass

from waterflood_app.config import Config


@dataclass(frozen=True)
class Split:
    n_train: int
    n_total: int

    @property
    def n_blind(self) -> int:
        return self.n_total - self.n_train

    @property
    def train(self) -> slice:
        return slice(0, self.n_train)

    @property
    def blind(self) -> slice:
        return slice(self.n_train, self.n_total)


def train_blind_split(n_steps: int, cfg: Config) -> Split:
    frac = float(cfg["split.blind_fraction"])
    min_blind = int(cfg["split.min_blind_steps"])
    n_blind = max(min_blind, round(n_steps * frac))
    n_blind = min(n_blind, max(0, n_steps - 2))  # never leave fewer than 2 training points
    return Split(n_train=n_steps - n_blind, n_total=n_steps)
