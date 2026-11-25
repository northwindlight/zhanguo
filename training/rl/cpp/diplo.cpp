// 外交：实体、战争/和平、条约、附庸、联盟、投票（简化自洽版）。
#include "engine.hpp"
#include <cstdio>
#include <cstdlib>

static const int BLOC_OFF = 1000;

int World::vassal_sovereign(int n) const {
    int cur = n, guard = 0;
    while (vassals.count(cur) && guard++ < 8) cur = vassals.at(cur).overlord;
    return cur;
}

const Bloc* World::bloc_of(int n) const {
    for (const auto& b : blocs)
        if (std::find(b.members.begin(), b.members.end(), n) != b.members.end()) return &b;
    return nullptr;
}

int World::entity(int n) const {
    int sovereign = vassal_sovereign(n);
    if (const Bloc* b = bloc_of(sovereign)) return BLOC_OFF + b->id;
    return sovereign;
}

std::vector<int> World::entity_members(int ent) const {
    std::vector<int> out;
    for (int m = 0; m < (int)nations.size(); m++)
        if (alive(m) && entity(m) == ent) out.push_back(m);
    return out;
}

bool World::allied(int a, int b) const {
    if (a < 0 || b < 0) return false;
    if (a == b) return true;
    return entity(a) == entity(b);
}

bool World::at_war(int a, int b) const {
    if (a < 0 || b < 0 || a == b) return false;
    int ea = entity(a), eb = entity(b);
    if (ea == eb) return false;
    for (auto& w : wars) {
        bool a_atk = std::find(w.atk_ents.begin(), w.atk_ents.end(), ea) != w.atk_ents.end();
        bool a_def = std::find(w.def_ents.begin(), w.def_ents.end(), ea) != w.def_ents.end();
        bool b_atk = std::find(w.atk_ents.begin(), w.atk_ents.end(), eb) != w.atk_ents.end();
        bool b_def = std::find(w.def_ents.begin(), w.def_ents.end(), eb) != w.def_ents.end();
        if ((a_atk && b_def) || (a_def && b_atk)) return true;
    }
    return false;
}

bool World::_diplo_authority(int n) const { return vassals.count(n) == 0; }

bool World::has_truce(int a, int b) const {
    int ea = entity(a), eb = entity(b);
    if (ea == eb) return false;
    return std::any_of(truces.begin(), truces.end(), [&](const Truce& t) {
        return t.until > turn && ((t.a == ea && t.b == eb) || (t.a == eb && t.b == ea));
    });
}

static void set_truce(World& w, int a, int b, int until) {
    int ea = w.entity(a), eb = w.entity(b);
    if (ea == eb) return;
    for (auto& t : w.truces) {
        if ((t.a == ea && t.b == eb) || (t.a == eb && t.b == ea)) {
            t.until = std::max(t.until, until);
            return;
        }
    }
    w.truces.push_back({ea, eb, until});
}

void World::add_truce(int a, int b, int until) {
    set_truce(*this, a, b, until);
}

bool World::declare_war(int a, int b) {
    int fee = tally_mode ? 0 : diplo_charge(a, b, false, -1);
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (a == b || !alive(a) || !alive(b) || !_diplo_authority(a)) return false;
        if (at_war(a, b)) return false;
        if (has_truce(a, b)) return false;
        int ea = entity(a), eb = entity(b);
        if (ea == eb) return false;
        // main：联盟成员宣战 → 先开联盟表决（T 发起、T+1 计票，赞成 > 反对才开战；未投算反对）
        if (!tally_mode && bloc_in(a)) {
            for (auto& bl : blocs)
                if (std::find(bl.members.begin(), bl.members.end(), a) != bl.members.end()) {
                    Vote v;
                    v.id = next_vote++; v.kind = 0; v.bloc = bl.id; v.proposer = a; v.target = b;
                    v.payload = 0; v.flag = false; v.turn = turn; v.deadline = turn + 1;
                    votes.push_back(v);
                    return true;
                }
        }
        // A real declaration invalidates all pending treaties and admission ballots.
        proposals.clear();
        votes.erase(std::remove_if(votes.begin(), votes.end(), [](const Vote& vote) {
            return vote.kind == 1;
        }), votes.end());
        War w;
        w.id = next_war++; w.turn = turn;
        w.atk_ents.push_back(ea);
        w.def_ents.push_back(eb);
        // 守侧传导（一跳）：对原防守实体的直接保障/共防
        for (auto& p : pacts) {
            bool obligation = (p.kind == 0 && p.b == eb) || (p.kind == 1 && (p.a == eb || p.b == eb));
            if (!obligation) continue;
            int other = (p.a == eb) ? p.b : p.a;
            if (other != ea && std::find(w.def_ents.begin(), w.def_ents.end(), other) == w.def_ents.end())
                w.def_ents.push_back(other);
        }
        // 攻守之间的条约作废
        pacts.erase(std::remove_if(pacts.begin(), pacts.end(), [&](const Pact& p) {
            bool a_side = (p.a == ea || p.b == ea);
            bool b_side = (p.a == eb || p.b == eb);
            return a_side && b_side;
        }), pacts.end());
        wars.push_back(w);
        return true;
    }();
    if (!ok && fee > 0) add_res(a, GOLD, fee);
    return ok;
}

