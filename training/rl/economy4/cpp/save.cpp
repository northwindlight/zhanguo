// 存档：二进制序列化。
#include "engine.hpp"
#include <cstdio>

namespace {
struct W {
    FILE* f;
    template <class T> void w(const T& v) { std::fwrite(&v, sizeof(T), 1, f); }
    void s(const std::string& x) { uint32_t n = (uint32_t)x.size(); w(n); std::fwrite(x.data(), 1, n, f); }
};
struct R {
    FILE* f;
    template <class T> void r(T& v) { if (std::fread(&v, sizeof(T), 1, f) != 1) std::memset(&v, 0, sizeof(T)); }
    std::string s() { uint32_t n = 0; r(n); std::string x(n, '\0'); if (n) std::fread(&x[0], 1, n, f); return x; }
};
}

bool World::save(const std::string& path) const {
    if (deferred_orders) return false; // Dynamic queue snapshots require a versioned save format.
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    W w{f};
    w.w((uint32_t)0x4C525047);   // "ZGRL"
    w.w((uint32_t)2);
    w.w(size); w.w(seed); w.w(turn); w.w(max_turns);
    w.w((uint32_t)nations.size());
    for (auto& n : nations) {
        w.w(n.id); w.w((uint8_t)n.alive);
        for (int r = 0; r < RES_N; r++) w.w(n.res[r]);
        w.w(n.gdp_last); w.w(n.energy_out); w.w(n.energy_use);
        w.w((uint8_t)n.grid_short); w.w((uint8_t)n.built_diplomacy_center);
    }
    w.w((uint32_t)tiles.size());
    for (auto& t : tiles) {
        w.w(t.owner); w.w(t.core); w.w(t.terrain);
        for (int r = 0; r < TR_N; r++) w.w(t.tres[r]);
        for (int b = 0; b < BLD_N; b++) { w.w(t.bld[b]); w.w(t.pending[b]); }
        w.w(t.recruited); w.w(t.built); w.w(t.militia_rec);
        w.w((uint8_t)materialized[&t - tiles.data()]);
        for (auto r : t.map_resources) w.w(r);
        w.w(t.pending_gold);
    }
    w.w((uint32_t)tile_order.size());
    for (auto i : tile_order) w.w(i);
    w.w((uint32_t)armies.size());
    for (auto& a : armies) {
        w.w(a.id); w.w(a.gid); w.w(a.aid); w.w(a.owner); w.w(a.kind); w.w(a.hp);
        w.w(a.x); w.w(a.y); w.w(a.moved_turn); w.w((uint8_t)a.engaged); w.w(a.engage_seq);
        w.w((uint8_t)a.has_retreat); w.w(a.rx); w.w(a.ry);
    }
    w.w((uint32_t)guard_once.size());
    for (auto& p : guard_once) { w.w(p.first); w.w(p.second); }
    for (int g = 0; g < 6; g++) { w.w(minv[g]); w.w(mref[g]); w.w(price[g]); }
    w.w(mperiod);
    w.w((uint32_t)carry.size());
    for (auto& kv : carry) { w.w(kv.first); w.w(kv.second); }
    // 外交
    w.w((uint32_t)pacts.size()); for (auto& p : pacts) { w.w(p.kind); w.w(p.a); w.w(p.b); }
    w.w((uint32_t)wars.size());
    for (auto& x : wars) {
        w.w(x.id); w.w((uint32_t)x.atk_ents.size()); for (int e : x.atk_ents) w.w(e);
        w.w((uint32_t)x.def_ents.size()); for (int e : x.def_ents) w.w(e);
        w.w(x.turn);
    }
    w.w((uint32_t)truces.size()); for (auto& t : truces) { w.w(t.a); w.w(t.b); w.w(t.until); }
    w.w((uint32_t)blocs.size());
    for (auto& b : blocs) {
        w.w(b.id); w.s(b.name); w.w(b.chief); w.w(b.turn);
        w.w((uint32_t)b.members.size()); for (int m : b.members) w.w(m);
    }
    w.w((uint32_t)proposals.size());
    for (auto& p : proposals) { w.w(p.id); w.w(p.kind); w.w(p.a); w.w(p.b); w.w(p.asker); w.w(p.turn); }
    w.w((uint32_t)peace.size());
    for (auto& p : peace) { w.w(p.id); w.w(p.a); w.w(p.b); w.w(p.war_id); w.w(p.kind); w.w(p.gold); w.w(p.truce); }
    w.w((uint32_t)vassals.size());
    for (auto& kv : vassals) { w.w(kv.first); w.w(kv.second.overlord); w.w(kv.second.kind); w.w(kv.second.tribute); }
    w.w(next_war); w.w(next_bloc); w.w(next_vote); w.w(next_prop); w.w(next_peace);
    std::fclose(f);
    return true;
}

