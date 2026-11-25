// zhanguo RL engine — C++ core, zero deps, zero abstraction.
// 对齐 main 2026-11 规则语义：地图逐格、市场库存引擎、军队/战斗、外交/附庸/投票、结算顺序。
#pragma once
#include <array>
#include <cmath>
#include "pyrand.hpp"
#include <cstdint>
#include <cstring>
#include <string>
#include <unordered_map>
#include <vector>
#include <algorithm>
#include <numeric>
#include <optional>
#include <random>

// ---------------- 枚举 ----------------
enum Terrain { PLAIN = 0, FOREST, HILL, MOUNTAIN, DESERT, HIGH_MTN, OCEAN, TERRAIN_N };
enum Res { GOLD = 0, FOOD, WOOD, ORE, OIL, EQUIP, SUPPLY, RES_N };
enum TileRes { TR_ORE = 0, TR_GOLD, TR_FARM, TR_OIL, TR_WOOD, TR_N };
enum Bld { CASTLE = 0, LUMBER, FARM, MINE, OILWELL, GOLDMINE, WOODPOWER, OILPOWER,
           SUPPLYFAC, EQUIPFAC, BARRACKS, TOWNHALL, WATCHTOWER, DIPLO, ACADEMY, MILITIA,
           BLD_N };
enum Kind { INF = 0, CAV, MIL, KIND_N };

// ---------------- 数值表（balance.py 同步） ----------------
struct Num {
    // 单位：hp/speed/supply/atk + 征召粮/装/金
    static constexpr int U_HP[KIND_N]     = {100, 100, 80};
    static constexpr int U_SPEED[KIND_N]  = {1, 2, 1};
    static constexpr int U_SUPPLY[KIND_N] = {1, 2, 1};
    static constexpr int U_ATK[KIND_N]    = {50, 50, 25};
    static constexpr int U_REC_FOOD[KIND_N] = {10, 12, 5};
    static constexpr int U_REC_EQUIP[KIND_N] = {5, 12, 0};
    static constexpr int U_REC_GOLD[KIND_N] = {0, 0, 50};

    static constexpr int ARMY_MAX_HP = 100;
    static constexpr int STARVE = 35;          // 断供满额扣血
    static constexpr int HEAL_PER_TURN = 30;   // 每件装备 30HP 池
    static constexpr int RETREAT_RANGE = 1;
    static constexpr int RETREAT_PENALTY = 80; // 撤退输出 -80%
    static constexpr int RETREAT_DEF_COVER = 50;
    static constexpr int DIE_MOD[6] = {-25, -15, -5, 5, 15, 25};

    // 地形：defense / build_penalty
    static constexpr int T_DEF[TERRAIN_N] = {0, 10, 25, 50, -10, 0, 0};
    static constexpr int T_BUILDP[TERRAIN_N] = {0, 15, 25, 50, 40, 0, 0};
    static constexpr int T_WEIGHT[5] = {30, 25, 20, 15, 10};  // 平原/森林/丘陵/山地/沙漠

    // 建筑金本/木本/最大级/上限/最小槽位
    static constexpr int B_GOLD[BLD_N] = {50,20,20,30,60,85,60,120,90,105,175,250,60,200,150,110};
    static constexpr int B_WOOD[BLD_N] = {20,15,15,15,20,25,30,30,25,25,40,80,30,60,60,30};
    static constexpr int B_MAXLV[BLD_N] = {5,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1};
    static constexpr int B_SITE_LIMIT[BLD_N] = {0,0,0,0,0,0,0,0,0,0,0,1,0,1,1,1};
    static constexpr int B_CASTLE_COST[] = {50,100,200,400,800};
    static int build_base_cost(int b, int completed) {
        return b == CASTLE ? B_CASTLE_COST[std::clamp(completed, 0, 4)] : B_GOLD[b];
    }
    static constexpr int B_MIN_SLOTS[BLD_N] = {0,0,0,0,0,0,0,0,0,0,3,6,0,5,4,0};
    // 能源：产出/耗电
    static constexpr int B_ENERGY_OUT[BLD_N] = {0,0,0,0,0,0,2,8,0,0,0,0,0,0,0,0};
    static constexpr int B_ENERGY_USE[BLD_N] = {0,0,0,0,0,0,0,0,1,1,1,1,0,0,0,0};
    // 效果：defense_per_level / gold_base / gold_per_slot / recruit_cap / recruit_food /
    //       recruit_equipment / vision_radius / build_discount / militia_cap / militia_per_turn
    static constexpr int B_DEF_LV = 10;
    static constexpr int B_GOLD_BASE = 5, B_GOLD_SLOT = 1;
    static constexpr int B_RECRUIT_CAP = 1, B_REC_FOOD = 10, B_REC_EQUIP = 5;
    static constexpr int B_VISION = 4;
    static constexpr int B_DISCOUNT = 25;
    static constexpr int B_MIL_CAP = 2, B_MIL_TURN = 1;
    static constexpr int MAX_SLOTS = 20;

