"""Read C++ rollout files into the Python PPO episode tuple format."""

from pathlib import Path
import struct
import mmap

import numpy as np


OPPONENTS = ("rule", "w1_25_2", "w1_40_3", "w2", "w3", "rule_economy")
WIDE_NATIONS_SCHEMA = b"ZGRL5\0\0\0"
SELF_PLAY_SCHEMA = b"ZGRL6\0\0\0"
MIXED_BC_SCHEMA = b"ZGRL7\0\0\0"
HYBRID_BC_SCHEMA = b"ZGRL8\0\0\0"
MIXED_SCHEMAS = (MIXED_BC_SCHEMA,HYBRID_BC_SCHEMA)
GDP_SCHEMAS = (b"ZGRL4\0\0\0", WIDE_NATIONS_SCHEMA, SELF_PLAY_SCHEMA, *MIXED_SCHEMAS)
SCHEMAS = {b"ZGRL2\0\0\0": (20, 48, 72), b"ZGRL3\0\0\0": (40, 72, 80),
           **{header: (40, 72, 80) for header in GDP_SCHEMAS}}


def read_episode(path, *, mixed_bc=False, mmap_arrays=False):
    with Path(path).open("rb") as f:
        mapped=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) if mmap_arrays else None
        position=0
        def take(n):
            nonlocal position
            if mapped is not None:
                value=memoryview(mapped)[position:position+n]
                position+=n
            else:value=f.read(n)
            if len(value) != n:
                raise ValueError(f"truncated trajectory {path}")
            return value

        def unpack(fmt):
            return struct.unpack("<" + fmt, take(struct.calcsize("<" + fmt)))

        def tensor(dtype, shape):
            dtype = np.dtype(dtype)
            data = take(int(np.prod(shape)) * dtype.itemsize)
            return np.frombuffer(data, dtype).reshape(shape)

        header = take(8)
        if (header in MIXED_SCHEMAS) != mixed_bc:
            raise ValueError(f"Unexpected teaching-trajectory mode in {path}")
        schema = SCHEMAS.get(header)
        if schema is None:
            raise ValueError(f"unknown trajectory header in {path}")
        nation_dim, tile_dim, summary_dim = schema
        seed, = unpack("Q")
        decisions, summaries, auxiliary = [], [], []
        rewards, nominal, starters = [], [], []
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
            elif tag == 2 or (mixed_bc and (tag == 5 or (tag==6 and header==HYBRID_BC_SCHEMA))):
                turn, action, logp, value, n, t, a, c = unpack("iiffIIII")
                if turn != len(summaries) - 1 or not 0 <= action < c:
                    raise ValueError(f"invalid decision in {path}")
                sizes = dict(globals=(32,), market=(6, 8), nations=(n, nation_dim),
                             tiles=(t, tile_dim), armies=(a, 20), cand=(c, 40))
                obs = {key: tensor("<f4" if key == "nations" and header in (WIDE_NATIONS_SCHEMA, SELF_PLAY_SCHEMA, *MIXED_SCHEMAS)
                                   else "<f2", shape)
                       for key, shape in sizes.items()}
                obs["cand_ptr"] = tensor("<i4", (c, 3))
                obs["cand_exec"] = obs["cand"][:, 0].copy()
                obs["cand_type"] = tensor("<i4", (c,))
                decision = dict(obs=obs, a=action, lp=logp, v=value, ti=turn)
                if mixed_bc:
                    decision["teacher"] = tag in (5,6)
                    decision["source"] = 'economy' if tag==6 else 'script' if tag==5 else 'model'
                decisions.append(decision)
            elif tag == 3:
                reward, winner, seat, turns, opponent = unpack("fiiii")
                has_trailing=(position<len(mapped)) if mapped is not None else f.read(1)
                if has_trailing:
                    raise ValueError(f"trailing data in {path}")
                summary = dict(seed=seed, winner=None if winner < 0 else winner, seat=seat, turns=turns,
                               opp=OPPONENTS[opponent], reward=reward, decisions=len(decisions))
                if header in GDP_SCHEMAS:
                    if len(rewards) != turns or len(summaries) != turns or not np.isclose(sum(rewards), reward):
                        raise ValueError(f"missing or invalid GDP rewards in {path}")
                    self_play = header in (SELF_PLAY_SCHEMA, *MIXED_SCHEMAS)
                    valid_seat = 0 <= seat < 4 if header == SELF_PLAY_SCHEMA else seat == seed % 4
                    if not valid_seat or any(s != (seed // 4 + t) % 4 for t, s in enumerate(starters)):
                        raise ValueError(f"invalid four-country action order in {path}")
                    summary.update(opp="market4_selfplay" if self_play else "market4",
                                   turn_rewards=rewards, nominal_gdp=nominal, starters=starters)
                return decisions, reward, summary, summaries, auxiliary
            elif tag == 4 and header in GDP_SCHEMAS:
                turn, gdp, current, first = unpack("iffi")
                if turn != len(rewards) or turn != len(summaries) - 1:
                    raise ValueError(f"out-of-order GDP reward in {path}")
                rewards.append(gdp)
                nominal.append(current)
                starters.append(first)
            else:
                raise ValueError(f"unknown trajectory record {tag} in {path}")


def episodes(directory, *, mixed_bc=False, mmap_arrays=False):
    for path in sorted(Path(directory).glob("*.zgrl")):
        yield read_episode(path, mixed_bc=mixed_bc,mmap_arrays=mmap_arrays)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    args = parser.parse_args()
    for _, _, summary, _, _ in episodes(args.directory):
        print(summary)
