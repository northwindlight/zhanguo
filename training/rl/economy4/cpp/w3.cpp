#include "w3.hpp"

#include "rl_env.hpp"
#include <deque>
#include <limits>
#include <ostream>
#include <tuple>

namespace rl {
namespace {

constexpr double epsilon = 1e-8;
constexpr int intel_refresh = 10;
constexpr int report_cost = 20;
constexpr int infinity = std::numeric_limits<int>::max();
constexpr int extractors[] = {LUMBER, FARM, MINE, OILWELL, GOLDMINE};
constexpr int cap_resource[] = {-1, TR_WOOD, TR_FARM, TR_ORE, TR_OIL, TR_GOLD,
                                -1, -1, -1, -1, -1, -1, -1, -1, -1, TR_FARM};
constexpr int extracted_good[] = {-1, WOOD, FOOD, ORE, OIL, GOLD};
// Python sorts equal-payback construction projects by their building names.
constexpr int extractor_name_order[] = {FARM, LUMBER, OILWELL, MINE, GOLDMINE};
constexpr int plants[] = {WOODPOWER, OILPOWER};
constexpr int factories[] = {SUPPLYFAC, EQUIPFAC};
using Stocks = std::array<double, RES_N>;

int used_slots(const Tile& tile) {
    return std::accumulate(tile.bld.begin(), tile.bld.end(), 0);
}

bool consumes_power(int building) {
    return building == SUPPLYFAC || building == EQUIPFAC || building == BARRACKS || building == TOWNHALL;
}

double reference(int good) {
    return good == GOLD ? 10.0 : Num::M_BASE[good - 1];
}

double army_value(int kind, double hp) {
    double base = Num::U_REC_FOOD[kind] * reference(FOOD) +
                  Num::U_REC_EQUIP[kind] * reference(EQUIP) + Num::U_REC_GOLD[kind];
    return base * hp / Num::U_HP[kind];
}

double stock_value(const Stocks& stock) {
    double value = 0;
    for (int good = FOOD; good < RES_N; ++good) value += stock[good] * reference(good);
    return value;
}

struct Forecast {
    double income = 0, supply = 0, equipment = 0;
    int power = 0, demand = 0;
};

struct Input {
    int good, quantity;
};

constexpr Input supply_inputs[] = {{FOOD, 2}, {ORE, 1}};
constexpr Input equipment_inputs[] = {{ORE, 1}, {OIL, 1}};

const Input* factory_inputs(int building) {
    return building == SUPPLYFAC ? supply_inputs : equipment_inputs;
}

Forecast forecast(const World& world, int seat, Stocks stock) {
    Forecast result;
    double output = 0, inputs = 0;
    int city_gold = 0;
    std::vector<std::pair<int, int>> running_plants, running_factories;
    for (int cell : world.tile_order) {
        const auto& tile = world.tiles[cell];
        if (tile.owner != seat) continue;
        for (int b : extractors) {
            const int count = tile.bld[b], good = extracted_good[b];
            if (good != GOLD) stock[good] += count;
            output += count * reference(good);
        }
        for (int b : plants) if (tile.bld[b]) running_plants.emplace_back(b, tile.bld[b]);
        for (int b : factories) if (tile.bld[b]) running_factories.emplace_back(b, tile.bld[b]);
        result.demand += tile.bld[SUPPLYFAC] + tile.bld[EQUIPFAC] + tile.bld[BARRACKS] + tile.bld[TOWNHALL];
        city_gold += tile.bld[TOWNHALL] * (Num::B_GOLD_BASE + (used_slots(tile) - tile.bld[TOWNHALL]) * Num::B_GOLD_SLOT);
    }
    auto consume = [&](const Input* needs, int count, int batches) {
        for (int i = 0; i < count; ++i)
            batches = std::min(batches, static_cast<int>(std::floor(stock[needs[i].good] / needs[i].quantity)));
        for (int i = 0; i < count; ++i) {
            const auto [good, quantity] = needs[i];
            stock[good] -= quantity * batches;
            inputs += quantity * batches * reference(good);
        }
        return batches;
    };
    for (const auto& [b, count] : running_plants) {
        const Input fuel{b == WOODPOWER ? WOOD : OIL, 1};
        result.power += consume(&fuel, 1, count) * Num::B_ENERGY_OUT[b];
    }
    if (result.power >= result.demand) {
        for (const auto& [b, count] : running_factories) {
            const int good = b == SUPPLYFAC ? SUPPLY : EQUIP;
            const int quantity = 2 * consume(factory_inputs(b), 2, count);
            stock[good] += quantity;
            output += quantity * reference(good);
        }
        output += city_gold;
    }
    result.income = output - inputs;
    result.supply = stock[SUPPLY];
    result.equipment = stock[EQUIP];
    return result;
}

class Turn {
public:
    Turn(World& world, int actor, std::ostream* trace, const W3Executor* executor, bool production_only)
        : w(world), seat(actor), limit(w.turn <= 50 ? 0.15 : 0.50),
          opening_stock(stock_value(w.nations[seat].res)), trace_(trace), executor_(executor),
          production_only_(production_only) {
        for (int i = 0; i < static_cast<int>(w.tiles.size()); ++i)
            if (w.tiles[i].owner == seat) own.push_back(i);
    }

    const Army* unit(int id) const {
        auto a = std::find_if(w.armies.begin(), w.armies.end(), [id](const Army& a) { return a.id == id; });
        return a == w.armies.end() ? nullptr : &*a;
    }

    bool move(int id, int site) {
        if (!can_deploy(id)) return false;
        const int aid = trace_ || executor_ ? unit(id)->aid : 0;
        const bool ok = executor_ ? (*executor_)({Type::Move, {aid, site / w.size, site % w.size}}, 0)
                                  : w.move_army(seat, id, site / w.size, site % w.size);
        return record(ok, "move", aid, site);
    }

    bool attack(const std::vector<int>& ids, int site) {
        if (production_only_ && std::any_of(ids.begin(), ids.end(), [&](int id) { return !can_deploy(id); }))
            return false;
        std::vector<int> aids;
        if (trace_) {
            for (int id : ids) aids.push_back(unit(id)->aid);
        }
        const bool ok = executor_ ? !ids.empty() &&
            (*executor_)({Type::Attack, {unit(ids.front())->aid, site / w.size, site % w.size}}, 0)
            : w.attack(seat, ids, site / w.size, site % w.size);
        if (trace_) {
            *trace_ << "attack " << ok << ' ' << site;
            for (int aid : aids) *trace_ << ' ' << aid;
            *trace_ << '\n';
        }
        return ok;
    }

    bool spy(int enemy) { return record(w.spy_send(seat, enemy), "spy", enemy); }
    bool report(int enemy, int report_turn) {
        return record(w.bank_sell_report(seat, enemy), "report", enemy, report_turn);
    }
    bool declare(int enemy) {
        return record(w.submit_diplo(seat, "declare_war", {std::to_string(enemy)}), "declare", enemy);
    }

    double res(int good) const { return w.res(seat, good); }
    Forecast predict() const { return forecast(w, seat, w.nations[seat].res); }
    int need() const { return w.supply_need(seat); }
    int count(int building, bool pending = false) const {
        return w.nation_bld_count(seat, building, pending);
    }
    int capital() const {
        for (int site : own) if (w.tiles[site].bld[TOWNHALL]) return site;
        return -1;
    }