// 议和 kind：0 白和 1 付款 2 索款 3 称臣(submit) 4 索臣(subjugate)。
// 称臣/索臣：不带金钱赔款；双方不能已是附庸；须先结束其他战线（main `_validate_submission(peace=True)`）。
bool World::peace_terms_ok(int a, int b, int kind, int gold) const {
    if (kind < 0 || kind > 4) return false;
    if (kind == 0) return true;
    if (kind < 3) return gold > 0;
    if (gold != 0) return false;
    int overlord = (kind == 3) ? b : a, subject = (kind == 3) ? a : b;
    if (subject == overlord || vassals.count(subject) || vassals.count(overlord)) return false;
    int ea = entity(a), eb = entity(b), this_war = -1;
    for (auto& w : wars) {
        bool a_atk = std::find(w.atk_ents.begin(), w.atk_ents.end(), ea) != w.atk_ents.end();
        bool b_atk = std::find(w.atk_ents.begin(), w.atk_ents.end(), eb) != w.atk_ents.end();
        bool a_def = std::find(w.def_ents.begin(), w.def_ents.end(), ea) != w.def_ents.end();
        bool b_def = std::find(w.def_ents.begin(), w.def_ents.end(), eb) != w.def_ents.end();
        if ((a_atk && b_def) || (a_def && b_atk)) { this_war = w.id; break; }
    }
    for (auto& w : wars) {
        if (w.id == this_war) continue;
        for (int e : {ea, eb})
            if (std::find(w.atk_ents.begin(), w.atk_ents.end(), e) != w.atk_ents.end() ||
                std::find(w.def_ents.begin(), w.def_ents.end(), e) != w.def_ents.end()) return false;
    }
    return true;
}

bool World::offer_peace(int a, int b, int kind, int gold, int truce) {
    int fee = diplo_charge(a, b, true, -1);   // 目标有外交中心免费
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (a == b || !alive(a) || !alive(b) || !at_war(a, b) || !_diplo_authority(a)) return false;
        if (!peace_terms_ok(a, b, kind, gold)) return false;
        PeaceOffer o;
        o.id = next_prop++; o.a = a; o.b = b; o.kind = kind; o.gold = gold; o.truce = truce; o.war_id = -1; o.turn = turn;
        int ea = entity(a), eb = entity(b);
        for (auto& w : wars) {
            bool a_atk = std::find(w.atk_ents.begin(), w.atk_ents.end(), ea) != w.atk_ents.end();
            bool b_atk = std::find(w.atk_ents.begin(), w.atk_ents.end(), eb) != w.atk_ents.end();
            if (a_atk != b_atk) { o.war_id = w.id; break; }
        }
        peace.push_back(o);
        return true;
    }();
    if (!ok && fee > 0) add_res(a, GOLD, fee);
    return ok;
}

bool World::accept_peace(int me, int offer_id) {
    for (size_t k = 0; k < peace.size(); k++) {
        if (peace[k].id != offer_id) continue;
        PeaceOffer o = peace[k];
        if (o.b != me && o.a != me) return false;
        peace.erase(peace.begin() + k);
        int payer = (o.kind == 1) ? o.a : (o.kind == 2 ? o.b : -1);
        if (payer >= 0) {
            if (res(payer, GOLD) < o.gold) return false;
            add_res(payer, GOLD, -o.gold);
            add_res(payer == o.a ? o.b : o.a, GOLD, o.gold);
        }
        wars.erase(std::remove_if(wars.begin(), wars.end(),
                                  [&](const War& w) { return w.id == o.war_id; }), wars.end());
        if (o.kind >= 3) {                       // 称臣/索臣：和约建立附庸；称臣方退出原联盟
            int overlord = (o.kind == 3) ? o.b : o.a, subject = (o.kind == 3) ? o.a : o.b;
            for (size_t bi = 0; bi < blocs.size(); bi++) {
                auto& bl = blocs[bi];
                auto it = std::find(bl.members.begin(), bl.members.end(), subject);
                if (it == bl.members.end()) continue;
                bl.members.erase(it);
                if (bl.members.size() < 2) blocs.erase(blocs.begin() + bi);
                break;
            }
            vassals[subject] = {overlord, 0, Num::TRIBUTE};
        }
        if (o.truce > 0)
            for (int m : entity_members(entity(o.a)))
                for (int nn : entity_members(entity(o.b))) set_truce(*this, m, nn, turn + o.truce);
        for (auto& aa : armies)
            if (aa.owner >= 0 && tiles[aa.x * size + aa.y].owner >= 0) aa.engaged = false;
        return true;
    }
    return false;
}

bool World::reject_peace(int me, int offer_id) {
    for (size_t k = 0; k < peace.size(); k++)
        if (peace[k].id == offer_id) { peace.erase(peace.begin() + k); return true; }
    return false;
}

