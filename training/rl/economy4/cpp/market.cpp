// 市场：库存引擎。p = base * exp(elasticity*(ref-inv)/ref)，单内逐件几何级数。
#include "engine.hpp"

static const int MK[6] = {FOOD, WOOD, ORE, OIL, EQUIP, SUPPLY};

void World::market_init() {
    for (int g = 0; g < 6; g++) {
        minv[g] = Num::M_DEPTH[g];
        mref[g] = Num::M_DEPTH[g];
        price[g] = Num::M_BASE[g];
    }
}

double World::market_level(int g) const {
    double ref = mref[g];
    double gap = (ref - minv[g]) / ref;
    double p = std::exp(std::log(Num::M_BASE[g]) + Num::M_ELASTICITY * gap);
    return std::min(std::max(p, Num::M_MINP), Num::M_MAXP);
}

std::pair<double, double> World::market_walk(int g, int n, int side, int owner) const {
    double p = price[g];
    double ref = mref[g];
    long long key = (long long)owner * 16 + side * 8 + g;
    int vol = 0;
    auto it = mvolume.find((int)key);
    if (it != mvolume.end()) vol = (int)it->second;
    double step = Num::M_ELASTICITY / ref;
    double total;
    if (step == 0) {
        double f = side == 0 ? 1 + Num::M_SPREAD / 2 : 1 - Num::M_SPREAD / 2;
        total = p * f * n;
    } else if (side == 0) {  // buy
        double up = std::expm1(step);
        double units = std::exp(Num::M_ELASTICITY * vol / ref) *
                       std::expm1(Num::M_ELASTICITY * n / ref) / up;
        total = p * units * (1 + Num::M_SPREAD / 2);
    } else {  // sell
        double down = -std::expm1(-step);
        double units = std::exp(-Num::M_ELASTICITY * vol / ref) *
                       -std::expm1(-Num::M_ELASTICITY * n / ref) / down;
        total = p * units * (1 - Num::M_SPREAD / 2);
    }
    return {total / n, total};
}

std::pair<int, double> World::market_cash(int owner, double exact, int side) const {
    double carry_v = 0;
    auto it = carry.find(owner);
    if (it != carry.end()) carry_v = it->second;
    double total, rem;
    long long gold;
    if (side == 1) {   // sell: floor；余数 = total - gold（正的小数）
        total = exact + carry_v;
        gold = (long long)std::floor(total + 1e-9);
        rem = total - gold;
    } else {           // buy: ceil；余数 = gold - total
        total = exact - carry_v;
        gold = (long long)std::max(0.0, std::ceil(total - 1e-9));
        rem = gold - total;
    }
    return {(int)gold, (rem < 1e-9) ? 0.0 : rem};
}

bool World::buy(int n, int g, int q) {
    if (g < 0 || g >= 6 || q <= 0) return false;
    int r = MK[g];
    auto [unit, exact] = market_walk(g, q, 0, n);
    auto [gold, carry_after] = market_cash(n, exact, 0);
    if (res(n, GOLD) < gold) return false;
    carry[n] = carry_after;
    add_res(n, GOLD, -gold);
    add_res(n, r, q);
    long long key = (long long)n * 16 + 0 * 8 + g;
    mvolume[(int)key] += q;
    mtrades.push_back({n, g, 0, q, gold, exact, mperiod});
    ledger(n).imports += gold;
    return true;
}

bool World::sell(int n, int g, int q) {
    if (g < 0 || g >= 6 || q <= 0) return false;
    int r = MK[g];
    if (res(n, r) < q) return false;
    auto [unit, exact] = market_walk(g, q, 1, n);
    auto [gold, carry_after] = market_cash(n, exact, 1);
    carry[n] = carry_after;
    add_res(n, r, -q);
    add_res(n, GOLD, gold);
    long long key = (long long)n * 16 + 1 * 8 + g;
    mvolume[(int)key] += q;
    mtrades.push_back({n, g, 1, q, gold, exact, mperiod});
    ledger(n).exports += gold;
    return true;
}

void World::market_settle() {
    int netbuy[6] = {0, 0, 0, 0, 0, 0};
    for (auto& t : mtrades) netbuy[t.good] += (t.side == 0 ? t.n : -t.n);
    for (int g = 0; g < 6; g++) {
        minv[g] -= netbuy[g];
        mref[g] = std::max(1.0, mref[g] + Num::M_FLOW * netbuy[g]);
        if (Num::M_REFILL != 0) {
            double gap = mref[g] - minv[g];
            if (gap > 0) minv[g] += (int)std::min(std::ceil(gap), std::ceil(gap * Num::M_REFILL));
        }
        price[g] = market_level(g);
    }
    mtrades.clear();
    mvolume.clear();
    mperiod++;
}
