"""Read C++ rollout files into the Python PPO episode tuple format."""

from pathlib import Path
import struct

import numpy as np


OPPONENTS = ("rule", "w1_25_2", "w1_40_3", "w2", "w3")
SCHEMAS = {b"ZGRL2\0\0\0": (20, 48, 72), b"ZGRL3\0\0\0": (40, 72, 80)}


def read_episode(path):
    with Path(path).open("rb") as f:
        def take(n):
            value = f.read(n)
            if len(value) != n:
                raise ValueError(f"truncated trajectory {path}")
            return value

        def unpack(fmt):
            return struct.unpack("<" + fmt, take(struct.calcsize("<" + fmt)))

        def tensor(dtype, shape):
            dtype = np.dtype(dtype)
            data = take(int(np.prod(shape)) * dtype.itemsize)
            return np.frombuffer(data, dtype).reshape(shape)

        schema = SCHEMAS.get(take(8))
        if schema is None:
            raise ValueError(f"unknown trajectory header in {path}")
        nation_dim, tile_dim, summary_dim = schema
        seed, = unpack("Q")
        decisions, summaries, auxiliary = [], [], []
        while True:
            tag, = unpack("B")
            if tag == 1:
                turn, = unpack("i")
                if turn != len(summaries):
                    raise ValueError(f"out-of-order turn in {path}")
                summaries.append(tensor("<f4", (summary_dim,)))
                lost = tensor("<f4", (8,))
                if turn:
                    auxiliary[-1] = lost
                auxiliary.append(None)
            elif tag == 2:
                turn, action, logp, value, n, t, a, c = unpack("iiffIIII")
                if turn != len(summaries) - 1 or not 0 <= action < c:
                    raise ValueError(f"invalid decision in {path}")
                sizes = dict(globals=(32,), market=(6, 8), nations=(n, nation_dim),
                             tiles=(t, tile_dim), armies=(a, 20), cand=(c, 40))
                obs = {key: tensor("<f2", shape) for key, shape in sizes.items()}
                obs["cand_ptr"] = tensor("<i4", (c, 3))
                obs["cand_exec"] = obs["cand"][:, 0].copy()
                obs["cand_type"] = tensor("<i4", (c,))
                decisions.append(dict(obs=obs, a=action, lp=logp, v=value, ti=turn))
            elif tag == 3:
                reward, winner, seat, turns, opponent = unpack("fiiii")
                if f.read(1):
                    raise ValueError(f"trailing data in {path}")
                summary = dict(seed=seed, winner=None if winner < 0 else winner, seat=seat, turns=turns,
                               opp=OPPONENTS[opponent], reward=reward, decisions=len(decisions))
                return decisions, reward, summary, summaries, auxiliary
            else:
                raise ValueError(f"unknown trajectory record {tag} in {path}")


def episodes(directory):
    for path in sorted(Path(directory).glob("*.zgrl")):
        yield read_episode(path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    args = parser.parse_args()
    for _, _, summary, _, _ in episodes(args.directory):
        print(summary)