bool World::declare_guarantee(int a, int b) {
    int fee = diplo_charge(a, b, false, -1);
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (a == b || !alive(a) || !alive(b) || !_diplo_authority(a) || at_war(a, b)) return false;
        pacts.push_back({0, entity(a), entity(b)});
        return true;
    }();
    if (!ok && fee > 0) add_res(a, GOLD, fee);
    return ok;
}

bool World::propose_pact(int kind, int a, int b) {
    int fee = diplo_charge(a, b, true, -1);   // 目标有外交中心免费
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (a == b || kind != 1 || !alive(a) || !alive(b) || !_diplo_authority(a) || at_war(a, b)) return false;
        int ea = entity(a), eb = entity(b);
        if (ea == eb) return false;
        for (auto& p : pacts)
            if (p.kind == 1 && ((p.a == ea && p.b == eb) || (p.a == eb && p.b == ea))) return false;
        Proposal p;
        p.id = next_prop++; p.kind = 1; p.a = ea; p.b = eb; p.asker = a; p.turn = turn;
        proposals.push_back(p);
        return true;
    }();
    if (!ok && fee > 0) add_res(a, GOLD, fee);
    return ok;
}

bool World::accept_pact(int me, int offer_id) {
    for (size_t k = 0; k < proposals.size(); k++) {
        if (proposals[k].id != offer_id || proposals[k].kind != 1) continue;
        Proposal p = proposals[k];
        if (entity(me) != p.b) return false;
        proposals.erase(proposals.begin() + k);
        pacts.push_back({1, p.a, p.b});
        return true;
    }
    return false;
}

bool World::reject_pact(int me, int offer_id) {
    for (size_t k = 0; k < proposals.size(); k++)
        if (proposals[k].id == offer_id && entity(me) == proposals[k].b) {
            proposals.erase(proposals.begin() + k);
            return true;
        }
    return false;
}

bool World::propose_vassalage(int overlord, int subject) {
    int fee = diplo_charge(overlord, subject, false, -1);
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (overlord == subject || !alive(overlord) || !alive(subject)) return false;
        if (!_diplo_authority(overlord) || vassals.count(subject) || vassals.count(overlord)) return false;
        if (at_war(overlord, subject) || bloc_in(subject)) return false;
        for (const auto& p : proposals)
            if (p.kind == 2 && p.a == overlord && p.b == subject) return false;
        if (entity_at_war(entity(overlord)) || entity_at_war(entity(subject))) return false;   // 战时冻结
        Proposal p;
        p.id = next_prop++; p.kind = 2; p.a = overlord; p.b = subject; p.asker = overlord; p.turn = turn;
        proposals.push_back(p);
        return true;
    }();
    if (!ok && fee > 0) add_res(overlord, GOLD, fee);
    return ok;
}

bool World::accept_vassalage(int me, int offer_id) {
    for (size_t k = 0; k < proposals.size(); k++) {
        if (proposals[k].id != offer_id) continue;
        Proposal p = proposals[k];
        int subject = -1, overlord = -1;
        if (p.kind == 2) { subject = p.b; overlord = p.a; if (p.b != me) continue; }       // 宗主邀请，接受者是将来的附庸
        else if (p.kind == 3) { subject = p.a; overlord = p.b; if (p.b != me) continue; }  // 主动称臣，接受者是宗主
        else continue;
        proposals.erase(proposals.begin() + k);
        if (subject < 0 || !alive(subject) || !alive(overlord)) return false;
        if (vassals.count(subject) || vassals.count(overlord)) return false;
        if (at_war(subject, overlord)) return false;
        if (entity_at_war(entity(overlord)) || entity_at_war(entity(subject))) return false;   // 战时冻结
        vassals[subject] = {overlord, 0, Num::TRIBUTE};
        return true;
    }
    return false;
}

bool World::submit_vassalage(int subject, int overlord) {
    int fee = diplo_charge(subject, overlord, false, -1);
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (subject == overlord || !alive(subject) || !alive(overlord) || bloc_in(subject)) return false;
        for (const auto& p : proposals)
            if (p.kind == 3 && p.a == subject && p.b == overlord) return false;
        if (vassals.count(subject) || vassals.count(overlord) || at_war(subject, overlord)) return false;
        if (entity_at_war(entity(overlord)) || entity_at_war(entity(subject))) return false;   // 战时冻结
        // main：称臣也是 "递邀约、宗主 accept" —— 不再直接成立
        Proposal p;
        p.id = next_prop++; p.kind = 3; p.a = subject; p.b = overlord; p.asker = subject; p.turn = turn;
        proposals.push_back(p);
        return true;
    }();
    if (!ok && fee > 0) add_res(subject, GOLD, fee);
    return ok;
}

bool World::release_vassal(int overlord, int subject) {
    auto it = vassals.find(subject);
    if (it == vassals.end() || it->second.overlord != overlord) return false;
    vassals.erase(it);
    return true;
}

bool World::independence_war(int subject) {
    int fee = diplo_charge(subject, -1, false, -1);
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        auto it = vassals.find(subject);
        if (it == vassals.end()) return false;
        int overlord = it->second.overlord;
        vassals.erase(it);
        if (!declare_war(subject, overlord)) { vassals[subject] = {overlord, 0, Num::TRIBUTE}; return false; }
        return true;
    }();
    if (!ok && fee > 0) add_res(subject, GOLD, fee);
    return ok;
}

