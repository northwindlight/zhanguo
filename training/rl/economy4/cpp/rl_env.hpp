#pragma once

#include "engine.hpp"

namespace rl {

enum class Type {
    EndTurn, Build, Demolish, Recruit, Buy, Sell, Move, Attack, Retreat, Disband,
    DeclareWar, OfferPeace, AcceptPeace, RejectPeace, Guarantee, ProposeDefense,
    AcceptProposal, RejectProposal, BlocFound, BlocJoin, BlocLeave, Vote,
    ProposeVassalage, SubmitVassalage, ReleaseVassal, IndependenceWar, Borrow, Gift,
    Spy, BuyReport, ShareMap, CancelGuarantee, BreakDefense,
    Count
};

constexpr int type_count = static_cast<int>(Type::Count);
constexpr int g_dim = 32, m_dim = 8, n_dim = 40, t_dim = 72, a_dim = 20, c_dim = 40;
constexpr int f_dim = 80, aux_dim = 8;
// A country may attempt at most this many game actions per turn. EndTurn is
// separate; exhausted observations contain only EndTurn, never a discarded action.
constexpr int game_action_budget = 60;
constexpr int budget_remaining_feature = 29, budget_used_feature = 30, budget_limit_feature = 31;

struct Candidate {
    Type type;
    // 与 Python 参数顺序相同，建筑、国家、兵种、商品、集团名称使用对应的编号。
    std::array<int, 3> args{};
    bool executable = true;
    std::array<float, c_dim> features{};
};

struct DiplomacyCache {
    std::array<int, 12> key{};
    std::vector<Candidate> value;
};

std::vector<Candidate> candidates(const World& w, int seat, bool diplomacy = true,
                                  DiplomacyCache* cache = nullptr);
bool apply(World& w, int seat, const Candidate& candidate);

struct Observation {
    std::array<float, g_dim> globals{};
    std::array<std::array<float, m_dim>, 6> market{};
    std::vector<std::array<float, n_dim>> nations;
    std::vector<std::array<float, t_dim>> tiles;
    std::vector<std::array<float, a_dim>> armies;
    std::vector<std::array<float, c_dim>> cand;
    std::vector<std::array<int32_t, 3>> cand_ptr;
    std::vector<float> cand_exec;
    std::vector<int32_t> cand_type;
};

struct EnemySighting {
    int owner, aid, x, y, kind;
    double hp;
};

class Observer {
public:
    Observation encode(const World& w, int seat, const std::vector<Candidate>& candidates);
    const std::vector<EnemySighting>& sight() const { return sight_; }

private:
    struct ReconMemory {
        int turn = -1;
        int source = -1;
        bool buildings = false;
        ReconTile tile;
    };
    struct KnownReport {
        int acquired;
        Report report;
    };
    void update_intelligence(const World& w, int seat);
    void encode_intelligence(const World& w, int seat, int target, std::array<float, n_dim>& row) const;
    struct TileMemory {
        int last_turn = -1;
        int terrain = 0;
        std::array<int, TR_N> resources{};
        bool hall = false;
        int owner_class = 0;
        double enemy_hp = 0;
    };
    std::vector<TileMemory> memory_;
    std::vector<EnemySighting> sight_;
    std::vector<std::optional<EconIntel>> spy_;
    std::vector<std::optional<KnownReport>> reports_;
    std::vector<int> map_turn_;
    std::vector<ReconMemory> recon_;
};

std::vector<char> visible_set(const World& w, int seat);
std::array<float, f_dim> turn_features(const Observation& o,
                                      const std::array<int, type_count>& previous,
                                      int invalid, int steps);
std::array<float, aux_dim> aggregate_lost(const std::vector<EnemySighting>& prev,
                                         const std::vector<EnemySighting>& cur, int size);
double power_score(const World& w, int seat);

}  // namespace rl
