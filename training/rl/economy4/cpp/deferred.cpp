#include "engine.hpp"
#include <map>
#include <tuple>

void World::cancel_site_reservations(int x,int y) {
    auto& t=tile(x,y);
    if (!t.pending_started && t.pending_owner>=0) {
        for(int b=0;b<BLD_N;++b) if(t.pending[b]) {
            add_res(t.pending_owner,GOLD,t.pending_gold);add_res(t.pending_owner,WOOD,Num::B_WOOD[b]);
            if(b==DIPLO)nations[t.pending_owner].built_diplomacy_center=false;
        }
        t.pending.fill(0);t.pending_gold=0;t.pending_owner=-1;t.built=0;
    }
    for(auto it=recruit_reservations.begin();it!=recruit_reservations.end();) {
        if(it->x!=x||it->y!=y){++it;continue;}
        add_res(it->owner,FOOD,Num::U_REC_FOOD[it->kind]);add_res(it->owner,EQUIP,Num::U_REC_EQUIP[it->kind]);add_res(it->owner,GOLD,Num::U_REC_GOLD[it->kind]);
        it=recruit_reservations.erase(it);
    }
}

void World::advance_production_orders() {
    for(int cell:tile_order) {
        auto& t=tiles[cell];
        if(t.pending_owner<0||t.pending_started)continue;
        if(t.owner!=t.pending_owner||!alive(t.pending_owner)){cancel_site_reservations(cell/size,cell%size);continue;}
        const int occupied=std::accumulate(t.bld.begin(),t.bld.end(),0);
        for(int b=0;b<BLD_N;++b) if(t.pending[b]&&occupied>=Num::B_MIN_SLOTS[b]) {
            t.pending_started=true;ledger(t.owner).invest_gold+=t.pending_gold;
            value_flow(t.owner,&Ledger::invest_wood,WOOD,Num::B_WOOD[b]);
        }
    }
    std::vector<Army> waiting;
    std::map<std::tuple<int,int,bool>,int> used;
    for(auto unit:recruit_reservations) {
        auto& t=tile(unit.x,unit.y);
        if(t.owner!=unit.owner||!alive(unit.owner)) {
            add_res(unit.owner,FOOD,Num::U_REC_FOOD[unit.kind]);add_res(unit.owner,EQUIP,Num::U_REC_EQUIP[unit.kind]);add_res(unit.owner,GOLD,Num::U_REC_GOLD[unit.kind]);continue;
        }
        const bool militia=unit.kind==MIL;
        const int capacity=militia?t.bld[MILITIA]*Num::B_MIL_TURN:t.bld[BARRACKS]*Num::B_RECRUIT_CAP;
        auto& quota=used[{unit.x,unit.y,militia}];
        if(quota>=capacity||(!militia&&nations[unit.owner].grid_short)){waiting.push_back(unit);continue;}
        ++quota;unit.moved_turn=turn;armies.push_back(unit);
    }
    recruit_reservations=std::move(waiting);
}

void World::project_recruits(int actor) {
    std::map<std::tuple<int,int,bool>,int> reserved;
    for(auto unit:recruit_reservations) if(unit.owner==actor) {
        unit.moved_turn=turn;armies.push_back(unit);
        auto& count=reserved[{unit.x,unit.y,unit.kind==MIL}];++count;
        auto& t=tile(unit.x,unit.y);if(unit.kind==MIL)t.militia_rec=std::max<int>(t.militia_rec,count);else t.recruited=std::max<int>(t.recruited,count);
    }
    // A projection is read-only. Removing its queue prevents double-counting militia.
    recruit_reservations.clear();
}