bool World::propose_bloc(int a, const std::string& name, const std::vector<int>& invitees) {
    int fee = diplo_charge(a, -1, false, 100);   // main：创盟固定 100 金，发布后即使被拒也不退
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (!alive(a) || !_diplo_authority(a) || bloc_in(a) || at_war(a, a)) return false;
        Proposal p;
        p.id = next_prop++; p.kind = 4; p.a = a; p.b = -1; p.asker = a; p.turn = turn; p.name = name;
        for (int m : invitees)
            if (m != a && alive(m) && !vassals.count(m) && !bloc_in(m) &&
                std::find(p.invitees.begin(), p.invitees.end(), m) == p.invitees.end())
                p.invitees.push_back(m);
        if (p.invitees.empty()) return false;
        proposals.push_back(p);
        return true;
    }();
    if (!ok && fee > 0) add_res(a, GOLD, fee);
    return ok;
}

// 受邀国接受创盟邀约：所有受邀国都接受才成立（盟主=发起人）。
static void absorb_pacts(World& w, const std::vector<int>& members) {
    w.pacts.erase(std::remove_if(w.pacts.begin(), w.pacts.end(), [&](const Pact& p) {
        return std::count(members.begin(), members.end(), p.a) || std::count(members.begin(), members.end(), p.b);
    }), w.pacts.end());
}

bool World::accept_founding(int me, int id) {
    for (size_t k = 0; k < proposals.size(); k++) {
        auto& p = proposals[k];
        if (p.kind != 4 || (id >= 0 && p.id != id) || std::find(p.invitees.begin(), p.invitees.end(), me) == p.invitees.end()) continue;
        if (std::find(p.accepted.begin(), p.accepted.end(), me) != p.accepted.end()) return false;
        p.accepted.push_back(me);
        if (p.accepted.size() == p.invitees.size()) {
            Bloc b;
            b.id = next_bloc++; b.name = p.name; b.chief = p.a; b.turn = turn;
            b.members.push_back(p.a);
            for (int m : p.invitees) b.members.push_back(m);
            absorb_pacts(*this, b.members);
            blocs.push_back(b);
            proposals.erase(proposals.begin() + k);
        }
        return true;
    }
    return false;
}

bool World::bloc_join(int a, int bloc_id) {
    int fee = diplo_charge(a, -1, false, -1);
    if (fee < 0) return false;
    bool ok = [&]() -> bool {
        if (!alive(a) || !_diplo_authority(a) || bloc_in(a)) return false;
        for (auto& b : blocs) {
            if (b.id != bloc_id) continue;
            for (int m : b.members) if (at_war(a, m)) return false;     // 战时不能入盟
            Vote v;
            v.id = next_vote++; v.kind = 1; v.bloc = b.id; v.proposer = a; v.target = a;
            v.payload = 0; v.flag = false; v.turn = turn; v.deadline = turn + 1;
            votes.push_back(v);                                          // T 发起、T+1 计票（现成员表决）
            return true;
        }
        return false;
    }();
    if (!ok && fee > 0) add_res(a, GOLD, fee);
    return ok;
}

bool World::bloc_leave(int actor) {
    for (size_t k = 0; k < blocs.size(); k++) {
        auto& b = blocs[k];
        auto it = std::find(b.members.begin(), b.members.end(), actor);
        if (it == b.members.end()) continue;
        if (b.chief == actor) return false;                              // 盟主须先转让或解散
        for (int m : b.members) if (at_war_any(m)) return false;         // 任一成员交战 → 全盟锁死
        b.members.erase(it);
        if (b.chief == actor || b.members.size() < 2) blocs.erase(blocs.begin() + k);   // 首领离开或只剩一人 → 解散（main：成员少于两国自动解散）
        return true;
    }
    return false;
}

bool World::bloc_transfer(int actor, int target) {
    for (auto& b : blocs)
        if (b.chief == actor && std::find(b.members.begin(), b.members.end(), target) != b.members.end()) {
            b.chief = target;
            return true;
        }
    return false;
}

bool World::bloc_rename(int actor, const std::string& name) {
    for (auto& b : blocs) if (b.chief == actor) { b.name = name; return true; }
    return false;
}

bool World::vote(int n, int vote_id, int choice) {
    for (auto& v : votes) {
        if (v.id != vote_id) continue;
        auto bi = std::find_if(blocs.begin(), blocs.end(), [&](const Bloc& b) { return b.id == v.bloc; });
        if (bi == blocs.end() || std::find(bi->members.begin(), bi->members.end(), n) == bi->members.end()) return false;
        if (v.kind == 1 && v.proposer == n) return false;
        if (turn != v.deadline) return false;                            // T 发起，T+1 才能投
        v.votes[n] = (int8_t)choice;
        return true;
    }
    return false;
}