    bool buy_to(int good, double minimum, double keep = 0) {
        const int missing = std::max(0, static_cast<int>(std::ceil(minimum - res(good) - epsilon)));
        if (!missing) return true;
        if (production_only_ && good == SUPPLY) return false;
        if (!production_only_ && buy_cost(good, missing) > res(GOLD) - keep) return false;
        const bool ok = executor_ ? (*executor_)({Type::Buy, {good - 1, missing}}, keep)
                                  : w.buy(seat, good - 1, missing);
        record(ok,"buy",good,missing);
        return ok&&(!production_only_||res(good)+epsilon>=minimum);
    }

    bool build(int site, int b, int keep = 0) {
        const auto& tile = w.tiles[site];
        const int used = used_slots(tile) + std::accumulate(tile.pending.begin(), tile.pending.end(), 0);
        const int already = tile.bld[b] + tile.pending[b];
        const int cap = cap_resource[b];
        if (tile.built || used >= Num::MAX_SLOTS || used < Num::B_MIN_SLOTS[b] ||
            (Num::B_SITE_LIMIT[b] > 0 && already >= Num::B_SITE_LIMIT[b]) ||
            (cap >= 0 && tile.tres[cap] <= already)) return false;
        const int cost = w.build_price(tile, b);
        if (cost < 0 || !buy_to(WOOD, Num::B_WOOD[b], cost + keep)) return false;
        const bool ok = executor_ ? (*executor_)({Type::Build, {site / w.size, site % w.size, b}}, 0)
                                  : w.build(seat, site / w.size, site % w.size, b);
        return record(ok, "build", site, b);
    }

    bool place(int b, const std::vector<int>& sites, int keep = 0) {
        for (int site : sites) {
            const auto& tile = w.tiles[site];
            const int used = used_slots(tile) + std::accumulate(tile.pending.begin(), tile.pending.end(), 0);
            if (tile.built || used < Num::B_MIN_SLOTS[b]) continue;
            if ((!consumes_power(b) || build_power(1, site)) && build(site, b, keep)) return true;
        }
        return false;
    }

    void stock_for_essentials(int rations) {
        std::array<int, RES_N> extraction{};
        for (int site : own)
            for (int b : extractors) extraction[extracted_good[b]] += w.tiles[site].bld[b];
        if(production_only_){
            std::array<int,RES_N> consumption{};
            consumption[WOOD]+=count(WOODPOWER);consumption[OIL]+=count(OILPOWER);
            for(int b:factories)for(int i=0;i<2;++i){
                const auto input=factory_inputs(b)[i];
                consumption[input.good]+=count(b)*input.quantity;
            }
            // After this settlement, retain two further rounds of net imported inputs.
            // Domestic extraction is credited every round, not treated as missing stock.
            for(int good:{WOOD,OIL,FOOD,ORE})
                buy_to(good,3*std::max(0,consumption[good]-extraction[good]));
            return;
        }
        for (int b : plants) {
            const int good = b == WOODPOWER ? WOOD : OIL;
            const int fuel = count(b);
            buy_to(good, (production_only_?2:1)*fuel - extraction[good]);
            extraction[good] -= fuel;
        }
        const double supply = predict().supply;
        if (supply < rations) buy_to(SUPPLY, res(SUPPLY) + rations - supply);
        if (w.at_war_any(seat)) buy_to(SUPPLY, rations);
        for (int b : factories) {
            const int available = count(b);
            const Input* needs = factory_inputs(b);
            for (int i = 0; i < 2; ++i) {
                const auto [good, quantity] = needs[i];
                buy_to(good, (production_only_?2:1)*available * quantity - extraction[good]);
                extraction[good] -= available * quantity;
            }
        }
    }

    void invest(int defend, int capital_tower, int frontier_tower) {
        const int home = capital();
        build_power(w.turn >= 48 || w.at_war_any(seat) ? 4 : 0, defend);
        if (production_only_ && 2 * count(SUPPLYFAC, true) < need() + 2)
            place(SUPPLYFAC, preferred_sites(SUPPLYFAC), 20);
        if (home >= 0 && w.turn <= 45 && !count(MILITIA, true)) {
            auto sites = preferred_sites(MILITIA);
            sites.insert(sites.begin(), home);
            place(MILITIA, sites, 40);
        }
        if (defend >= 0) {
            const auto& tile = w.tiles[defend];
            if (tile.bld[CASTLE] + tile.pending[CASTLE] < 3) build(defend, CASTLE, 60);
        }
        if (capital_tower >= 0 && !has_tower(capital_tower)) build(capital_tower, WATCHTOWER, 40);
        if (frontier_tower >= 0 && !has_tower(frontier_tower)) build(frontier_tower, WATCHTOWER, 40);
        const int barracks_goal = w.turn < 18 ? 1 : (w.turn < 51 ? 2 : 4);
        if (count(BARRACKS, true) < barracks_goal) {
            auto sites = sorted_sites([&](int p) {
                return std::tuple{-used_slots(w.tiles[p]), w.build_price(w.tiles[p], BARRACKS), p};
            });
            place(BARRACKS, sites, 45);
        }
        const int affordable_supply = static_cast<int>(std::floor(std::max(0.0, predict().income) * limit / reference(SUPPLY)));
        const int expected_supply = std::min(need() + 2, std::max(2, affordable_supply));
        if (2 * count(SUPPLYFAC, true) < expected_supply)
            place(SUPPLYFAC, preferred_sites(SUPPLYFAC), 45);
        if (w.turn >= 14 && count(EQUIPFAC, true) < (w.turn < 50 ? 2 : 4))
            place(EQUIPFAC, preferred_sites(EQUIPFAC), 60);
        if (w.turn >= 16 && count(TOWNHALL, true) < std::min(static_cast<int>(own.size()), 4)) {
            auto sites = sorted_sites([&](int p) { return std::pair{-used_slots(w.tiles[p]), p}; });
            sites.erase(std::remove_if(sites.begin(), sites.end(), [&](int p) { return w.tiles[p].bld[TOWNHALL]; }),
                        sites.end());
            place(TOWNHALL, sites, 115);
        }
        std::vector<std::tuple<double, int, int>> projects;
        for (int site : own) {
            const auto& tile = w.tiles[site];
            if (tile.built) continue;
            for (int order = 0; order < 5; ++order) {
                const int b = extractor_name_order[order];
                if (tile.tres[cap_resource[b]] <= tile.bld[b] + tile.pending[b]) continue;
                const double value = reference(extracted_good[b]) + (tile.bld[TOWNHALL] > 0);
                const double payback = (w.build_price(tile, b) + Num::B_WOOD[b] * reference(WOOD)) / value;
                projects.emplace_back(payback, site, order);
            }
        }
        std::sort(projects.begin(), projects.end());
        for (const auto& [payback, site, order] : projects)
            if (payback <= 36 || w.turn < 16)
                build(site, extractor_name_order[order], w.turn >= 37 ? 165 : (w.turn >= 12 ? 75 : 30));
    }

