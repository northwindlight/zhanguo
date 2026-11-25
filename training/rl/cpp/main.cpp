// zhanguo RL engine CLI：建世界、空跑回合、存/读档。
#include "engine.hpp"
#include "rl_env.hpp"
#include <cstdio>
#include <cstdlib>
#include <string>

int main(int argc, char** argv) {
    if (argc < 2) {
        std::printf("usage:\n  %s sim <seed> <size> <turns> [nations]\n"
                    "  %s save <seed> <size> <turns> <path>\n  %s load <path>\n", argv[0], argv[0], argv[0]);
        return 1;
    }
    std::string cmd = argv[1];
    if (cmd == "vecsim") {
        int games = argc > 2 ? std::stoi(argv[2]) : 4;
        int nations = argc > 3 ? std::stoi(argv[3]) : 2;
        int turns = argc > 4 ? std::stoi(argv[4]) : 20;
        std::mt19937 random(7);
        size_t decisions = 0;
        for (int g = 0; g < games; ++g) {
            World world;
            world.seed = 1000 + g;
            world.size = 13;
            world.max_turns = turns;
            world.setup(nations, {});
            world.bank_on = false;
            std::vector<rl::Observer> observers(nations);
            std::vector<rl::DiplomacyCache> diplomacy(nations);
            while (world.turn < turns) {
                world.begin_turn();
                for (int n = 0; n < nations; ++n) {
                    if (!world.alive(n)) continue;
                    for (int step = 0; step < 12; ++step) {
                        auto candidates = rl::candidates(world, n, true, &diplomacy[n]);
                        auto observation = observers[n].encode(world, n, candidates);
                        ++decisions;
                        const auto& action = candidates[random() % observation.cand.size()];
                        if (action.type == rl::Type::EndTurn) break;
                        rl::apply(world, n, action);
                    }
                }
                world.resolve_turn();
            }
        }
        std::printf("games=%d nations=%d turns=%d decisions=%zu\n", games, nations, turns, decisions);
        return 0;
    }
    if (cmd == "mapstat") {
        World w;
        w.seed = std::stoull(argv[2]);
        w.size = std::stoi(argv[3]);
        w.setup(0, {});
        uint64_t fp = 1469598103934665603ULL;
        auto mix = [&](uint64_t v) { fp ^= v; fp *= 1099511628211ULL; };
        for (int y = 0; y < w.size; y++)
            for (int x = 0; x < w.size; x++) {
                Tile& t = w.tiles[x * w.size + y];
                mix((uint64_t)t.terrain);
                for (int r = 0; r < TR_N; r++) mix((uint64_t)t.tres[r]);
            }
        std::printf("fp=%llu\n", (unsigned long long)fp);
        return 0;
    }
    if (cmd == "mapdump") {
        World w;
        w.seed = std::stoull(argv[2]);
        w.size = std::stoi(argv[3]);
        w.gen_map();
        FILE* f = fopen(argv[4], "w");
        for (int y = 0; y < w.size; y++)
            for (int x = 0; x < w.size; x++) {
                Tile& t = w.tiles[x * w.size + y];
                fprintf(f, "%d %d %d", x, y, (int)t.terrain);
                for (int r = 0; r < TR_N; r++) fprintf(f, " %d", t.tres[r]);
                fprintf(f, " %.17g", w.tmask[y * w.size + x]);
                fprintf(f, "\n");
            }
        fclose(f);
        return 0;
    }
    if (cmd == "replay") {
        if (argc < 4) { std::printf("usage: replay <script> <dump>\n"); return 1; }
        return run_replay(argv[2], argv[3]);
    }
    if (cmd == "sim") {
        if (argc < 5) return 1;
        World w;
        w.seed = std::stoull(argv[2]);
        w.size = std::stoi(argv[3]);
        w.max_turns = std::stoi(argv[4]);
        int nn = argc > 5 ? std::stoi(argv[5]) : 3;
        w.setup(nn, {});
        for (int t = 1; t <= w.max_turns; t++) { w.begin_turn(); w.resolve_turn(); }
        int live = 0, owned = 0;
        for (auto& n : w.nations) live += n.alive;
        for (int i = 0; i < w.size * w.size; i++) owned += w.tiles[i].owner >= 0;
        std::printf("turn=%d alive=%d owned=%d armies=%zu\n", w.turn, live, owned, w.armies.size());
        for (int n = 0; n < (int)w.nations.size(); n++) {
            if (!w.nations[n].alive) continue;
            std::printf("  n%d gold=%g food=%g wood=%g ore=%g oil=%g equip=%g supply=%g\n", n,
                        w.nations[n].res[GOLD], w.nations[n].res[FOOD], w.nations[n].res[WOOD],
                        w.nations[n].res[ORE], w.nations[n].res[OIL], w.nations[n].res[EQUIP],
                        w.nations[n].res[SUPPLY]);
        }
        std::printf("market:");
        for (int g = 0; g < 6; g++) std::printf(" %.2f/%d", w.price[g], w.minv[g]);
        std::printf("\n");
        return 0;
    }
    if (cmd == "save") {
        if (argc < 6) return 1;
        World w;
        w.seed = std::stoull(argv[2]);
        w.size = std::stoi(argv[3]);
        w.max_turns = std::stoi(argv[4]);
        w.setup(3, {});
        for (int t = 1; t <= w.max_turns; t++) { w.begin_turn(); w.resolve_turn(); }
        std::printf("saved=%d\n", (int)w.save(argv[5]));
        return 0;
    }
    if (cmd == "load") {
        if (argc < 3) return 1;
        World w;
        if (!w.load(argv[2])) { std::printf("load failed\n"); return 1; }
        for (int t = 1; t <= 2; t++) { w.begin_turn(); w.resolve_turn(); }
        std::printf("loaded turn=%d size=%d alive=%d\n", w.turn, w.size,
                    (int)std::count_if(w.nations.begin(), w.nations.end(),
                                       [](const Nation& n) { return n.alive; }));
        return 0;
    }
    return 1;
}