bool World::load(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    R r{f};
    uint32_t magic = 0, ver = 0;
    r.r(magic); r.r(ver);
    if (magic != 0x4C525047 || (ver != 1 && ver != 2)) { std::fclose(f); return false; }
    r.r(size); r.r(seed); r.r(turn); r.r(max_turns);
    uint32_t nn = 0; r.r(nn);
    nations.assign(nn, Nation{});
    ledgers.assign(nn, {});
    econ_reports.assign(nn, {});
    econ_intel.assign(nn, {});
    maps.assign(nn, {});
    for (uint32_t k = 0; k < nn; k++) {
        Nation& n = nations[k];
        r.r(n.id); uint8_t al = 0; r.r(al); n.alive = al;
        for (int q = 0; q < RES_N; q++) {
            if (ver == 1) { int v; r.r(v); n.res[q] = v; }
            else r.r(n.res[q]);
        }
        if (ver >= 2) {
            r.r(n.gdp_last); r.r(n.energy_out); r.r(n.energy_use);
            uint8_t short_, built;
            r.r(short_); r.r(built);
            n.grid_short = short_;
            n.built_diplomacy_center = built;
        }
    }
    uint32_t nt = 0; r.r(nt);
    tiles.assign(nt, Tile{});
    materialized.assign(nt, 0);
    tile_order.clear();
    for (uint32_t k = 0; k < nt; k++) {
        Tile& t = tiles[k];
        r.r(t.owner); r.r(t.core); r.r(t.terrain);
        for (int q = 0; q < TR_N; q++) r.r(t.tres[q]);
        for (int b = 0; b < BLD_N; b++) { r.r(t.bld[b]); r.r(t.pending[b]); }
        r.r(t.recruited); r.r(t.built); r.r(t.militia_rec);
        uint8_t m = 0; r.r(m); if (m) materialize(k);
        if (ver >= 2) {
            for (auto& v : t.map_resources) r.r(v);
            r.r(t.pending_gold);
        } else {
            t.map_resources = t.tres;
            if (t.owner >= 0 && t.bld[DIPLO]) nations[t.owner].built_diplomacy_center = true;
        }
    }
    if (ver >= 2) {
        uint32_t count = 0; r.r(count);
        tile_order.resize(count);
        for (auto& i : tile_order) r.r(i);
    }
    uint32_t na = 0; r.r(na);
    armies.assign(na, Army{});
    for (auto& a : armies) {
        r.r(a.id); r.r(a.gid); r.r(a.aid); r.r(a.owner); r.r(a.kind); r.r(a.hp);
        r.r(a.x); r.r(a.y); r.r(a.moved_turn);
        uint8_t e = 0; r.r(e); a.engaged = e;
        r.r(a.engage_seq);
        uint8_t hr = 0; r.r(hr); a.has_retreat = hr;
        r.r(a.rx); r.r(a.ry);
    }
    uint32_t ng = 0; r.r(ng);
    guard_once.assign(ng, {0, 0});
    for (auto& p : guard_once) { r.r(p.first); r.r(p.second); }
    for (int g = 0; g < 6; g++) { r.r(minv[g]); r.r(mref[g]); r.r(price[g]); }
    r.r(mperiod);
    uint32_t nc = 0; r.r(nc);
    carry.clear();
    for (uint32_t k = 0; k < nc; k++) { int key = 0; double v = 0; r.r(key); r.r(v); carry[key] = v; }
    uint32_t np = 0; r.r(np);
    pacts.assign(np, Pact{});
    for (auto& p : pacts) { r.r(p.kind); r.r(p.a); r.r(p.b); }
    uint32_t nw = 0; r.r(nw);
    wars.assign(nw, War{});
    for (auto& x : wars) {
        r.r(x.id);
        uint32_t k1 = 0; r.r(k1); x.atk_ents.assign(k1, 0); for (auto& e : x.atk_ents) r.r(e);
        uint32_t k2 = 0; r.r(k2); x.def_ents.assign(k2, 0); for (auto& e : x.def_ents) r.r(e);
        r.r(x.turn);
    }
    uint32_t ntr = 0; r.r(ntr);
    truces.assign(ntr, Truce{});
    for (auto& t : truces) { r.r(t.a); r.r(t.b); r.r(t.until); }
    uint32_t nb = 0; r.r(nb);
    blocs.assign(nb, Bloc{});
    for (auto& b : blocs) {
        r.r(b.id); b.name = r.s(); r.r(b.chief); r.r(b.turn);
        uint32_t k = 0; r.r(k); b.members.assign(k, 0); for (auto& m : b.members) r.r(m);
    }
    uint32_t npr = 0; r.r(npr);
    proposals.assign(npr, Proposal{});
    for (auto& p : proposals) { r.r(p.id); r.r(p.kind); r.r(p.a); r.r(p.b); r.r(p.asker); r.r(p.turn); }
    uint32_t npe = 0; r.r(npe);
    peace.assign(npe, PeaceOffer{});
    for (auto& p : peace) { r.r(p.id); r.r(p.a); r.r(p.b); r.r(p.war_id); r.r(p.kind); r.r(p.gold); r.r(p.truce); }
    uint32_t nv = 0; r.r(nv);
    vassals.clear();
    for (uint32_t k = 0; k < nv; k++) {
        int key = 0; Vassal v{};
        r.r(key); r.r(v.overlord); r.r(v.kind); r.r(v.tribute);
        vassals[key] = v;
    }
    r.r(next_war); r.r(next_bloc); r.r(next_vote); r.r(next_prop); r.r(next_peace);
    std::fclose(f);
    return true;
}