    // 市场：库存引擎
    static constexpr double M_BASE[6] = {1.5, 2.0, 2.5, 6.0, 8.0, 6.0};  // 粮木矿油装补
    static constexpr int    M_DEPTH[6] = {1920, 1920, 1280, 960, 640, 960};
    static constexpr double M_ELASTICITY = 1.0;
    static constexpr double M_FLOW = 0.25;
    static constexpr double M_REFILL = 0.0;
    static constexpr double M_SPREAD = 0.10;
    static constexpr double M_MINP = 1e-6, M_MAXP = 1e9;

    // 开局
    static constexpr int START_GOLD = 1500, START_FOOD = 20, START_WOOD = 200;
    static constexpr int START_ORE = 10, START_OIL = 0, START_EQUIP = 5, START_SUPPLY = 10;

    // 外交
    static constexpr int FEE = 10, FOUND_FEE = 100, FALL_TRUCE = 10;
    static constexpr int LETTER_BASE = 20, LETTER_ALLY = 10, LETTER_MIN = 5;
    static constexpr int SPY_COST = 100, SPY_TURNS = 3;
    static constexpr int TRIBUTE = 15;
    static constexpr int PLAN_MAX = 10, REPORT_EVERY = 10;

    // 移动力
    static int move_cost(Kind k, Terrain t) {
        static const int m[KIND_N][TERRAIN_N] = {
            {1,1,1,1,1,-1,-1},   // 步
            {1,2,1,2,1,-1,-1},   // 骑
            {1,1,1,1,1,-1,-1},   // 民
        };
        return m[k][t];
    }
};

// ---------------- 实体 ----------------
struct Tile {
    int owner = -1;
    int core = -1;
    int terrain = PLAIN;
    std::array<int, TR_N> tres{}, map_resources{};
    std::array<int, BLD_N> bld{};
    std::array<int, BLD_N> pending{};
    int pending_gold = 0;
    uint8_t recruited = 0, built = 0, militia_rec = 0;
};

struct Army {
    int id = 0;          // 国家内序列
    int gid = 0;
    int aid = 0;         // 野人：x*size+y+1
    int owner = -1;      // -1 = 野人
    int kind = INF;
    double hp = 100.0;
    int x = 0, y = 0;
    int moved_turn = -1;
    bool engaged = false;
    int engage_seq = 0;
    bool has_retreat = false;
    int retreat_cover = 100;   // 承伤比例：守方撤退 50、攻方 100（main）
    int rx = 0, ry = 0;
};

struct Nation {
    int id = 0;
    bool alive = true;
    std::array<double, RES_N> res{};
    double gdp_last = 0.0;
    int energy_out = 0, energy_use = 0;
    bool grid_short = false;
    bool built_diplomacy_center = false;
};

struct ReconTile {
    int cell = 0, terrain = 0, castle = 0, slots = 0;
    bool owned = false;
    std::array<int, BLD_N> built{};
    int pending = 0;
};

struct MapIntel {
    int source, turn;
    bool stolen;
    std::vector<ReconTile> tiles;
};

struct EconIntel {
    int source, turn, report_turn = 0;
    std::array<double, RES_N> resources{};
    int energy = 0, demand = 0;
    bool blackout = false;
    std::array<int, KIND_N> armies{};
    std::vector<ReconTile> tiles;
};