void World::tally_votes() {
    std::vector<int> done;
    for (auto& v : votes) {
        if (turn < v.deadline) continue;
        int yes = 0, no = 0;
        std::vector<int> bloc_members;
        for (auto& b : blocs) if (b.id == v.bloc) bloc_members = b.members;
        for (int m : bloc_members) {
            auto it = v.votes.find(m);
            if (it == v.votes.end()) no++;
            else if (it->second == 1) yes++;
            else if (it->second == 0) no++;
        }
        if (yes > no) approved_votes.push_back(v);
        done.push_back(v.id);
    }
    votes.erase(std::remove_if(votes.begin(), votes.end(), [&](const Vote& v) {
        return std::find(done.begin(), done.end(), v.id) != done.end();
    }), votes.end());
}


void World::execute_vote(const Vote& v) {
    auto b = std::find_if(blocs.begin(), blocs.end(), [&](const Bloc& bloc) { return bloc.id == v.bloc; });
    if (b == blocs.end()) return;
    if (v.kind == 0) {
        declare_war(b->chief, v.target);
    } else if (v.kind == 1 && alive(v.proposer) && !bloc_in(v.proposer)) {
        if (std::any_of(b->members.begin(), b->members.end(), [&](int n) { return at_war(n, v.proposer); })) return;
        absorb_pacts(*this, {v.proposer});
        b->members.push_back(v.proposer);
    }
}


// ---------------- 外交命令队列 ----------------
// main：行动期提交（干跑校验 + 预扣费）→ 生产之后「relations」挂点按优先级统一重验生效，失败退款。
static int A2I(const std::vector<std::string>& a, size_t i) {
    if (i >= a.size()) return 0;
    try { return std::stoi(a[i]); } catch (...) { return 0; }   // 联盟名等非数字参数
}

int World::diplo_priority(const std::string& nm) {
    static const std::unordered_map<std::string, int> P = {
        {"accept_peace", 10}, {"reject_peace", 11}, {"offer_peace", 12},
        {"propose_vassalage", 26}, {"submit_vassalage", 27}, {"release_vassal", 20},
        {"cancel_guarantee", 30}, {"break_defense", 31}, {"declare_guarantee", 32},
        {"propose_pact", 33}, {"reject_pact", 34}, {"accept_pact", 35},
        {"reject_vassalage", 34}, {"accept_vassalage", 35}, {"accept_founding", 35}, {"reject_founding", 34},
        {"bloc_leave", 21}, {"bloc_transfer", 22}, {"bloc_rename", 23}, {"propose_bloc", 24}, {"bloc_join", 25},
        {"independence_war", 90}, {"declare_war", 100},
    };
    auto it = P.find(nm);
    return it == P.end() ? 50 : it->second;
}

// 队列动作 = 有优先级的（gift/信件/换图/谍报/银行是即时动作）
static bool is_queued(const std::string& nm) {
    static const std::unordered_map<std::string, int> P = {
        {"accept_peace", 1}, {"reject_peace", 1}, {"offer_peace", 1},
        {"propose_vassalage", 1}, {"submit_vassalage", 1}, {"release_vassal", 1},
        {"cancel_guarantee", 1}, {"break_defense", 1}, {"declare_guarantee", 1},
        {"propose_pact", 1}, {"reject_pact", 1}, {"accept_pact", 1},
        {"reject_vassalage", 1}, {"accept_vassalage", 1}, {"accept_founding", 1}, {"reject_founding", 1},
        {"propose_bloc", 1}, {"bloc_join", 1}, {"bloc_leave", 1}, {"bloc_transfer", 1}, {"bloc_rename", 1},
        {"independence_war", 1}, {"declare_war", 1},
    };
    return P.count(nm) != 0;
}

bool World::at_war_any(int n) const {
    int e = entity(n);
    for (auto& w : wars)
        if (std::find(w.atk_ents.begin(), w.atk_ents.end(), e) != w.atk_ents.end() ||
            std::find(w.def_ents.begin(), w.def_ents.end(), e) != w.def_ents.end()) return true;
    return false;
}

bool World::bloc_in(int actor) const {
    for (auto& b : blocs) if (std::find(b.members.begin(), b.members.end(), actor) != b.members.end()) return true;
    return false;
}
bool World::bloc_chief(int actor) const {
    for (auto& b : blocs) if (b.chief == actor) return true;
    return false;
}

// 干跑校验（main 的 submit 阶段：用**当前状态**，同回合前面的命令尚未生效）
bool World::dry_diplo(int actor, const std::string& nm, const std::vector<std::string>& a) const {
    if (!alive(actor)) return false;
    if (already_queued(actor, nm, a)) return true;
    const int fee = queued_fee(actor, nm, a);
    return (!fee || res(actor, GOLD) >= fee) && dry_diplo_inner(actor, nm, a);
}