    void recruit(int kind, int maximum = -1) {
        if (kind == INF && w.nations[seat].grid_short) return;
        if (production_only_ && kind != MIL && 2 * count(SUPPLYFAC) < need() + Num::U_SUPPLY[kind]) return;
        const int b = kind == INF ? BARRACKS : MILITIA;
        const int cash = Num::U_REC_GOLD[kind];
        const Input materials[] = {{FOOD, Num::U_REC_FOOD[kind]}, {EQUIP, Num::U_REC_EQUIP[kind]}};
        for (int site : own) {
            if (maximum >= 0 && std::count_if(w.armies.begin(),w.armies.end(),[&](const Army& a) {
                    return a.owner==seat && a.kind==kind && a.hp>0;
                }) >= maximum) break;
            const auto& tile = w.tiles[site];
            if (!tile.bld[b]) continue;
            if (kind == INF && tile.recruited >= tile.bld[b]) continue;
            if (kind == MIL) {
                if (tile.militia_rec) continue;
                const int stationed = std::count_if(w.armies.begin(), w.armies.end(), [&](const Army& a) {
                    return a.owner == seat && a.kind == MIL && a.hp > 0 && a.x * w.size + a.y == site;
                });
                if (stationed >= tile.bld[b] * Num::B_MIL_CAP) continue;
            } else {
                const int future_need = need() + Num::U_SUPPLY[kind];
                double budget = limit * predict().income;
                if (w.at_war_any(seat) && w.turn <= 50) budget *= 0.9;
                if (future_need * reference(SUPPLY) > budget + epsilon) break;
                stock_for_essentials(future_need);
                if (predict().supply < future_need) break;
            }
            double buy_value = 0;
            for (auto [good, qty] : materials)
                if (qty) buy_value += buy_cost(good, std::max(0, static_cast<int>(std::ceil(qty - res(good)))));
            if (buy_value > res(GOLD) - cash) continue;
            bool supplied = true;
            for (auto [good, qty] : materials) {
                if (qty && !buy_to(good, qty, cash)) {
                    supplied = false;
                    break;
                }
            }
            if (!supplied) continue;
            Stocks remaining = w.nations[seat].res;
            for (auto [good, qty] : materials) remaining[good] -= qty;
            const auto prediction = forecast(w, seat, remaining);
            const int rations = need() + (kind == INF ? Num::U_SUPPLY[kind] : 0);
            if (prediction.supply >= rations && prediction.power >= prediction.demand &&
                rations * reference(SUPPLY) <= limit * prediction.income + epsilon)
                record(executor_ ? (*executor_)({Type::Recruit, {site / w.size, site % w.size, kind}}, 0)
                                 : w.recruit(seat, site / w.size, site % w.size, 1, kind), "recruit", site, kind);
        }
    }

    void trim() {
        const int rations = need();
        stock_for_essentials(rations);
        const auto power = predict();
        if (w.turn >= 2 && power.power < power.demand) shed_load(rations);
        for (int good : {OIL, WOOD, FOOD, ORE, EQUIP, SUPPLY}) {
            const Stocks stock = w.nations[seat].res;
            const auto old = forecast(w, seat, stock);
            double cap = 3 * std::max(0.0, old.income);
            if (w.turn >= 2 && w.turn <= 15) cap = std::max(cap, opening_stock);
            const double kept = (res(GOLD) > 500 ? 0.30 : 0.12) * cap;
            int desired = static_cast<int>(std::ceil(std::max(0.0, stock_value(stock) - kept) / reference(good) - epsilon));
            if (w.turn == 1 && good == WOOD) desired = std::max(0, std::min(desired, static_cast<int>(stock[good]) - 60));
            const double held = good == SUPPLY && w.at_war_any(seat) ? std::min<double>(rations, stock[good]) : 0;
            int low = 0, high = std::min(desired, static_cast<int>(stock[good] - held));
            while (low < high) {
                const int step = (low + high + 1) / 2;
                Stocks trial = stock;
                trial[good] -= step;
                const auto future = forecast(w, seat, trial);
                if (future.power >= std::min(old.power, old.demand) &&
                    future.supply >= std::min<double>(old.supply, rations) &&
                    future.income + epsilon >= old.income) low = step;
                else high = step - 1;
            }
            if (low) record(executor_ ? (*executor_)({Type::Sell, {good - 1, low}}, 0)
                                      : w.sell(seat, good - 1, low), "sell", good, low);
        }
    }

    World& w;
    int seat;
    double limit;
    std::vector<int> own;

    void economy_only() {
        auto ensure=[&](int b,int goal){
            for(int attempt=0;attempt<goal&&count(b,true)<goal;++attempt){
                bool built=false;
                for(int site:preferred_sites(b)){
                    const auto& tile=w.tiles[site];
                    const int cap=cap_resource[b];
                    if(tile.built||used_slots(tile)+std::accumulate(tile.pending.begin(),tile.pending.end(),0)>=Num::MAX_SLOTS||
                       (cap>=0&&tile.tres[cap]<=tile.bld[b]+tile.pending[b]))continue;
                    if(!buy_to(WOOD,Num::B_WOOD[b]+3*count(WOODPOWER),w.build_price(tile,b)))continue;
                    if(consumes_power(b)&&!build_power(1,site))continue;
                    if(build(site,b)){built=true;break;}
                }
                if(!built)break;
            }
        };
        // Sell finished goods and genuinely surplus inputs; retain construction
        // timber and three rounds of net fuel/industrial input requirements.
        auto trade_surplus=[&]{
            const int supply=count(SUPPLYFAC),equip=count(EQUIPFAC);
            const int keep[RES_N]={0,std::max(12,3*std::max(0,2*supply-count(FARM))),
                std::max(90,3*std::max(0,count(WOODPOWER)-count(LUMBER))),
                std::max(8,3*std::max(0,supply+equip-count(MINE))),
                std::max(4,3*std::max(0,count(OILPOWER)+equip-count(OILWELL))),8,8};
            for(int good:{SUPPLY,EQUIP,ORE,OIL,FOOD,WOOD}){
                int surplus=std::max(0,int(std::floor(res(good)-keep[good])));
                if(surplus)(*executor_)({Type::Sell,{good-1,surplus}},0);
            }
        };
        trade_surplus();stock_for_essentials(0);
        int oil=0;for(int site:own)oil+=w.tiles[site].tres[TR_OIL];
        ensure(LUMBER,std::max(2,count(WOODPOWER,true)));
        if(oil)ensure(OILWELL,std::min(oil,2));
        // Oil power is preferred with a domestic well. Otherwise use wood power.
        if(oil&&count(OILWELL)>0&&!count(OILPOWER,true))ensure(OILPOWER,1);
        build_power(0,-1);
        ensure(FARM,4);ensure(MINE,oil>1?3:2);
        if(count(FARM)>=2)ensure(SUPPLYFAC,2);
        if(oil>1&&count(OILWELL)>count(OILPOWER))ensure(EQUIPFAC,1);
        if(w.turn>=12&&count(SUPPLYFAC)>=2)ensure(TOWNHALL,2);
        // Reinvest after the production chain is running. Capacity shortfalls
        // are covered through real market purchases, without filtering maps.
        if(w.turn>=16&&res(GOLD)>250){
            ensure(LUMBER,4);ensure(FARM,6);ensure(MINE,4);
            if(count(FARM)>=6)ensure(SUPPLYFAC,3);
            if(oil>1)ensure(EQUIPFAC,2);
            ensure(TOWNHALL,3);
        }
        stock_for_essentials(0);trade_surplus();
    }

