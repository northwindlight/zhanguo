#include "rl_env.hpp"

namespace rl {
namespace {

Candidate candidate(Type type, std::array<int, 3> args = {}, bool ok = true,
                    float a = 0, float b = 0, float c = 0, float d = 0, int onehot = -1) {
    Candidate result{type, args, ok, {}};
    auto& f = result.features;
    f[0] = ok;
    f[1] = static_cast<float>(type) / type_count;
    f[2] = a;
    f[3] = b;
    f[4] = c;
    f[5] = d;
    if (onehot >= 0) f[6 + onehot] = 1;
    return result;
}

std::vector<std::string> bloc_args(int actor, int target) {
    return {"国" + std::to_string(actor) + "盟", std::to_string(target)};
}

const char* proposal_command(const Proposal& p, bool accept) {
    if (p.kind == 4) return accept ? "accept_founding" : "reject_founding";
    if (p.kind == 2 || p.kind == 3) return accept ? "accept_vassalage" : "reject_vassalage";
    return accept ? "accept_pact" : "reject_pact";
}

void append_intel(std::vector<Candidate>& out, const World& w, int seat) {
    const double gold = w.res(seat, GOLD);
    if (w.bank_on) {
        const int fee = w.base_fee(seat);
        const int credit = w.bank_credit(seat);
        out.push_back(candidate(Type::Borrow, {}, !w.loans.count(seat) && credit > 0 &&
            (!fee || gold >= fee), credit / 500.0f, fee / 10.0f));
    }
    int others = 0;
    for (int target = 0; target < static_cast<int>(w.nations.size()); ++target) {
        if (target == seat || !w.alive(target)) continue;
        const float index = others++ / 8.0f;
        const bool gated = w.vassal_gated(seat, target);
        const int pending = std::count_if(w.spy_pending.begin(), w.spy_pending.end(), [&](const World::Spy& s) {
            return s.from == seat && s.to == target;
        });
        out.push_back(candidate(Type::Spy, {target}, gold >= Num::SPY_COST && !gated,
            index, std::min(pending, 3) / 3.0f, Num::SPY_COST / 200.0f));
        if (w.bank_on)
            out.push_back(candidate(Type::BuyReport, {target}, gold >= 20 && !w.econ_reports[target].empty(),
                index, 20 / 200.0f));
        const int fee = w.base_fee(seat, target);
        out.push_back(candidate(Type::ShareMap, {target}, (!fee || gold >= fee) && !gated,
            index, fee / 10.0f));
        for (int amount : {20, 100})
            out.push_back(candidate(Type::Gift, {target, GOLD, amount}, gold >= amount + fee && !gated,
                index, amount / 200.0f, fee / 10.0f));
    }
}

std::vector<Candidate> diplomacy_candidates(const World& w, int seat) {
    std::vector<Candidate> out;
    int others = 0;
    for (int target = 0; target < static_cast<int>(w.nations.size()); ++target) {
        if (target == seat || !w.alive(target)) continue;
        const std::vector<std::string> args{std::to_string(target)};
        const float index = others++ / 8.0f;
        const bool at_war = w.at_war(seat, target);
        out.push_back(candidate(Type::DeclareWar, {target}, w.dry_diplo(seat, "declare_war", args), index, at_war));
        if (at_war) {
            for (int kind = 0; kind < 5; ++kind) {
                for (int gold : {0, 50, 200}) {
                    if (gold && kind != 1 && kind != 2) continue;
                    bool ok = w.dry_diplo(seat, "offer_peace", {args[0], std::to_string(kind), std::to_string(gold), "0"});
                    out.push_back(candidate(Type::OfferPeace, {target, kind, gold}, ok, index, kind / 5.0f, gold / 200.0f));
                }
            }
        }
        out.push_back(candidate(Type::Guarantee, {target}, w.dry_diplo(seat, "declare_guarantee", args), index));
        out.push_back(candidate(Type::ProposeDefense, {target}, w.dry_diplo(seat, "propose_pact", {args[0]}), index));
        out.push_back(candidate(Type::ProposeVassalage, {target}, w.dry_diplo(seat, "propose_vassalage", args), index));
        out.push_back(candidate(Type::SubmitVassalage, {target}, w.dry_diplo(seat, "submit_vassalage", args), index));
        const int us = w.entity(seat), them = w.entity(target);
        const bool guarantee = std::any_of(w.pacts.begin(), w.pacts.end(), [&](const Pact& p) {
            return p.kind == 0 && p.a == us && p.b == them;
        });
        if (guarantee)
            out.push_back(candidate(Type::CancelGuarantee, {target}, w.dry_diplo(seat, "cancel_guarantee", args), index));
        const bool defense = std::any_of(w.pacts.begin(), w.pacts.end(), [&](const Pact& p) {
            return p.kind == 1 && ((p.a == us && p.b == them) || (p.a == them && p.b == us));
        });
        if (defense)
            out.push_back(candidate(Type::BreakDefense, {target}, w.dry_diplo(seat, "break_defense", args), index));
        auto vassal = w.vassals.find(target);
        if (vassal != w.vassals.end() && vassal->second.overlord == seat)
            out.push_back(candidate(Type::ReleaseVassal, {target}, w.dry_diplo(seat, "release_vassal", args), index));
        out.push_back(candidate(Type::BlocFound, {target}, w.dry_diplo(seat, "propose_bloc", bloc_args(seat, target)), index));
    }
    for (const auto& p : w.peace) {
        if (p.b != seat) continue;
        const std::vector<std::string> args{std::to_string(p.id)};
        out.push_back(candidate(Type::AcceptPeace, {p.id}, w.dry_diplo(seat, "accept_peace", args), p.gold / 200.0f));
        out.push_back(candidate(Type::RejectPeace, {p.id}, w.dry_diplo(seat, "reject_peace", args)));
    }
    for (const auto& p : w.proposals) {
        const bool addressed = p.kind == 4 ? std::count(p.invitees.begin(), p.invitees.end(), seat) != 0
                             : p.kind == 2 || p.kind == 3 ? p.b == seat : p.b == w.entity(seat);
        if (!addressed) continue;
        const std::vector<std::string> args{std::to_string(p.id)};
        out.push_back(candidate(Type::AcceptProposal, {p.id}, w.dry_diplo(seat, proposal_command(p, true), args)));
        out.push_back(candidate(Type::RejectProposal, {p.id}, w.dry_diplo(seat, proposal_command(p, false), args)));
    }
    if (const Bloc* bloc = w.bloc_of(seat)) {
        for (const auto& v : w.votes) {
            if (v.bloc != bloc->id || v.deadline != w.turn) continue;
            for (int choice : {1, 0, -1})
                out.push_back(candidate(Type::Vote, {v.id, choice}, true, choice == 1, choice < 0 ? 0.5f : 0.0f));
        }
        out.push_back(candidate(Type::BlocLeave, {}, w.dry_diplo(seat, "bloc_leave", {})));
    }
    for (const auto& b : w.blocs)
        out.push_back(candidate(Type::BlocJoin, {b.id}, w.dry_diplo(seat, "bloc_join", {std::to_string(b.id)})));
    if (w.vassals.count(seat))
        out.push_back(candidate(Type::IndependenceWar, {}, w.dry_diplo(seat, "independence_war", {})));
    return out;
}

}  // namespace

std::vector<Candidate> candidates(const World& w, int seat, bool diplomacy, DiplomacyCache* cache) {
    std::vector<Candidate> out;
    out.reserve(160);
    out.push_back(candidate(Type::EndTurn));
    if (!w.alive(seat)) return out;

    const float size = w.size;
    const int halls = w.nation_bld_count(seat, TOWNHALL);
    for (int i = 0; i < static_cast<int>(w.tiles.size()); ++i) {
        const Tile& t = w.tiles[i];
        if (t.owner != seat) continue;
        const int x = i / w.size, y = i % w.size;
        const float xn = x / size, yn = y / size;
        const int buildings = std::accumulate(t.bld.begin(), t.bld.end(), 0);
        for (int b = 0; b < BLD_N; ++b) {
            auto c = candidate(Type::Build, {x, y, b}, w.build_cost(seat, x, y, b) >= 0, xn, yn,
                               Num::build_base_cost(b, t.bld[b]) / 200.0f, Num::B_WOOD[b] / 100.0f, b);
            for (int r = 0; r < TR_N; ++r) c.features[22 + r] = t.tres[r] / 5.0f;
            c.features[27] = buildings / 20.0f;
            out.push_back(c);
        }
        for (int b = 0; b < BLD_N; ++b) {
            if ((!t.bld[b] && !t.pending[b]) || (b == TOWNHALL && halls <= 1)) continue;
            out.push_back(candidate(Type::Demolish, {x, y, b}, true, xn, yn, 0, 0, b));
        }
        for (int k = 0; k < KIND_N; ++k)
            out.push_back(candidate(Type::Recruit, {x, y, k}, w.recruit_count(seat, x, y, 1, k) > 0, xn, yn, 0, 0, k));
    }
    for (int good = 0; good < 6; ++good) {
        float price = std::round(w.price[good] * 10) / 100;
        const auto stock = w.res(seat, good + 1);
        for (int qty : {1, 5, 20, 60}) {
            const double exact = w.market_walk(good, qty, 0, seat).second;
            const int cash = w.market_cash(seat, exact, 0).first;
            const bool can_buy = w.res(seat, GOLD) >= cash && w.minv[good] >= qty;
            out.push_back(candidate(Type::Buy, {good, qty}, can_buy, qty / 60.0f, price, cash / 300.0f, 0, good));
            out.push_back(candidate(Type::Sell, {good, qty}, stock >= qty, qty / 60.0f, price, stock / 100.0f, 0, good));
        }
    }
    int army_count = 0;
    for (const auto& a : w.armies) {
        if (a.owner != seat || a.hp <= 0) continue;
        ++army_count;
        const float x = a.x / size, y = a.y / size, hp = a.hp / Num::U_HP[a.kind];
        const bool moved = a.moved_turn == w.turn;
        if (a.engaged) {
            for (int dx = -1; dx <= 1; ++dx) {
                for (int dy = -1; dy <= 1; ++dy) {
                    int nx = a.x + dx, ny = a.y + dy;
                    if ((dx || dy) && w.in_bounds(nx, ny))
                        out.push_back(candidate(Type::Retreat, {a.aid, nx, ny}, !moved && w.retreat_legal(seat, nx, ny),
                                                x, y, hp, (dx + 1) / 2.0f));
                }
            }
        } else if (!moved) {
            for (const auto& entry : w.reachable(seat, a, true)) {
                auto [nx, ny] = entry.first;
                if (nx == a.x && ny == a.y) continue;
                int owner = w.owner_of(nx, ny);
                bool mine = owner == seat || (owner >= 0 && w.allied(seat, owner));
                float d = (std::abs(nx - a.x) + std::abs(ny - a.y)) / 2.0f;
                if (!mine) out.push_back(candidate(Type::Attack, {a.aid, nx, ny}, true, x, y, hp, d));
                if (mine || owner < 0)
                    out.push_back(candidate(Type::Move, {a.aid, nx, ny}, !w.movement_blocked(seat, nx, ny), x, y, hp, d));
            }
        }
        out.push_back(candidate(Type::Disband, {a.aid}, !a.engaged, x, y, hp));
    }

    if (diplomacy) append_intel(out, w, seat);
    if (diplomacy && cache) {
        const std::array<int, 12> key{w.turn, seat, static_cast<int>(w.diplo_q.size()),
            static_cast<int>(w.proposals.size()), static_cast<int>(w.peace.size()),
            static_cast<int>(w.votes.size()), static_cast<int>(w.blocs.size()),
            static_cast<int>(w.wars.size()), static_cast<int>(w.vassals.size()),
            std::min(static_cast<int>(w.res(seat, GOLD)), Num::FEE),
            w.res(seat, GOLD) >= Num::FOUND_FEE, army_count > 0};
        if (cache->value.empty() || cache->key != key) {
            cache->key = key;
            cache->value = diplomacy_candidates(w, seat);
        }
        out.insert(out.end(), cache->value.begin(), cache->value.end());
    } else if (diplomacy) {
        const auto rest = diplomacy_candidates(w, seat);
        out.insert(out.end(), rest.begin(), rest.end());
    }
    return out;
}

bool apply(World& w, int seat, const Candidate& c) {
    const auto [a, b, d] = c.args;
    auto submit = [&](const std::string& name, const std::vector<std::string>& args = {}) {
        return w.submit_diplo(seat, name, args);
    };
    switch (c.type) {
    case Type::EndTurn: return true;
    case Type::Build: return w.build(seat, a, b, d);
    case Type::Demolish: return w.demolish(seat, a, b, d);
    case Type::Recruit: return w.recruit(seat, a, b, 1, d);
    case Type::Buy: return w.buy(seat, a, b);
    case Type::Sell: return w.sell(seat, a, b);
    case Type::DeclareWar: return submit("declare_war", {std::to_string(a)});
    case Type::OfferPeace: return submit("offer_peace", {std::to_string(a), std::to_string(b), std::to_string(d), "0"});
    case Type::AcceptPeace: return submit("accept_peace", {std::to_string(a)});
    case Type::RejectPeace: return submit("reject_peace", {std::to_string(a)});
    case Type::Guarantee: return submit("declare_guarantee", {std::to_string(a)});
    case Type::ProposeDefense: return submit("propose_pact", {std::to_string(a)});
    case Type::AcceptProposal:
    case Type::RejectProposal: {
        auto p = std::find_if(w.proposals.begin(), w.proposals.end(), [&](const Proposal& p) { return p.id == a; });
        if (p == w.proposals.end()) return false;
        return submit(proposal_command(*p, c.type == Type::AcceptProposal), {std::to_string(a)});
    }
    case Type::BlocFound: return submit("propose_bloc", bloc_args(seat, a));
    case Type::BlocJoin: return submit("bloc_join", {std::to_string(a)});
    case Type::BlocLeave: return submit("bloc_leave");
    case Type::Vote: return w.vote(seat, a, b);
    case Type::ProposeVassalage: return submit("propose_vassalage", {std::to_string(a)});
    case Type::SubmitVassalage: return submit("submit_vassalage", {std::to_string(a)});
    case Type::ReleaseVassal: return submit("release_vassal", {std::to_string(a)});
    case Type::IndependenceWar: return submit("independence_war");
    case Type::Borrow: return w.bank_loan(seat);
    case Type::Gift: return w.gift(seat, a, b, d);
    case Type::Spy: return w.spy_send(seat, a);
    case Type::BuyReport: return w.bank_sell_report(seat, a);
    case Type::ShareMap: return w.share_map(seat, a);
    case Type::CancelGuarantee: return submit("cancel_guarantee", {std::to_string(a)});
    case Type::BreakDefense: return submit("break_defense", {std::to_string(a)});
    case Type::Move:
    case Type::Attack:
    case Type::Retreat:
    case Type::Disband: {
        auto army = std::find_if(w.armies.begin(), w.armies.end(), [&](const Army& unit) {
            return unit.owner == seat && unit.aid == a && unit.hp > 0;
        });
        if (army == w.armies.end()) return false;
        if (c.type == Type::Move) return w.move_army(seat, army->id, b, d);
        if (c.type == Type::Retreat) return w.retreat(seat, army->id, b, d);
        if (c.type == Type::Disband) return w.disband(seat, army->id);
        std::vector<int> group;
        for (const auto& unit : w.armies)
            if (unit.owner == seat && unit.hp > 0 && unit.x == army->x && unit.y == army->y
                && unit.moved_turn != w.turn && !unit.engaged)
                group.push_back(unit.id);
        if (group.empty()) group.push_back(army->id);
        return w.attack(seat, group, b, d);
    }
    default: return false;
    }
}

}  // namespace rl