bool World::dry_diplo_inner(int actor, const std::string& nm, const std::vector<std::string>& a) const {
    int X = A2I(a, 0), Y = A2I(a, 1), Z = A2I(a, 2);
    if (!alive(actor)) return false;
    if (nm == "declare_war") {
        if (X < 0 || X >= (int)nations.size() || !alive(X)) return false;
        if (!_diplo_authority(actor) || at_war(actor, X) || entity(actor) == entity(X)) return false;
        return !has_truce(actor, X);
    }
    if (nm == "offer_peace") return alive(X) && _diplo_authority(actor) && at_war(actor, X) &&
                                   peace_terms_ok(actor, X, Y, Z);
    if (nm == "accept_peace" || nm == "reject_peace") {
        for (const auto& p : peace) {
            if (p.b != actor || (!a.empty() && p.id != X)) continue;
            if (nm == "reject_peace" || bloc_chief(actor)) return true;
            if (!at_war(p.a, p.b)) return false;
            return p.kind == 1 ? res(p.a, GOLD) >= p.gold
                 : p.kind == 2 ? res(p.b, GOLD) >= p.gold : true;
        }
        return false;
    }
    if (nm == "declare_guarantee") {
        if (!alive(X)) return false;
        int ea = entity(actor), eb = entity(X);
        if (ea == eb || entity_at_war(ea) || entity_at_war(eb)) return false;
        for (const auto& p : pacts)
            if ((p.a == ea && p.b == eb) || (p.kind == 1 && p.a == eb && p.b == ea)) return false;
        return true;
    }
    if (nm == "cancel_guarantee" || nm == "break_defense") {
        if (!alive(X) || (nm == "break_defense" && !_diplo_authority(actor))) return false;
        int ea = entity(actor), eb = entity(X);
        if (ea == eb || entity_at_war(ea) || entity_at_war(eb)) return false;
        for (const auto& p : pacts) {
            if (nm == "cancel_guarantee" && p.kind == 0 && p.a == ea && p.b == eb) return true;
            if (nm == "break_defense" && p.kind == 1 &&
                ((p.a == ea && p.b == eb) || (p.b == ea && p.a == eb))) return true;
        }
        return false;
    }
    if (nm == "propose_pact") {
        if (X < 0 || X >= (int)nations.size() || !alive(X) || !_diplo_authority(actor) || at_war(actor, X)) return false;
        int ea = entity(actor), eb = entity(X);
        if (ea == eb || entity_at_war(ea) || entity_at_war(eb)) return false;
        for (const auto& p : proposals)
            if (p.kind == 1 && ((p.a == ea && p.b == eb) || (p.a == eb && p.b == ea))) return false;
        for (auto& p : pacts) if (p.kind == 1 && ((p.a == ea && p.b == eb) || (p.a == eb && p.b == ea))) return false;
        return true;
    }
    if (nm == "accept_pact" || nm == "reject_pact") {
        for (const auto& p : proposals) {
            if (p.kind != 1 || entity(actor) != p.b || (!a.empty() && p.id != X)) continue;
            if (nm == "reject_pact") return true;
            if (entity(p.asker) != p.a || entity_at_war(p.a) || entity_at_war(p.b)) return false;
            for (const auto& pact : pacts)
                if (pact.kind == 1 && ((pact.a == p.a && pact.b == p.b) ||
                                       (pact.a == p.b && pact.b == p.a))) return false;
            return true;
        }
        return false;
    }
    if (nm == "propose_vassalage" || nm == "submit_vassalage") {
        int subject = (nm == "propose_vassalage") ? X : actor;
        int overlord = (nm == "propose_vassalage") ? actor : X;
        if (subject == overlord || !alive(subject) || !alive(overlord) || bloc_in(subject)) return false;
        int kind = nm == "propose_vassalage" ? 2 : 3;
        for (const auto& p : proposals)
            if (p.kind == kind && p.a == actor && p.b == X) return false;
        if (vassals.count(subject) || vassals.count(overlord) || at_war(subject, overlord)) return false;
        return !entity_at_war(entity(overlord)) && !entity_at_war(entity(subject));
    }
    if (nm == "accept_vassalage" || nm == "reject_vassalage") {
        for (const auto& p : proposals) {
            if ((p.kind != 2 && p.kind != 3) || p.b != actor || (!a.empty() && p.id != X)) continue;
            if (nm == "reject_vassalage") return entity(actor) == p.b;
            const int subject = p.kind == 2 ? p.b : p.a;
            const int overlord = p.kind == 2 ? p.a : p.b;
            return alive(subject) && alive(overlord) && !bloc_in(subject) &&
                !vassals.count(subject) && !vassals.count(overlord) &&
                !entity_at_war(entity(subject)) && !entity_at_war(entity(overlord));
        }
        return false;
    }
    if (nm == "release_vassal") {
        auto it = vassals.find(X);
        return it != vassals.end() && it->second.overlord == actor;
    }
    if (nm == "independence_war") {
        auto v = vassals.find(actor);
        if (v == vassals.end() || !alive(v->second.overlord)) return false;
        int overlord = entity(v->second.overlord);
        int sovereign = bloc_of(actor) ? BLOC_OFF + bloc_of(actor)->id : actor;
        if (sovereign == overlord) return false;
        return std::none_of(truces.begin(), truces.end(), [&](const Truce& t) {
            return t.until > turn && ((t.a == sovereign && t.b == overlord) || (t.b == sovereign && t.a == overlord));
        });
    }
    if (nm == "propose_bloc") {
        if (!_diplo_authority(actor) || bloc_in(actor) || at_war_any(actor) ||
            a.empty() || a[0].empty() || a[0].find(' ') != std::string::npos) return false;
        for (const auto& bloc : blocs)
            if (bloc.name == a[0]) return false;
        for (const auto& proposal : proposals)
            if (proposal.kind == 4 && proposal.a == actor && proposal.name == a[0]) return false;
        bool has_invitee = false;
        for (size_t i = 1; i < a.size(); i++) {
            int m = A2I(a, i);
            if (m == actor) continue;
            if (!alive(m) || bloc_in(m) || at_war_any(m)) return false;
            has_invitee = true;
        }
        return has_invitee;
    }
    if (nm == "bloc_join") {
        if (!_diplo_authority(actor) || bloc_in(actor) || at_war_any(actor)) return false;
        for (auto& b : blocs) if (b.id == X) { for (int m : b.members) if (at_war(actor, m)) return false; return true; }
        return false;
    }
    if (nm == "accept_founding" || nm == "reject_founding") {
        for (auto& p : proposals)
            if (p.kind == 4 && (a.empty() || p.id == X) && std::find(p.invitees.begin(), p.invitees.end(), actor) != p.invitees.end() &&
                (nm == "reject_founding" || std::find(p.accepted.begin(), p.accepted.end(), actor) == p.accepted.end()))
                return true;
        return false;
    }
    if (nm == "bloc_leave") {
        for (auto& b : blocs)
            if (std::find(b.members.begin(), b.members.end(), actor) != b.members.end()) {
                if (b.chief == actor) return false;
                for (int m : b.members) if (at_war_any(m)) return false;
                return true;
            }
        return false;
    }
    if (nm == "bloc_transfer") { for (auto& b : blocs) if (b.chief == actor && std::find(b.members.begin(), b.members.end(), X) != b.members.end()) return true; return false; }
    if (nm == "bloc_rename") return bloc_chief(actor);
    return false;
}

