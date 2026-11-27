"""Anneal the PPO holding bonus after sustained terminal nominal GDP reaches its target."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from statistics import mean


WINDOW = 16
THRESHOLD = 60.0
FADE_GAMES = 200
RULE = dict(metric="four_seat_final_nominal_gdp", window_games=WINDOW, threshold=THRESHOLD,
            fade_games=FADE_GAMES, trigger_at="ppo_batch_end", decay="linear_by_batch_end")


@dataclass(frozen=True)
class LandRewardSchedule:
    recent_gdp: tuple = ()
    triggered_at: int | None = None

    @property
    def rolling_gdp(self):
        return mean(self.recent_gdp) if len(self.recent_gdp) == WINDOW else None

    def weight(self, batch_end):
        if self.triggered_at is None:
            return 1.0
        return max(0.0, 1.0 - (batch_end - self.triggered_at) / FADE_GAMES)

    def after_batch(self, batch_end, metrics):
        values = tuple(row["final_nominal_gdp"] for row in metrics)
        if not values or not all(math.isfinite(v) for v in values):
            raise ValueError("Expected finite, four-seat terminal GDP for every completed game")
        recent = (self.recent_gdp + values)[-WINDOW:]
        trigger = self.triggered_at
        if trigger is None and len(recent) == WINDOW and mean(recent) >= THRESHOLD:
            trigger = batch_end
        return LandRewardSchedule(recent, trigger)

    def state_dict(self):
        return asdict(self)

    @classmethod
    def from_checkpoint(cls, state, path, expected_contract, lr):
        old = state.get("contract")
        if old is None and not state.get("games", 0):
            return cls()
        if state["lr"] != lr:
            raise ValueError("PPO continuation changed the learning rate")
        if old == expected_contract:
            saved = state["land_reward"]
            recent, trigger = tuple(saved["recent_gdp"]), saved["triggered_at"]
            if (not 0 < len(recent) <= min(WINDOW, state["games"]) or not all(map(math.isfinite, recent))
                    or trigger is not None and not (isinstance(trigger, int) and WINDOW <= trigger <= state["games"])):
                raise ValueError("Invalid land reward checkpoint state")
            return cls(recent, trigger)
        legacy = {k: v for k, v in expected_contract.items() if k != "land_reward_schedule"}
        if old != legacy:
            raise ValueError("PPO continuation changed the task contract")

        # A legacy segment publishes a batch report beside its checkpoint. Recover
        # the contiguous GDP history available there; a standalone .pt starts a fresh window.
        end = state["games"]
        batches, count = [], 0
        while end and count < WINDOW:
            report = path.parent / f"games-{end:04}.json"
            if not report.exists():
                break
            batch = json.loads(report.read_text())
            if end == state["games"]:
                with path.open("rb") as handle:
                    if hashlib.file_digest(handle, "sha256").hexdigest() != batch["sha256"]:
                        raise ValueError("Legacy terminal GDP report belongs to a different checkpoint")
            rows = batch["metrics"]
            if (batch["games"] != end or not 0 < len(rows) <= end or batch["contract"] != old
                    or batch["initial_sha256"] != state["initial_sha256"]):
                raise ValueError(f"Inconsistent legacy PPO batch history: {report}")
            batches.append((end, rows))
            end -= len(rows)
            count += len(rows)
        schedule = cls()
        for end, rows in reversed(batches):
            schedule = schedule.after_batch(end, rows)
        return schedule
