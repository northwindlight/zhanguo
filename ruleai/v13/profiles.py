"""Persistent economic preferences; operating solvency is shared by all profiles."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    horizon: int
    reserve: int
    cash_periods: float
    self_supply: float
    export: float
    gold: float
    release: float


PROFILES = {
    'balanced': Profile(32, 3, 2, 1.15, 1, 1, 1),
    'integrated': Profile(48, 4, 2, 1.7, .85, .95, .7),
    'merchant': Profile(24, 2, 1, .85, 1.25, .85, 1),
    'buffer': Profile(40, 6, 3, 1.3, .95, 1, .4),
    'liquid': Profile(24, 2, 4, 1, .85, 1.35, 1),
}