// 执行（result 阶段）：当前状态重验，失败返回 false（由 resolve 退款）
bool World::exec_diplo(int actor, const std::string& nm, const std::vector<std::string>& a) {
    int X = A2I(a, 0), Y = A2I(a, 1), Z = A2I(a, 2), Q = A2I(a, 3);
    if (nm == "declare_war") return declare_war(actor, X);
    if (nm == "offer_peace") return offer_peace(actor, X, Y, Z, Q);
    if (nm == "accept_peace" || nm == "reject_peace") {
        for (auto& p : peace) {
            if (p.b != actor || (!a.empty() && p.id != X)) continue;
            int id = p.id;
            return nm == "accept_peace" ? accept_peace(actor, id) : reject_peace(actor, id);
        }
        return false;
    }
    if (nm == "declare_guarantee") return declare_guarantee(actor, X);
    if (nm == "cancel_guarantee" || nm == "break_defense") {
        if (!dry_diplo_inner(actor, nm, a)) return false;
        int ea = entity(actor), eb = entity(X);
        for (size_t k = 0; k < pacts.size(); k++)
            if ((nm == "cancel_guarantee" && pacts[k].kind == 0 && pacts[k].a == ea && pacts[k].b == eb) ||
                (nm == "break_defense" && pacts[k].kind == 1 &&
                 ((pacts[k].a == ea && pacts[k].b == eb) || (pacts[k].a == eb && pacts[k].b == ea)))) {
                pacts.erase(pacts.begin() + k);
                return true;
            }
        return false;
    }
    if (nm == "propose_pact") return propose_pact(1, actor, X);
    if (nm == "accept_pact" || nm == "reject_pact") {
        for (size_t k = 0; k < proposals.size(); k++) {
            auto& p = proposals[k];
            if (p.kind != 1 || entity(actor) != p.b || (!a.empty() && p.id != X)) continue;
            int id = p.id;
            return nm == "accept_pact" ? accept_pact(actor, id) : reject_pact(actor, id);
        }
        return false;
    }
    if (nm == "propose_vassalage") return propose_vassalage(actor, X);
    if (nm == "submit_vassalage") return submit_vassalage(actor, X);
    if (nm == "accept_vassalage") {
        for (size_t k = 0; k < proposals.size(); k++) {
            auto& p = proposals[k];
            if ((p.kind == 2 || p.kind == 3) && p.b == actor && (a.empty() || p.id == X)) return accept_vassalage(actor, p.id);
        }
        return false;
    }
    if (nm == "reject_vassalage") {
        for (size_t k = 0; k < proposals.size(); k++) {
            auto& p = proposals[k];
            if ((p.kind == 2 || p.kind == 3) && p.b == actor && (a.empty() || p.id == X)) {
                proposals.erase(proposals.begin() + k);
                return true;
            }
        }
        return false;
    }
    if (nm == "release_vassal") return release_vassal(actor, X);
    if (nm == "independence_war") return independence_war(actor);
    if (nm == "propose_bloc") {
        if (!dry_diplo_inner(actor, nm, a)) return false;
        std::string name = a.empty() ? "b" : a[0];
        std::vector<int> inv;
        for (size_t i = 1; i < a.size(); i++) inv.push_back(A2I(a, i));
        return propose_bloc(actor, name, inv);
    }
    if (nm == "bloc_join") return bloc_join(actor, X);
    if (nm == "accept_founding") return accept_founding(actor, a.empty() ? -1 : X);
    if (nm == "reject_founding") {
        for (auto it = proposals.begin(); it != proposals.end(); ++it) {
            if (it->kind != 4 || (X && it->id != X) || !std::count(it->invitees.begin(), it->invitees.end(), actor)) continue;
            proposals.erase(it);
            return true;
        }
        return false;
    }
    if (nm == "bloc_leave") return bloc_leave(actor);
    if (nm == "bloc_transfer") return bloc_transfer(actor, X);
    if (nm == "bloc_rename") return bloc_rename(actor, a.empty() ? "b" : a[0]);
    return false;
}

