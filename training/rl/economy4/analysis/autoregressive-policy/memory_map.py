"""Private thought map and the future mail payload share a 4-by-4 format."""

from dataclasses import dataclass, replace
from enum import IntEnum


SLOTS = 4
CAPACITY = 4
SYMBOLS = 4
MIN_THOUGHTS = 8
MAX_THOUGHTS = 30
EXTENDED_THOUGHTS = 100
GAME_BUDGET = 60


class Op(IntEnum):
    CLEAR_FIRST = SLOTS * SYMBOLS
    STOP = SLOTS * SYMBOLS + SLOTS
    START = STOP + 1
    GAME = START + 1


@dataclass(frozen=True)
class MemoryMap:
    slots: tuple[tuple[int, ...], ...] = ((),) * SLOTS

    def __post_init__(self):
        if not isinstance(self.slots, tuple) or len(self.slots) != SLOTS or any(
            not isinstance(row, tuple) or len(row) > CAPACITY
            or any(symbol not in range(1, SYMBOLS + 1) for symbol in row)
            for row in self.slots
        ):
            raise ValueError("expected four left-aligned slots containing symbols 1..4")

    def padded(self):
        return tuple(row + (0,) * (CAPACITY - len(row)) for row in self.slots)

    def write_mask(self) -> tuple[bool, ...]:
        return tuple(len(row) < CAPACITY for row in self.slots for _ in range(SYMBOLS)) + (True,) * SLOTS

    def write(self, operation: int):
        if not 0 <= operation < Op.STOP or not self.write_mask()[operation]:
            raise ValueError("illegal memory operation")
        if operation < Op.CLEAR_FIRST:
            slot, symbol = divmod(operation, SYMBOLS)
            new = self.slots[slot] + (symbol + 1,)
        else:
            slot = operation - Op.CLEAR_FIRST
            new = ()
        return MemoryMap(self.slots[:slot] + (new,) + self.slots[slot + 1:])


@dataclass(frozen=True)
class TurnThought:
    graph: MemoryMap = MemoryMap()
    writes: int = 0
    closed: bool = False

    def mask(self) -> tuple[bool, ...]:
        if self.closed:
            return (False,) * (Op.STOP + 1)
        return self.graph.write_mask() + (self.writes >= MIN_THOUGHTS,)

    def choose(self, operation: int, limit: int = MAX_THOUGHTS):
        if not 0 <= operation <= Op.STOP or not self.mask()[operation]:
            raise ValueError("illegal thought action for this phase")
        if operation == Op.STOP:
            return replace(self, closed=True)
        writes = self.writes + 1
        return TurnThought(self.graph.write(operation), writes, writes == limit)

    def next_turn(self):
        if not self.closed:
            raise ValueError("active thought phase must end before next turn")
        return TurnThought(self.graph)


@dataclass(frozen=True)
class Letter:
    sender: int
    recipient: int
    turn: int
    graph: MemoryMap


@dataclass(frozen=True)
class Draft:
    recipient: int
    graph: MemoryMap = MemoryMap()

    def write(self, operation: int):
        return replace(self, graph=self.graph.write(operation))

    def send(self, sender: int, turn: int) -> Letter:
        if sender == self.recipient:
            raise ValueError("letter requires a different recipient")
        return Letter(sender, self.recipient, turn, self.graph)
