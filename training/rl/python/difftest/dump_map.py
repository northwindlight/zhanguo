import sys, json, importlib
root, kind = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
if kind == 'new':
    from mods.native.map_generation.algorithm import MapGen
else:
    from mapgen import MapGen
out = {}
for seed in (1, 7, 7001, 7005, 12345):
    for size in (12, 16, 24, 28):
        g = MapGen(seed, size)
        out[f"{seed}:{size}"] = [[[g.terrain(x, y), sorted(g.resources(x, y).items()), g.name(x, y)] for x in range(size)] for y in range(size)]
json.dump(out, open(sys.argv[3], 'w'), ensure_ascii=False)