    bool has_tower(int site) const {
        const auto& tile = w.tiles[site];
        return tile.bld[WATCHTOWER] || tile.pending[WATCHTOWER];
    }

private:
    bool can_deploy(int id) const {
        if (!production_only_) return true;
        const auto* army = unit(id);
        int required = need();
        if (army && army->kind == MIL) {
            const auto consumers = w.supply_consumers(seat);
            const bool already_fed = std::any_of(consumers.begin(), consumers.end(),
                                                [&](size_t index) { return &w.armies[index] == army; });
            if (!already_fed) ++required;
        }
        const auto next = predict();
        return 2 * count(SUPPLYFAC) >= required && next.power >= next.demand && next.supply >= required;
    }

    template<class... Args>
    bool record(bool ok, const char* action, Args... args) {
        if (trace_) {
            *trace_ << action << ' ' << ok;
            ((*trace_ << ' ' << args), ...);
            *trace_ << '\n';
        }
        return ok;
    }

    template<class Key>
    std::vector<int> sorted_sites(Key key) const {
        auto sites = own;
        std::sort(sites.begin(), sites.end(), [&](int a, int b) { return key(a) < key(b); });
        return sites;
    }

    double buy_cost(int good, int quantity) const {
        if (!quantity) return 0;
        if (quantity > w.minv[good - 1]) return std::numeric_limits<double>::infinity();
        return w.market_cash(seat, w.market_walk(good - 1, quantity, 0, seat).second, 0).first;
    }

    std::pair<int, int> capacity() const {
        int installed = 0, demand = 0;
        for (int site : own) {
            const auto& tile = w.tiles[site];
            for (int b : plants) installed += (tile.bld[b] + tile.pending[b]) * Num::B_ENERGY_OUT[b];
            for (int b : {SUPPLYFAC, EQUIPFAC, BARRACKS, TOWNHALL}) demand += tile.bld[b] + tile.pending[b];
        }
        return {installed, demand};
    }

    bool build_power(int extra, int avoid) {
        const auto [installed, demand] = capacity();
        if (installed >= demand + extra) return true;
        const bool oil = demand + extra >= 5 && count(OILWELL, true) > count(OILPOWER, true);
        for (int b : {OILPOWER, WOODPOWER}) {
            if (b == OILPOWER && !oil) continue;
            auto sites = sorted_sites([&](int p) {
                return std::tuple{w.build_price(w.tiles[p], b), p == avoid, !w.tiles[p].bld[TOWNHALL], p};
            });
            for (int site : sites) {
                if (site == avoid || !build(site, b)) continue;
                const auto [after, usage] = capacity();
                return after >= usage + extra;
            }
        }
        return false;
    }

    std::vector<int> preferred_sites(int b) const {
        return sorted_sites([&](int p) {
            return std::tuple{w.build_price(w.tiles[p], b), -w.tiles[p].bld[TOWNHALL], p};
        });
    }

    void shed_load(int rations) {
        for (int b : {EQUIPFAC, BARRACKS, SUPPLYFAC}) {
            // A maneuver earlier this turn can capture new power consumers.
            for (int site = 0; site < static_cast<int>(w.tiles.size()); ++site) {
                const auto& tile = w.tiles[site];
                if (tile.owner != seat) continue;
                while (tile.bld[b] && !tile.pending[b]) {
                    const auto current = predict();
                    if (current.power >= current.demand) return;
                    if (b == SUPPLYFAC && res(SUPPLY) < rations) break;
                    const bool ok = executor_ ? (*executor_)({Type::Demolish, {site / w.size, site % w.size, b}}, 0)
                                              : w.demolish(seat, site / w.size, site % w.size, b);
                    if (!record(ok, "demolish", site, b)) break;
                }
            }
        }
    }