// 提交：干跑 + 预扣费 + 入队；即时动作直接执行
int World::queued_fee(int actor, const std::string& nm, const std::vector<std::string>& a) const {
    const int target = A2I(a, 0);
    if (nm == "declare_war" || nm == "declare_guarantee" || nm == "cancel_guarantee" ||
        nm == "break_defense" || nm == "propose_vassalage" || nm == "submit_vassalage" || nm == "release_vassal")
        return base_fee(actor, target);
    if (nm == "offer_peace" || nm == "propose_pact") return base_fee(actor, target, true);
    if (nm == "propose_bloc") return Num::FOUND_FEE;
    if (nm == "accept_peace" || nm == "reject_peace" || nm == "accept_pact" || nm == "reject_pact" ||
        nm == "accept_vassalage" || nm == "reject_vassalage" || nm == "accept_founding" ||
        nm == "reject_founding" || nm == "bloc_transfer" || nm == "bloc_rename") return 0;
    return base_fee(actor);
}

bool World::already_queued(int actor, const std::string& nm, const std::vector<std::string>& a) const {
    return std::any_of(diplo_q.begin(), diplo_q.end(), [&](const DiploCmd& c) {
        return c.actor == actor && c.action == nm && c.args == a;
    });
}

bool World::submit_diplo(int actor, const std::string& nm, const std::vector<std::string>& a) {
    if (!is_queued(nm)) return exec_diplo(actor, nm, a);
    if (!alive(actor)) return false;
    if (already_queued(actor, nm, a)) return true;
    const int fee = queued_fee(actor, nm, a);
    if ((fee && res(actor, GOLD) < fee) || !dry_diplo_inner(actor, nm, a)) return false;
    add_res(actor, GOLD, -fee);
    diplo_q.push_back({actor, nm, a, fee});
    return true;
}

void World::resolve_diplomacy() {
    struct Task { int priority, actor; const DiploCmd* cmd; const Vote* vote; };
    std::vector<Task> tasks;
    for (const auto& c : diplo_q) tasks.push_back({diplo_priority(c.action), c.actor, &c, nullptr});
    for (const auto& v : approved_votes) tasks.push_back({v.kind == 1 ? 25 : 100, v.proposer, nullptr, &v});
    std::stable_sort(tasks.begin(), tasks.end(), [](const Task& a, const Task& b) {
        return std::pair{a.priority, a.actor} < std::pair{b.priority, b.actor};
    });
    no_fee = true;
    for (const auto& task : tasks) {
        if (task.vote) {
            tally_mode = true;
            execute_vote(*task.vote);
            tally_mode = false;
        } else if (alive(task.actor)) {
            const auto& c = *task.cmd;
            if (!exec_diplo(c.actor, c.action, c.args) && c.fee > 0) add_res(c.actor, GOLD, c.fee);
        }
    }
    no_fee = false;
    diplo_q.clear();
    approved_votes.clear();
}

void World::end_actions(int actor) {
    auto answered = [&](int id, const char* accept, const char* reject) {
        return std::any_of(diplo_q.begin(), diplo_q.end(), [&](const DiploCmd& cmd) {
            return cmd.actor == actor && (cmd.action == accept || cmd.action == reject)
                && (cmd.args.empty() || A2I(cmd.args, 0) == id);
        });
    };
    for (const auto& p : peace)
        if (p.b == actor && !answered(p.id, "accept_peace", "reject_peace"))
            submit_diplo(actor, "reject_peace", {std::to_string(p.id)});
    for (const auto& p : proposals) {
        const char* accept = "accept_pact";
        const char* reject = "reject_pact";
        bool respond = false;
        if (p.kind == 1) {
            const auto* bloc = bloc_of(actor);
            respond = p.b == entity(actor) && (bloc ? bloc->chief == actor : p.b == actor);
        } else if (p.kind == 2 || p.kind == 3) {
            respond = p.b == actor;
            accept = "accept_vassalage";
            reject = "reject_vassalage";
        } else if (p.kind == 4) {
            respond = std::count(p.invitees.begin(), p.invitees.end(), actor)
                && !std::count(p.accepted.begin(), p.accepted.end(), actor);
            accept = "accept_founding";
            reject = "reject_founding";
        }
        if (respond && !answered(p.id, accept, reject)) submit_diplo(actor, reject, {std::to_string(p.id)});
    }
    for (const auto& v : votes)
        if (!v.votes.count(actor)) vote(actor, v.id, 0);
}
