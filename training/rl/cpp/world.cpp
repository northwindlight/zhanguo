// 世界：开局布点与公开查询。
#include "engine.hpp"
#include <tuple>

void World::setup(int n_nations, const std::vector<std::pair<int, int>>& starts) {
    gen_map();
    pyrng.seed(seed);
    bank_on = true;   // main：央行默认存在   // 战斗骰子：与 main 的 random.Random(seed) 同序列
    turn = 0;
    nations.assign(n_nations, Nation{});
    ledgers.assign(n_nations, {});
    econ_reports.assign(n_nations, {});
    econ_intel.assign(n_nations, {});
    maps.assign(n_nations, {});
    pending_valuations.clear();
    gift_pending.clear();
    map_pending.clear();
    spy_pending.clear();
    loans.clear();
    bank_rate_v = 0.0;
    for (int n = 0; n < n_nations; n++) {
        nations[n].id = n;
        nations[n].alive = true;
        for (int r = 0; r < RES_N; r++) nations[n].res[r] = 0;
        nations[n].res[GOLD] = Num::START_GOLD;
        nations[n].res[FOOD] = Num::START_FOOD;
        nations[n].res[WOOD] = Num::START_WOOD;
        nations[n].res[ORE] = Num::START_ORE;
        nations[n].res[OIL] = Num::START_OIL;
        nations[n].res[EQUIP] = Num::START_EQUIP;
        nations[n].res[SUPPLY] = Num::START_SUPPLY;
    }
    std::vector<std::pair<int, int>> pts;
    if (!starts.empty()) pts = starts;
    else {
        double c = size / 2.0, r = size * 0.31;
        for (int i = 0; i < n_nations; i++) {
            double ang = -3.14159265358979323846 / 2 + i * 2 * 3.14159265358979323846 / n_nations;
            pts.push_back({(int)std::llround(c + r * std::cos(ang)),
                           (int)std::llround(c + r * std::sin(ang))});
        }
    }
    static const int CROSS[5][2] = {{0, 0}, {0, -1}, {0, 1}, {-1, 0}, {1, 0}};
    auto cross_ok = [&](int cx, int cy) {
        for (auto& d : CROSS) {
            int x = cx + d[0], y = cy + d[1];
            if (x < 0 || x >= size || y < 0 || y >= size) return false;
            int i = x * size + y;
            if (materialized[i]) return false;
            if (tiles[i].terrain == HIGH_MTN || tiles[i].terrain == OCEAN) return false;
        }
        return true;
    };
    auto find_safe = [&](int cx, int cy) {
        std::vector<std::tuple<int, int, int, int>> cand;
        for (int x = 2; x < size - 2; x++)
            for (int y = 2; y < size - 2; y++)
                cand.push_back({std::max(std::abs(x - cx), std::abs(y - cy)),
                                (x - cx) * (x - cx) + (y - cy) * (y - cy), x, y});
        std::sort(cand.begin(), cand.end());
        for (const auto& [distance, squared, x, y] : cand)
            if (cross_ok(x, y)) return std::pair<int, int>{x, y};
        return std::pair<int, int>{-1, -1};
    };
    for (int n = 0; n < n_nations && n < (int)pts.size(); n++) {
        int cx = pts[n].first, cy = pts[n].second;
        if (!cross_ok(cx, cy)) { auto s = find_safe(cx, cy); cx = s.first; cy = s.second; }
        for (auto& d : CROSS) {
            int x = cx + d[0], y = cy + d[1];
            if (x < 0 || x >= size || y < 0 || y >= size) continue;
            int i = x * size + y;
            Tile& t = tiles[i];
            t.owner = n;
            t.core = n;
            if (d[0] == 0 && d[1] == 0) {
                t.terrain = PLAIN;
                t.tres = roll_res(x, y, PLAIN);
                t.bld[TOWNHALL] = 1;
            }
            materialize(i);
        }
    }
    market_init();
    armies.clear();
    guard_once.clear();
    rng.seed((uint64_t)seed * 0x9E3779B97F4A7C15ULL + 12345);
}

bool World::visible_to(int n, int x, int y) const {
    const auto* bloc = bloc_of(n);
    auto allied_view = [&](int owner) {
        return owner == n || (bloc && std::count(bloc->members.begin(), bloc->members.end(), owner));
    };
    for (int tx = std::max(0, x - 1); tx <= std::min(size - 1, x + 1); ++tx)
        for (int ty = std::max(0, y - 1); ty <= std::min(size - 1, y + 1); ++ty)
            if (allied_view(owner_of(tx, ty))) return true;
    const int r = Num::B_VISION;
    for (int tx = std::max(0, x - r); tx <= std::min(size - 1, x + r); ++tx) {
        for (int ty = std::max(0, y - r); ty <= std::min(size - 1, y + r); ++ty) {
            if ((tx - x) * (tx - x) + (ty - y) * (ty - y) > r * r) continue;
            const auto& t = tiles[tx * size + ty];
            if (allied_view(t.owner) && t.bld[WATCHTOWER]) return true;
        }
    }
    return false;
}