struct Report {
    int period_end, report_turn, period_start, span;
    double gdp, military, invest, assets, export_gold, import_gold, trade_ratio;
    int supply_eaten, supply_eaten_turn;
    std::optional<double> gdp_growth, military_ratio, invest_growth, assets_growth;
};

struct Ledger {
    int since = 0;
    double prod = 0, mid = 0, fuel = 0, gold = 0, supply = 0, heal = 0;
    double imports = 0, exports = 0, invest_gold = 0, invest_wood = 0;
};

struct War {
    int id = 0;
    std::vector<int> atk_ents, def_ents;   // 实体 id 集合（联盟=1000+id）
    int turn = 0;
};

struct Pact { int kind = 0; int a = -1, b = -1; };   // 0 保障(有向) 1 共同防御
struct Truce { int a = -1, b = -1, until = 0; };
struct Bloc { int id; std::string name; int chief = -1; std::vector<int> members; int turn; };
struct Vote { int id; int kind; int bloc; int proposer; int target; int payload; bool flag;
              std::unordered_map<int, int8_t> votes; int turn, deadline; };
struct Proposal { int id; int kind; int a, b; int asker; int turn;
                  std::string name; std::vector<int> invitees, accepted; };   // kind 4 = 创盟邀约
struct Vassal { int overlord; int kind; int tribute; };   // 0 ordinary
struct PeaceOffer {
    int turn = 0; int id, a, b, war_id, kind, gold, truce; };

struct World {
    int size = 80;
    uint64_t seed = 0;
    int turn = 0;
    int max_turns = 200;

    std::vector<Nation> nations;
    std::vector<Tile> tiles;                    // size*size
    std::vector<int> tile_order;                // Python 物化地块的插入顺序
    std::vector<char> materialized;             // 已物化
    std::vector<std::vector<double>> rmask;     // 资源蓝噪声 rank（5 类）
    std::vector<double> tmask;                  // 地形蓝噪声 rank
    std::vector<Army> armies;
    int next_engage_seq = 1;
    int next_army_id = 1;          // 全局唯一 id（删军后不复用）
    std::unordered_map<int, int> next_aid;   // 每国 aid 序号（不回收，与 main next_army_seq 一致）
    PyRandom pyrng;                // Python 兼容 RNG（战斗骰子逐位对齐）
    std::vector<std::pair<int,int>> guard_once; // 生过野人的格
    std::vector<std::array<int,2>> order;       // 国家顺序

    // 市场
    std::array<int, 6> minv{};
    std::array<double, 6> mref{}, price{};
    std::unordered_map<int,int64_t> mvolume;    // (owner*8+side*4+good) -> n
    std::unordered_map<int,double> carry;
    int mperiod = 0;
    struct Trade { int owner, good, side, n; long long gold; double exact; int period; };
    std::vector<Trade> mtrades;

    // 外交
    std::vector<Pact> pacts;
    std::vector<War> wars;
    std::vector<Truce> truces;
    std::vector<Bloc> blocs;
    std::vector<Vote> votes, approved_votes;
    std::vector<Proposal> proposals;
    std::vector<PeaceOffer> peace;
    std::unordered_map<int, Vassal> vassals;
    int next_war = 1, next_bloc = 1, next_vote = 1, next_prop = 1, next_peace = 1;

    // 经济账本
    std::array<int, RES_N> spend{};
    bool grid_short = false;
    int energy_out = 0, energy_use = 0;

    std::unordered_map<int, double> tribute_carry;
    bool tally_mode = false;   // 投票结算内部调用（不重复收外交手续费）
    bool no_fee = false;       // 队列结算内部执行（已预扣，不重复收费）
    struct DiploCmd { int actor = 0; std::string action; std::vector<std::string> args; int fee = 0; };
    std::vector<DiploCmd> diplo_q;   // 本回合提交的外交命令（生产后统一重验生效）
    struct TribOut { int s = 0, o = 0, r = 0, v = 0; };
    std::vector<TribOut> tribute_out;   // 生产期逐笔扣下的贡品，生产结束后划给宗主
    int tribute_take(int n, int r, int v);
    void tribute_flush();
    std::mt19937_64 rng;
    std::vector<std::string> logbuf;

