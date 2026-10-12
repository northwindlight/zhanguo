from ..core import *

def initialize(world):
    world.nations: dict[str, "Nation"] = {}
    world.order: list[str] = []
    world.bank: dict = {"on": False, "rate": 0.0, "loans": {}}
    world.prices: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}
    world.equilibrium: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}
    world.flow_in: dict[str, int] = {g: 0 for g in TRADEABLE}
    world.flow_out: dict[str, int] = {g: 0 for g in TRADEABLE}
    world.churn: dict[str, dict[str, dict[str, int]]] = {}
    world.gold_carry: dict[str, float] = {}
    world.econ_summary: dict[str, str] = {}
    world.econ_reports: dict[str, list[dict]] = {}
    world.ledger: dict[str, dict] = {}
    world.gdp_run: dict[str, float] = {}
    world.spend: dict[str, dict] = {}
