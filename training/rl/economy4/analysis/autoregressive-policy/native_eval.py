"""Expose the C++ reference game and its observation tensors to the policy."""

import ctypes as ct
from pathlib import Path

import numpy as np
import torch

from rl.obs import collate
from model import GAME_OBSERVATION_FIELDS, FEAT_DIM


FloatPointer = ct.POINTER(ct.c_float)
IntPointer = ct.POINTER(ct.c_int32)


class View(ct.Structure):
    _fields_ = [
        *((name, ct.c_int) for name in ("turn", "is_new_turn", "tiles", "nations", "armies", "candidates")),
        *((name, FloatPointer) for name in ("global_token", "market", "nation", "tile", "army", "candidate", "executable")),
        ("pointer", IntPointer), ("type", IntPointer),
    ]


class Result(ct.Structure):
    _fields_ = [
        *((name, ct.c_int) for name in (
            "done", "turn", "seat", "alive", "opponents_alive", "initial_land", "land", "decisions",
            "invalid", "recruits", "attacks", "starved", "shortage", "blackouts",
        )),
        *((name, ct.c_double) for name in ("nominal_gdp", "fixed_gdp", "total_nominal_gdp")),
    ]

    def as_dict(self):
        return {key: getattr(self, key) for key, _ in self._fields_}


class Engine:
    def __init__(self, library: Path):
        self.lib = ct.CDLL(str(library.resolve()))
        self.bind("error", [], ct.c_char_p)
        self.bind("create", [ct.c_uint64, ct.c_int, ct.c_int], ct.c_void_p)
        self.bind("destroy", [ct.c_void_p], None)
        self.bind("observe", [ct.c_void_p, ct.POINTER(View)], ct.c_int)
        self.bind("play", [ct.c_void_p, ct.c_int], ct.c_int)
        self.bind("result", [ct.c_void_p, ct.POINTER(Result)], None)

    def bind(self, name, arguments, result):
        method = getattr(self.lib, f"zhanguo_eval_{name}")
        method.argtypes, method.restype = arguments, result
        setattr(self, name, method)

    def check(self, condition):
        if not condition:
            raise RuntimeError(self.error().decode("utf-8", errors="replace"))

    def match(self, seed: int, size: int, turns: int):
        return Match(self, seed, size, turns)

    def self_play(self, seed: int, size: int, turns: int):
        if not hasattr(self, "self_create"):
            self.bind("self_create", [ct.c_uint64, ct.c_int, ct.c_int], ct.c_void_p)
            self.bind("self_destroy", [ct.c_void_p], None)
            self.bind("self_observe", [ct.c_void_p, ct.POINTER(View), ct.POINTER(ct.c_int)], ct.c_int)
            self.bind("self_advance", [ct.c_void_p, ct.c_int, ct.POINTER(ct.c_int)], ct.c_int)
            self.bind("self_result", [ct.c_void_p, ct.POINTER(Result)], None)
        return SelfPlay(self, seed, size, turns)


def array(pointer, shape, dtype=np.float32):
    size = int(np.prod(shape))
    if size == 0:
        return np.empty(shape, dtype=dtype)
    return np.ctypeslib.as_array(pointer, shape=(size,)).reshape(shape).copy()

def tensors(view):
    sizes = dict(GAME_OBSERVATION_FIELDS)
    obs = {
        "globals": array(view.global_token, (sizes["globals"],)),
        "market": array(view.market, (6, sizes["market"])),
        "nations": array(view.nation, (view.nations, sizes["nations"])),
        "tiles": array(view.tile, (view.tiles, sizes["tiles"])),
        "armies": array(view.army, (view.armies, sizes["armies"])),
        "cand": array(view.candidate, (view.candidates, FEAT_DIM)),
        "cand_ptr": array(view.pointer, (view.candidates, 3), np.int32),
        "cand_exec": array(view.executable, (view.candidates,)),
        "cand_type": array(view.type, (view.candidates,), np.int32),
    }
    return {k: torch.from_numpy(v) for k, v in collate([obs]).items()}


class Match:
    def __init__(self, engine, seed, size, turns):
        self.engine = engine
        self.handle = engine.create(seed, size, turns)
        engine.check(self.handle)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.engine.destroy(self.handle)
        self.handle = None

    def observe(self):
        view = View()
        self.engine.check(self.engine.observe(self.handle, ct.byref(view)) == 0)
        return view.turn, bool(view.is_new_turn), tensors(view)

    def play(self, action: int):
        self.engine.check(self.engine.play(self.handle, action) == 0)
        return self.result()

    def result(self):
        result = Result()
        self.engine.result(self.handle, ct.byref(result))
        return result


class SelfPlay:
    def __init__(self, engine, seed, size, turns):
        self.engine = engine
        self.handle = engine.self_create(seed, size, turns)
        engine.check(self.handle)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.engine.self_destroy(self.handle)

    def observe(self):
        view, actor = View(), ct.c_int()
        self.engine.check(self.engine.self_observe(self.handle, ct.byref(view), ct.byref(actor)) == 0)
        if actor.value < 0:
            return None
        return actor.value, view.turn, bool(view.is_new_turn), tensors(view)

    def play(self, action):
        period = ct.c_int()
        self.engine.check(self.engine.self_advance(self.handle, action, ct.byref(period)) == 0)
        return period.value

    def result(self):
        result = (Result * 4)()
        self.engine.self_result(self.handle, result)
        return tuple(row.as_dict() for row in result)