    bool alive(int n) const { return n >= 0 && n < (int)nations.size() && nations[n].alive; }
    double res(int n, int r) const { return nations[n].res[r]; }
    void add_res(int n, int r, double v) { nations[n].res[r] += v; }
    Tile& tile(int x, int y) { return tiles[x * size + y]; }
    bool owned(int x, int y) const { return tiles[x * size + y].owner >= 0; }
    int owner_of(int x, int y) const { return tiles[x * size + y].owner; }
    bool in_bounds(int x, int y) const { return x >= 0 && y >= 0 && x < size && y < size; }

    // mapgen.cpp
    void materialize(int i) {
        if (materialized[i]) return;
        materialized[i] = true;
        tile_order.push_back(i);
    }
    void gen_map();
    void setup(int n_nations, const std::vector<std::pair<int, int>>& starts = {});
    std::array<int, TR_N> roll_res(int x, int y, Terrain t) const;

    // market.cpp
    void market_init();
    double market_level(int g) const;
    std::pair<double, double> market_walk(int g, int n, int side, int owner = -1) const;
    std::pair<int, double> market_cash(int owner, double exact, int side) const;
    bool buy(int n, int g, int q);
    bool sell(int n, int g, int q);
    void market_settle();

    // army.cpp
    void spawn_guardian(int x, int y);
    void ensure_guardian_at(int x, int y);
    std::vector<Army*> defs_at(int n, int x, int y);
    bool recruit(int n, int x, int y, int q = 1, int kind = INF);
    int recruit_count(int n, int x, int y, int q, int kind) const;
    void spawn_recruited(int n, int x, int y, int q, int kind);
    bool move_army(int n, int id, int x, int y);
    bool attack(int n, const std::vector<int>& ids, int x, int y);
    bool retreat(int n, int id, int x, int y);
    bool retreat_legal(int n, int x, int y) const;
    bool movement_blocked(int n, int x, int y) const;
    bool visible_to(int n, int x, int y) const;
    bool disband(int n, int id);
    std::vector<std::pair<std::pair<int,int>, int>> reachable(int n, const Army& a, bool for_attack = false) const;
    std::vector<std::pair<int, int>> resolve_battles();
    int claim_battle(int x, int y, const std::vector<int>& factions, bool all_occupants) const;
    void conquer(int x, int y, int by);
    void supply_phase();
    int supply_need(int n) const;
    std::vector<size_t> supply_consumers(int n) const;
    void withdraw_illegal();
    void clear_disengaged();

    // economy.cpp
    void production_phase();
    bool build(int n, int x, int y, int b);
    int build_price(const Tile& tile, int b) const;
    int build_cost(int n, int x, int y, int b) const;
    bool demolish(int n, int x, int y, int b);
    void completion_phase();
    void bank_settle();

    // diplo.cpp
    bool at_war(int a, int b) const;
    bool allied(int a, int b) const;
    const Bloc* bloc_of(int n) const;
    bool has_truce(int a, int b) const;
    int  entity(int n) const;             // 国家 -> 实体 id（联盟=1000+id）
    std::vector<int> entity_members(int ent) const;
    int  vassal_sovereign(int n) const;
    bool declare_war(int a, int b);
    bool peace_terms_ok(int a, int b, int kind, int gold) const;
    bool offer_peace(int a, int b, int kind, int gold, int truce);
    bool accept_peace(int me, int offer_id);
    bool reject_peace(int me, int offer_id);
    bool propose_pact(int kind, int a, int b);
    bool accept_pact(int me, int offer_id);
    bool reject_pact(int me, int offer_id);
    bool declare_guarantee(int a, int b);
    bool propose_vassalage(int overlord, int subject);
    bool accept_vassalage(int me, int offer_id);
    bool submit_vassalage(int subject, int overlord);
    bool release_vassal(int overlord, int subject);
    bool independence_war(int subject);
    bool propose_bloc(int a, const std::string& name, const std::vector<int>& invitees);
    bool bloc_join(int a, int bloc_id);
    bool accept_founding(int me, int id = -1);
    bool bloc_leave(int actor);
    bool bloc_transfer(int actor, int target);
    bool bloc_rename(int actor, const std::string& name);
    bool bloc_in(int actor) const;
    bool at_war_any(int n) const;
    bool bloc_chief(int actor) const;
    bool vote(int n, int vote_id, int choice);   // -1 弃权 0 反对 1 赞成
    void tally_votes();
    void execute_vote(const Vote& vote);
    void end_actions(int actor);
    void add_truce(int a, int b, int until);
    bool _diplo_authority(int n) const;