    double opening_stock;
    std::ostream* trace_;
    const W3Executor* executor_;
    bool production_only_;
};

void update_intelligence(const World& w, int seat, W3State& state, const std::vector<char>& visible) {
    state.known.resize(w.tiles.size());
    state.rivals.resize(w.nations.size());
    for (int i = 0; i < static_cast<int>(visible.size()); ++i) {
        if (!visible[i]) continue;
        auto& memory = state.known[i];
        const auto& tile = w.tiles[i];
        memory.terrain = tile.terrain;
        memory.owner = tile.owner;
        memory.hall_owner = tile.bld[TOWNHALL] ? tile.owner : -1;
    }
    for (const auto& intel : w.econ_intel[seat]) {
        auto& rival = state.rivals[intel.source];
        if (intel.report_turn || intel.turn <= rival.spy_arrived) continue;
        rival.spy_arrived = intel.turn;
        rival.strength = 0;
        for (int kind = 0; kind < KIND_N; ++kind)
            rival.strength += intel.armies[kind] * army_value(kind, Num::U_HP[kind]);
        for (const auto& tile : intel.tiles) {
            if (visible[tile.cell]) continue;
            auto& memory = state.known[tile.cell];
            memory.terrain = tile.terrain;
            memory.owner = intel.source;
            if (tile.built[TOWNHALL]) memory.hall_owner = intel.source;
        }
    }
    for (const auto& map : w.maps[seat]) {
        if (!map.stolen) continue;
        for (const auto& tile : map.tiles) {
            auto& memory = state.known[tile.cell];
            if (!tile.owned && memory.terrain < 0) memory.terrain = tile.terrain;
        }
    }
}

int distance(const World& w, int a, int b) {
    return std::abs(a / w.size - b / w.size) + std::abs(a % w.size - b % w.size);
}

int distance(const World& w, int a, const std::vector<int>& cells) {
    int best = infinity;
    for (int b : cells) best = std::min(best, distance(w, a, b));
    return best;
}

template<class Visit>
int neighbor(const World& w, int cell, Visit visit) {
    const int x = cell / w.size, y = cell % w.size;
    for (int i = x - 1; i <= x + 1; ++i) {
        for (int j = y - 1; j <= y + 1; ++j) {
            if ((i == x && j == y) || !w.in_bounds(i, j)) continue;
            int next = i * w.size + j;
            if (visit(next)) return next;
        }
    }
    return -1;
}

template<class Passable>
int next_step(const World& w, const W3State& state, int start, const std::vector<int>& goals,
              Passable passable, bool explore = false) {
    std::vector<char> goal(w.tiles.size());
    for (int cell : goals) goal[cell] = true;
    if (goal[start]) return -1;
    std::vector<char> seen(w.tiles.size());
    seen[start] = true;
    std::deque<std::pair<int, int>> queue{{start, -1}};
    while (!queue.empty()) {
        const auto [cell, first] = queue.front();
        queue.pop_front();
        const int found = neighbor(w, cell, [&](int dst) {
            const auto& known = state.known[dst];
            if (seen[dst] || !passable(known.owner) || known.blocked || known.terrain >= HIGH_MTN) return false;
            seen[dst] = true;
            const int hop = first < 0 ? dst : first;
            if (goal[dst]) return true;
            if (explore || known.terrain >= 0) queue.emplace_back(dst, hop);
            return false;
        });
        if (found >= 0) return first < 0 ? found : first;
    }
    return -1;
}

bool in(const std::vector<char>& nations, int seat) { return seat >= 0 && nations[seat]; }

int defense_site(const Turn& turn, const W3State& state, const std::vector<Army>& observed,
                 const std::vector<char>& enemies) {
    if (std::none_of(enemies.begin(), enemies.end(), [](char v) { return v; })) return -1;
    const auto& w = turn.w;
    std::vector<int> pressure;
    for (const auto& a : observed)
        if (in(enemies, a.owner)) pressure.push_back(a.x * w.size + a.y);
    if (pressure.empty()) {
        for (int i = 0; i < static_cast<int>(state.known.size()); ++i)
            if (in(enemies, state.known[i].owner)) pressure.push_back(i);
    }
    std::pair<int, int> best{infinity, infinity};
    for (int site : turn.own)
        if (w.tiles[site].bld[TOWNHALL]) best = std::min(best, std::pair{distance(w, site, pressure), site});
    return best.second == infinity ? -1 : best.second;
}

struct Target {
    int seat = -1, separation = infinity;
    double strength = 0, assets = 0;
    std::vector<int> land;
};

bool valid_tower_site(const Turn& turn, int site) {
    if (site < 0 || turn.w.tiles[site].owner != turn.seat) return false;
    const auto& tile = turn.w.tiles[site];
    return turn.has_tower(site) || used_slots(tile)
        + std::accumulate(tile.pending.begin(), tile.pending.end(), 0) < Num::MAX_SLOTS;
}

bool on_frontier(const Turn& turn, const W3State& state, int site) {
    return neighbor(turn.w, site, [&](int nb) {
        return state.known[nb].owner != turn.seat && state.known[nb].terrain < HIGH_MTN;
    }) >= 0;
}

int new_frontier_tower(const Turn& turn, const W3State& state, const std::vector<int>& land) {
    const auto& w = turn.w;
    std::pair<int, int> best{infinity, infinity};
    for (int site : turn.own) {
        if (site == state.capital || !valid_tower_site(turn, site)) continue;
        if (on_frontier(turn, state, site)) best = std::min(best, std::pair{distance(w, site, land), site});
    }
    return best.second == infinity ? -1 : best.second;
}

double public_assets(const Tile& tile) {
    double value = 50;
    for (int b : {CASTLE, TOWNHALL}) {
        const int count = tile.bld[b];
        value += count * Num::B_WOOD[b] * reference(WOOD);
        if (b == TOWNHALL) value += count * Num::B_GOLD[b];
        else for (int level = 0; level < count; ++level) value += Num::B_CASTLE_COST[level];
    }
    return value;
}

Target choose_target(const Turn& turn, const W3State& state, const std::vector<char>& visible,
                     const std::vector<Army>& observed) {
    const auto& w = turn.w;
    const int seat = turn.seat;
    if (std::any_of(w.diplo_q.begin(), w.diplo_q.end(), [&](const World::DiploCmd& command) {
        return command.actor == seat && command.action == "declare_war";
    })) return {};
    auto key = [](const Target& t) { return std::tuple{t.land.empty(), t.strength, t.separation, t.assets, t.seat}; };
    Target best;
    for (int n = 0; n < static_cast<int>(w.nations.size()); ++n) {
        if (!w.alive(n) || n == seat || w.at_war(seat, n) || w.has_truce(seat, n) || w.entity(seat) == w.entity(n))
            continue;
        Target rival;
        rival.seat = n;
        for (int i = 0; i < static_cast<int>(state.known.size()); ++i) {
            if (state.known[i].owner != n) continue;
            rival.land.push_back(i);
            if (visible[i]) rival.assets += public_assets(w.tiles[i]);
        }
        for (int home : turn.own) rival.separation = std::min(rival.separation, distance(w, home, rival.land));
        if (state.rivals[n].spy_arrived >= 0) rival.strength = state.rivals[n].strength;
        else {
            for (const auto& a : observed)
                if (a.owner == n) rival.strength += army_value(a.kind, a.hp);
            rival.strength *= 1.5;
        }
        if (best.seat < 0 || key(rival) < key(best)) best = std::move(rival);
    }
    return best;
}

std::vector<int> own_armies(const World& w, int seat) {
    std::vector<int> ids;
    for (const auto& a : w.armies)
        if (a.owner == seat && a.hp > 0) ids.push_back(a.id);
    return ids;
}

void war_maneuver(Turn& turn, W3State& state, const std::vector<Army>& observed,
                  const std::vector<char>& enemies, const std::vector<int>& infantry, int defend) {
    const auto& w = turn.w;
    std::vector<int> goals, borders, hostile;
    for (int cell = 0; cell < static_cast<int>(state.known.size()); ++cell) {
        const auto& info = state.known[cell];
        if (in(enemies, info.hall_owner)) goals.push_back(cell);
        if (in(enemies, info.owner)) borders.push_back(cell);
    }
    if (goals.empty()) goals = std::move(borders);
    for (const auto& a : observed)
        if (in(enemies, a.owner)) hostile.push_back(a.x * w.size + a.y);
    if (defend >= 0 && (distance(w, defend, hostile) <= 4 || goals.empty() || infantry.size() < 7)) {
        goals = {defend};
    }

    // Group order follows the oldest army in each occupied cell.
    std::vector<std::pair<int, std::vector<int>>> groups;
    for (int id : infantry) {
        const auto& a = *turn.unit(id);
        if (a.moved_turn == w.turn) continue;
        const int cell = a.x * w.size + a.y;
        auto group = std::find_if(groups.begin(), groups.end(), [cell](const auto& g) { return g.first == cell; });
        if (group == groups.end()) groups.emplace_back(cell, std::vector<int>{id});
        else group->second.push_back(id);
    }
    for (const auto& [cell, ids] : groups) {
        std::vector<int> engaged;
        for (int id : ids) if (turn.unit(id)->engaged) engaged.push_back(id);
        if (!engaged.empty()) {
            const int owner = state.known[cell].owner;
            if (owner < 0 || in(enemies, owner)) turn.attack(engaged, cell);
            continue;
        }
        const int dst = next_step(w, state, cell, goals, [&](int owner) {
            return owner < 0 || owner == turn.seat || in(enemies, owner);
        }, true);
        if (dst < 0) continue;
        const int owner = state.known[dst].owner;
        if (in(enemies, owner) || (owner != turn.seat && std::count(hostile.begin(), hostile.end(), dst))) {
            turn.attack(ids, dst);
        } else {
            for (int id : ids) {
                const auto& a = *turn.unit(id);
                const bool free_to_probe = a.moved_turn != w.turn && !a.engaged &&
                    !w.movement_blocked(turn.seat, dst / w.size, dst % w.size) &&
                    std::none_of(w.armies.begin(), w.armies.end(), [&](const Army& d) {
                        return d.engaged && d.x == a.x && d.y == a.y && w.at_war(turn.seat, d.owner);
                    });
                if (turn.move(id, dst)) continue;
                // The movement receipt may name impassable terrain only when the destination is visible.
                if (free_to_probe && w.visible_to(turn.seat, dst / w.size, dst % w.size) &&
                    w.tiles[dst].terrain >= HIGH_MTN) {
                    state.known[dst].blocked = true;
                    break;
                }
            }
        }
    }
}

double frontier_value(const Tile& tile) {
    const auto& r = tile.map_resources;
    return r[TR_GOLD] * reference(GOLD) + r[TR_OIL] * reference(OIL) + r[TR_WOOD] * reference(WOOD)
         + r[TR_ORE] * reference(ORE) + r[TR_FARM] * reference(FOOD);
}

void expand(Turn& turn, const W3State& state, const std::vector<int>& armies,
            const std::vector<int>& aim, size_t infantry_count) {
    const auto& w = turn.w;
    std::vector<int> frontier;
    if (w.turn >= 51 && turn.own.size() >= 18 && infantry_count < 10) return;
    for (int cell = 0; cell < static_cast<int>(state.known.size()); ++cell) {
        const auto& info = state.known[cell];
        if (info.owner >= 0 || info.terrain < 0 || info.terrain >= HIGH_MTN) continue;
        if (neighbor(w, cell, [&](int nb) { return state.known[nb].owner == turn.seat; }) >= 0)
            frontier.push_back(cell);
    }
    if (frontier.empty()) return;
    const int capital = turn.capital();
    const std::vector<int> home{capital >= 0 ? capital : turn.own.front()};
    const auto& destinations = aim.empty() ? home : aim;
    std::vector<char> taken(w.tiles.size());
    int mobilized = turn.need();
    for (int id : armies) {
        const Army a = *turn.unit(id);
        if (a.moved_turn == w.turn) continue;
        const int cell = a.x * w.size + a.y;
        const int owner = state.known[cell].owner;
        if (a.engaged) {
            if (owner < 0) turn.attack({id}, cell);
            continue;
        }
        const bool station = a.kind == MIL && owner == turn.seat && w.tiles[cell].bld[MILITIA];
        if (station) {
            if ((mobilized + 1) * reference(SUPPLY) > turn.limit * turn.predict().income + epsilon) continue;
            turn.stock_for_essentials(mobilized + 1);
            if (turn.predict().supply < mobilized + 1) continue;
        }
        auto key = [&](int site) {
            return std::tuple{distance(w, cell, site), -frontier_value(w.tiles[site]),
                              distance(w, site, destinations), site};
        };
        int dst = -1;
        for (int site : frontier)
            if (!taken[site] && (dst < 0 || key(site) < key(dst))) dst = site;
        if (dst < 0) break;
        const int step = next_step(w, state, cell, {dst}, [&](int owner) { return owner < 0 || owner == turn.seat; });
        if (step < 0) continue;
        bool ok;
        if (state.known[step].owner < 0) {
            std::vector<int> party;
            if (a.kind == INF) {
                for (int other : armies) {
                    const auto& d = *turn.unit(other);
                    if (d.kind == INF && d.moved_turn != w.turn && !d.engaged && d.x == a.x && d.y == a.y)
                        party.push_back(other);
                }
            } else party.push_back(id);
            ok = turn.attack(party, step);
        } else ok = turn.move(id, step);
        if (ok) {
            taken[step] = true;
            if (station) mobilized = turn.need();
        }
    }
}

}  // namespace

void script_economy_turn(World& w,int seat,const W3Executor& execute){
    Turn turn(w,seat,nullptr,&execute,true);turn.economy_only();
}

void w3_turn(World& w, int seat, W3State& state, std::ostream* trace, const W3Executor* executor) {
    if (!w.alive(seat)) return;
    Turn turn(w, seat, trace, executor, state.production_only);
    if (turn.own.empty()) return;
    const auto visible = visible_set(w, seat);
    update_intelligence(w, seat, state, visible);
    std::vector<Army> observed;
    for (const auto& a : w.armies)
        if (a.owner != seat && visible[a.x * w.size + a.y]) observed.push_back(a);
    if (w.turn > 1 && turn.res(GOLD) < 350) turn.trim();

    std::vector<char> enemies(w.nations.size()), incoming(w.nations.size());
    for (int n = 0; n < static_cast<int>(w.nations.size()); ++n) {
        enemies[n] = w.alive(n) && w.at_war(seat, n);
        auto& rival = state.rivals[n];
        rival.initiated_war &= enemies[n];
        incoming[n] = enemies[n] && !rival.initiated_war;
    }
    if (state.capital < 0 || w.tiles[state.capital].owner != seat || !w.tiles[state.capital].bld[TOWNHALL])
        state.capital = turn.capital();
    const int defend = defense_site(turn, state, observed, incoming);
    const auto target = choose_target(turn, state, visible, observed);
    if (!valid_tower_site(turn, state.frontier_tower) || state.frontier_tower == state.capital ||
        (target.seat >= 0 && state.tower_target != target.seat) ||
        (!turn.has_tower(state.frontier_tower) && !on_frontier(turn, state, state.frontier_tower))) {
        state.tower_target = -1;
        state.frontier_tower = -1;
    }
    const bool at_war = w.at_war_any(seat);
    turn.invest(defend, state.capital, at_war || target.seat < 0 ? -1 : state.frontier_tower);
    turn.stock_for_essentials(turn.need());
    turn.recruit(MIL);
    turn.recruit(INF);
    const auto armies = own_armies(w, seat);
    std::vector<int> infantry;
    double force = 0;
    for (int id : armies) {
        const auto& a = *turn.unit(id);
        if (a.kind == INF) infantry.push_back(id);
        force += army_value(a.kind, a.hp);
    }

    if (w.national_wars && target.seat >= 0 && !at_war && infantry.size() > 6) {
        const int enemy = target.seat;
        auto& rival = state.rivals[enemy];
        if (w.bank_on && turn.res(GOLD) >= report_cost && !w.econ_reports[enemy].empty()) {
            const int latest = w.econ_reports[enemy].back().report_turn;
            if (latest != rival.report_bought && turn.report(enemy, latest))
                rival.report_bought = latest;
        }
        if (w.turn - rival.spy_sent >= intel_refresh && turn.res(GOLD) >= Num::SPY_COST + 30 && turn.spy(enemy))
            rival.spy_sent = w.turn;
        const bool waiting = w.turn - rival.spy_sent < Num::SPY_TURNS;
        const bool stale = rival.spy_arrived >= 0 && w.turn - rival.spy_arrived >= intel_refresh;
        if (!waiting && !stale && target.separation <= 6 && force + epsilon >= 1.3 * target.strength) {
            if (state.tower_target != enemy) {
                state.tower_target = enemy;
                state.frontier_tower = new_frontier_tower(turn, state, target.land);
            }
            const int site = state.frontier_tower;
            const bool ready = site < 0 || turn.has_tower(site) || turn.build(site, WATCHTOWER, 40);
            if (ready && turn.declare(enemy)) rival.initiated_war = true;
        }
    }
    if (at_war) war_maneuver(turn, state, observed, enemies, infantry, defend);
    else expand(turn, state, armies, target.land, infantry.size());
    turn.trim();
}

namespace {
int industry_oil_capacity(const World& w,int seat) {
    int total=0;for(const auto& t:w.tiles)if(t.owner==seat)total+=t.tres[TR_OIL];return total;
}
std::vector<int> industry_infantry(const World& w,int seat) {
    std::vector<int> ids;for(const auto& a:w.armies)if(a.owner==seat&&a.kind==INF&&a.hp>0)ids.push_back(a.id);
    return ids;
}
int industry_heal_equipment(const World& w,int seat) {
    std::vector<double> damage;
    for(const auto& a:w.armies)if(a.owner==seat&&a.kind==INF&&a.hp>0)damage.push_back(100-a.hp);
    int required=0;
    for(int round=0;round<4;++round){
        double restored=0;
        for(auto& d:damage){const double h=std::min(30.0,d);restored+=h;d-=h;}
        required+=static_cast<int>(std::ceil(restored/30.0-1e-8));
    }
    return required;
}
bool industry_mines_ready(const World& w,int seat,bool allow_import) {
    int cap=0;for(const auto& t:w.tiles)if(t.owner==seat)cap+=t.tres[TR_ORE];
    return w.nation_bld_count(seat,MINE)>=2||(allow_import&&cap<2&&w.nation_bld_count(seat,MINE)>=cap);
}
bool industry_farms_ready(const World& w,int seat,bool allow_import=false){
    int cap=0;for(const auto& t:w.tiles)if(t.owner==seat)cap+=t.tres[TR_FARM];
    return w.nation_bld_count(seat,FARM)>=4||(allow_import&&cap<4&&w.nation_bld_count(seat,FARM)>=cap);
}
bool industry_base_ready(const World& w,int seat,bool allow_import=false,bool allow_capacity_import=false) {
    const auto next=forecast(w,seat,w.nations[seat].res);
    return industry_farms_ready(w,seat,allow_capacity_import)&&industry_mines_ready(w,seat,allow_import)&&
        w.nation_bld_count(seat,SUPPLYFAC)>=2&&w.nation_bld_count(seat,BARRACKS)>=1&&
        next.power>=next.demand&&w.nations[seat].supply_output_last>=w.supply_need(seat)&&
        w.nations[seat].supply_output_last>0;
}
bool industry_line_resources_ready(const World& w,int seat) {
    const auto& n=w.nations[seat];
    const auto count=[&](int b){return w.nation_bld_count(seat,b);};
    const int supply=count(SUPPLYFAC),equipment=count(EQUIPFAC);
    const int consume[RES_N]={0,2*supply,count(WOODPOWER),supply+equipment,count(OILPOWER)+equipment,0,0};
    const int extraction[RES_N]={0,count(FARM),count(LUMBER),count(MINE),count(OILWELL),0,0};
    const auto future=forecast(w,seat,n.res);
    if(n.res[GOLD]<0||n.grid_short||future.power<future.demand)return false;
    for(int good:{FOOD,WOOD,ORE,OIL})
        if(n.res[good]+2*extraction[good]+epsilon<2*consume[good])return false;
    if(n.supply_output_last<2*supply||future.supply+epsilon<w.supply_need(seat))return false;
    if(equipment&&n.equipment_output_last<2*equipment)return false;
    return true;
}
bool industry_resources_ready(const World& w,int seat) {
    return industry_line_resources_ready(w,seat)&&
        forecast(w,seat,w.nations[seat].res).equipment+epsilon>=industry_heal_equipment(w,seat);
}
bool industry_retire_wood_power(const World& w,int seat) {
    const auto count=[&](int b,bool pending=false){return w.nation_bld_count(seat,b,pending);};
    const int oil=count(OILPOWER);
    const int demand=count(SUPPLYFAC,true)+count(EQUIPFAC,true)+count(BARRACKS,true)+count(TOWNHALL,true);
    return oil>0&&count(WOODPOWER)>0&&8*oil>=demand&&
        w.res(seat,OIL)+2*count(OILWELL)+epsilon>=2*(oil+count(EQUIPFAC));
}
}

bool industry_takeover(World& w,int seat,IndustryTutorState& state) {
    if(state.last_observed_turn!=w.turn){
        state.last_observed_turn=w.turn;
        const auto& nation=w.nations[seat];
        const auto future=forecast(w,seat,nation.res);
        const bool equipment_operating=nation.equipment_output_last>=2&&!nation.grid_short&&
            w.nation_bld_count(seat,EQUIPFAC)>0&&future.power>=future.demand&&
            future.equipment-nation.res[EQUIP]>=2;
        state.equipment_good_turns=equipment_operating?
                                   state.equipment_good_turns+1:0;
        const int oil=industry_oil_capacity(w,seat);
        int farm_capacity=0,ore_capacity=0;
        for(const auto& tile:w.tiles)if(tile.owner==seat){farm_capacity+=tile.tres[TR_FARM];ore_capacity+=tile.tres[TR_ORE];}
        if(farm_capacity<4||ore_capacity<2)++state.blocked_resource_turns;
        if(!state.initialized){
            state.initialized=true;state.known_oil_capacity=oil;
            state.require_equipment=oil>std::max(1,w.nation_bld_count(seat,OILPOWER));
        }
        else if(oil>state.known_oil_capacity){
            state.require_equipment=true;
            if(state.equipment_good_turns<3&&!state.equipment_takeover){
                state.equipment_takeover=true;++state.equipment_takeovers;
            }
            state.known_oil_capacity=oil;
        }
        double hp=0;int count=0;bool all_full=true;
        for(const auto& a:w.armies)if(a.owner==seat&&a.kind==INF&&a.hp>0){
            hp+=a.hp;++count;all_full&=a.hp>=100-1e-8&&!a.engaged;
        }
        if(count&&hp/count<50&&!state.healing){state.healing=true;++state.heal_takeovers;}
        if(state.healing&&count>=3&&all_full)state.healing=false;
        if(state.equipment_takeover&&state.equipment_good_turns>=3)state.equipment_takeover=false;
        if(state.require_equipment&&state.equipment_good_turns<3&&!state.equipment_takeover){
            state.equipment_takeover=true;++state.equipment_takeovers;
        }
        const bool line_ready=industry_base_ready(w,seat,state.allow_ore_import,state.allow_capacity_import)&&
            industry_line_resources_ready(w,seat)&&(!state.require_equipment||state.equipment_good_turns>=3);
        state.repair_turns=line_ready?0:state.repair_turns+1;
        if(state.rebuilding&&line_ready)state.rebuilding=false;
        if(!state.setup_done&&industry_base_ready(w,seat,state.allow_ore_import,state.allow_capacity_import)&&industry_resources_ready(w,seat)&&count>=3&&
           (!state.require_equipment||state.equipment_good_turns>=3)){
            state.setup_done=true;state.setup_turn=w.turn;
        }
    }
    return !state.setup_done||state.equipment_takeover||state.healing||state.rebuilding||industry_retire_wood_power(w,seat)||
           !industry_base_ready(w,seat,state.allow_ore_import,state.allow_capacity_import)||!industry_resources_ready(w,seat)||
           industry_infantry(w,seat).size()<3;
}

void industry_turn(World& w,int seat,IndustryTutorState& state,bool economic_takeover,
                   const W3Executor& execute) {
    Turn t(w,seat,nullptr,&execute,true);
    t.limit=1.0;
    auto sites=t.own;
    std::sort(sites.begin(),sites.end(),[&](int a,int b){
        return std::pair{!w.tiles[a].bld[TOWNHALL],a}<std::pair{!w.tiles[b].bld[TOWNHALL],b};
    });
    auto ensure=[&](int building,int goal){
        for(int attempt=0;attempt<goal&&t.count(building,true)<goal;++attempt)
            if(!t.place(building,sites))break;
    };
    const int infantry=static_cast<int>(industry_infantry(w,seat).size());
    const int heal_stock=industry_heal_equipment(w,seat);
    if(economic_takeover&&industry_retire_wood_power(w,seat)){
        for(int site:sites)while(w.tiles[site].bld[WOODPOWER])
            if(!execute({Type::Demolish,{site/w.size,site%w.size,WOODPOWER}},0))break;
    }
    if(economic_takeover){
        // Raise construction cash from surplus only. Never liquidate the current
        // fuel, factory inputs, recruitment materials or healing reserve.
        const bool recruit_budget=industry_base_ready(w,seat,state.allow_ore_import,state.allow_capacity_import)&&
            industry_line_resources_ready(w,seat)&&(!state.require_equipment||state.equipment_good_turns>=3);
        const int keep[RES_N]={0, std::max(8,4*t.count(SUPPLYFAC))+(recruit_budget?10*std::max(0,3-infantry):0),
            std::max(45,2*t.count(WOODPOWER)),std::max(4,2*(t.count(SUPPLYFAC)+t.count(EQUIPFAC))),
            std::max(2,2*(t.count(OILPOWER)+t.count(EQUIPFAC))),
            recruit_budget?5*std::max(0,3-infantry)+heal_stock+3:0,std::max(6,2*t.need())};
        for(int good:{SUPPLY,EQUIP,OIL,ORE,FOOD,WOOD}){
            const int surplus=static_cast<int>(std::floor(t.res(good)-keep[good]));
            if(surplus>0)execute({Type::Sell,{good-1,surplus}},0);
        }
    }
    t.stock_for_essentials(t.need());
    const int oil_capacity=industry_oil_capacity(w,seat);
    const bool oil_power=oil_capacity>0;
    const int planned_equipment=(state.equipment_takeover||oil_capacity>std::max(1,t.count(OILPOWER,true)))?1:0;
    if(economic_takeover){
        if(t.res(WOOD)<45||t.count(LUMBER)<t.count(WOODPOWER,true))
            ensure(LUMBER,std::max(1,t.count(WOODPOWER,true)));
        if(oil_power)ensure(OILWELL,std::min(oil_capacity,std::max(1,t.count(OILPOWER,true))+planned_equipment));
        const int demand=std::max(2,t.count(SUPPLYFAC,true))+std::max(1,t.count(BARRACKS,true))+
                         std::max(planned_equipment,t.count(EQUIPFAC,true))+t.count(TOWNHALL,true);
        if(oil_power)ensure(OILPOWER,std::max(1,(demand+7)/8));
        else ensure(WOODPOWER,(demand+1)/2);
        ensure(FARM,4);ensure(MINE,2+planned_equipment);
        if(industry_farms_ready(w,seat,state.allow_capacity_import)&&industry_mines_ready(w,seat,state.allow_ore_import))ensure(SUPPLYFAC,2);
        if(t.count(SUPPLYFAC)>=2){
            ensure(BARRACKS,1);
            if(planned_equipment&&(t.count(OILWELL)>t.count(OILPOWER)||state.allow_capacity_import))ensure(EQUIPFAC,1);
        }
        t.stock_for_essentials(t.need());
    }
    const bool line_ready=industry_base_ready(w,seat,state.allow_ore_import,state.allow_capacity_import)&&
        industry_line_resources_ready(w,seat)&&(!state.require_equipment||state.equipment_good_turns>=3);
    if(!line_ready&&infantry){
        const auto next=t.predict();
        const double production=std::max(0.0,next.supply-t.res(SUPPLY));
        int supported=std::clamp(static_cast<int>(std::floor((t.res(SUPPLY)+2*production)/2)),0,infantry);
        // Try repairs first. Persistent construction/energy failures with little
        // cash make retaining three armies more expensive than rebuilding.
        bool pending_repair=false;
        for(const auto& tile:w.tiles)if(tile.owner==seat)
            for(int b:{FARM,MINE,LUMBER,OILWELL,WOODPOWER,OILPOWER,SUPPLYFAC,EQUIPFAC})
                pending_repair|=tile.pending[b]>0;
        const bool near_recovery=pending_repair||(next.power>=next.demand&&production>=t.need()&&
            (!state.require_equipment||next.equipment-t.res(EQUIP)>=2));
        if(state.repair_turns>=3&&t.res(GOLD)<90&&!near_recovery)supported=std::min(supported,1);
        if(supported<infantry){
            auto ids=industry_infantry(w,seat);
            std::sort(ids.begin(),ids.end(),[&](int a,int b){return t.unit(a)->hp<t.unit(b)->hp;});
            int kept=infantry;
            for(int id:ids){
                if(kept<=supported)break;
                const auto* army=t.unit(id);
                if(army&&!army->engaged&&execute({Type::Disband,{army->aid}},0)){
                    --kept;++state.disbands;state.rebuilding=true;
                }
            }
        }
    }
    if(!line_ready||state.rebuilding)return;
    // Production is already operating before cash is spent on equipment or troops.
    const int missing=std::max(0,3-static_cast<int>(industry_infantry(w,seat).size()));
    if(state.healing||missing)t.buy_to(EQUIP,5*missing+industry_heal_equipment(w,seat)+3);
    t.recruit(INF,3);
    if(state.healing||!industry_base_ready(w,seat,state.allow_ore_import,state.allow_capacity_import))return;
    auto ids=industry_infantry(w,seat);
    if(ids.size()<3)return;
    const auto visible=visible_set(w,seat);
    update_intelligence(w,seat,state.intelligence,visible);
    if(state.target>=0&&w.tiles[state.target].owner==seat)state.target=-1;
    if(state.target<0){
        int best_distance=infinity;
        for(int cell=0;cell<(int)w.tiles.size();++cell){
            const auto& known=state.intelligence.known[cell];
            if(known.terrain<0||known.terrain>=HIGH_MTN||known.owner>=0)continue;
            if(neighbor(w,cell,[&](int n){return state.intelligence.known[n].owner==seat;})<0)continue;
            int dist=0;for(int id:ids){const auto* a=t.unit(id);dist+=distance(w,a->x*w.size+a->y,cell);}
            if(state.target<0||std::tuple{dist,known.terrain,cell}<
                              std::tuple{best_distance,state.intelligence.known[state.target].terrain,state.target}){
                state.target=cell;best_distance=dist;
            }
        }
    }
    if(state.target<0)return;
    for(int id:ids){
        const auto* ptr=t.unit(id);if(!ptr)continue;const Army army=*ptr;
        if(army.moved_turn==w.turn||army.engaged)continue;
        const int current=army.x*w.size+army.y;
        const int step=next_step(w,state.intelligence,current,{state.target},
                                [&](int owner){return owner<0||owner==seat;});
        if(step<0)continue;
        if(state.intelligence.known[step].owner<0)t.attack({id},step);
        else t.move(id,step);
    }
}

}  // namespace rl