    // turn.cpp
    void resolve_turn();
    void begin_turn();
    void eliminate_if_dead(int n);
    void settle_deaths();
    void balance_cores(const std::vector<int>& nations_);
    void snapshot_cores(const std::vector<int>& ns);

    // bank.cpp
    bool bank_on = false;
    double bank_rate_v = 0.0;
    struct Loan { double principal = 0, due = 0; int turns_left = 0, taken_turn = 0; double rate = 0; };
    std::unordered_map<int, Loan> loans;
    bool bank_set_rate(double r);
    bool bank_loan(int n);
    bool bank_sell_report(int from, int to);
    int bank_credit(int n) const;
    double nation_gdp(int n) const;
    std::vector<std::vector<Report>> econ_reports;
    std::vector<Ledger> ledgers;
    struct Valuation { int nation, resource; double Ledger::*field; double amount, initial; };
    std::vector<Valuation> pending_valuations;
    Ledger& ledger(int n);
    void value_flow(int n, double Ledger::*field, int resource, double amount);
    void close_report_period(const std::vector<Ledger>& before);
    double nation_assets(int n) const;
    struct Gift { int from, to, good, amount, arrive; };
    struct Spy { int from, to, arrive; };
    struct MapDelivery { int to; MapIntel intel; };
    std::vector<Gift> gift_pending;
    std::vector<Spy> spy_pending;
    std::vector<MapDelivery> map_pending;
    std::vector<std::vector<EconIntel>> econ_intel;
    std::vector<std::vector<MapIntel>> maps;
    bool vassal_gated(int from, int to) const;
    MapIntel map_snapshot(int source, int arrival, bool stolen) const;
    EconIntel econ_snapshot(int source, int arrival) const;
    void deliver_intel();

    // diplomacy extras
    int diplo_fee(int n) const;
    int base_fee(int n, int target = -1, bool free_if_target_center = false) const;
    bool pay_fee(int n);
    // 外交手续费：返回 -1=现金不足(未扣)；0=免费；>0=已扣
    int diplo_charge(int n, int target, bool free_if_target_center, int fixed);
    static int diplo_priority(const std::string& action);
    bool dry_diplo(int actor, const std::string& nm, const std::vector<std::string>& a) const;
    bool dry_diplo_inner(int actor, const std::string& nm, const std::vector<std::string>& a) const;
    int queued_fee(int actor, const std::string& nm, const std::vector<std::string>& args) const;
    bool already_queued(int actor, const std::string& nm, const std::vector<std::string>& args) const;
    bool exec_diplo(int actor, const std::string& nm, const std::vector<std::string>& a);
    bool submit_diplo(int actor, const std::string& nm, const std::vector<std::string>& a);
    void resolve_diplomacy();
    bool entity_at_war(int ent) const;
    int nation_bld_count(int n, int b, bool include_pending = false) const;
    bool gift(int from, int to, int good, int amt);
    bool send_letter(int from, int to, int textlen);
    bool share_map(int from, int to);
    bool spy_send(int from, int to);

    // save.cpp
    bool save(const std::string& path) const;
    bool load(const std::string& path);

    // util
    void log(const std::string& s) { if (logbuf.size() < 512) logbuf.push_back(s); }
};

uint64_t blake2b8(const std::string& in);
std::vector<std::string> debug_pool(uint64_t seed, int size);

// replay.cpp
int run_replay(const std::string& script, const std::string& dump_path);
