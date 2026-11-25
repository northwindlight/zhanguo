#include "rollout.hpp"
#include <cassert>
#include <iostream>

using namespace rl;
Episode fresh(){Episode e(432000,24,3,{},Scenario::Market4SelfPlay);e.enable_dynamic();for(auto& n:e.world.nations)n.res.fill(1000);return e;}
int capital(const World& w,int actor=0){for(int i=0;i<int(w.tiles.size());++i)if(w.tiles[i].owner==actor && w.tiles[i].bld[TOWNHALL])return i;throw 1;}
void end_round(Episode& e){const int period=e.world.mperiod;while(e.world.mperiod==period){auto ready=e.ready_actors();assert(!ready.empty());for(int actor:ready){auto view=e.observe(actor);e.act(actor,view.choices[0]);}}}
int main(){
    {
        auto e=fresh();assert(e.ready_actors().size()==4);
        for(int actor:{2,0,3}){auto view=e.observe(actor);e.act(actor,view.choices[0]);assert(e.world.mperiod==0);}
        auto view=e.observe(1);e.act(1,view.choices[0]);assert(e.world.mperiod==1);assert(e.world.turn==2);assert(e.ready_actors().size()==4);
    }
    {
        auto e=fresh();int cell=capital(e.world),x=cell/24,y=cell%24;auto before=e.world.nations[0].res;
        e.observe(0);e.act(0,{Type::Build,{x,y,WOODPOWER}});
        assert(e.world.tiles[cell].pending[WOODPOWER]==1);assert(e.world.tiles[cell].bld[WOODPOWER]==0);
        auto view=e.observe(0);assert(view.obs.tiles[cell][37]>0);assert(view.obs.tiles[cell][21+WOODPOWER]==0);
        e.act(0,{Type::Demolish,{x,y,WOODPOWER}});assert(e.world.nations[0].res==before);
        e.act(0,{Type::Build,{x,y,WOODPOWER}});end_round(e);
        assert(e.world.tiles[cell].bld[WOODPOWER]==1);assert(e.world.tiles[cell].pending[WOODPOWER]==0);assert(e.world.nations[0].energy_out==0);
    }
    {
        auto e=fresh();int cell=capital(e.world),x=cell/24,y=cell%24;e.world.tiles[cell].bld[BARRACKS]=1;e.world.tiles[cell].bld[WOODPOWER]=1;
        auto before=e.world.nations[0].res;e.observe(0);e.act(0,{Type::Recruit,{x,y,INF}});
        assert(e.world.recruit_reservations.size()==1);assert(std::none_of(e.world.armies.begin(),e.world.armies.end(),[](const Army& a){return a.owner==0;}));
        auto view=e.observe(0);assert(view.obs.globals[14]>.09f);assert(view.obs.armies.size()==1);assert(view.obs.armies[0][9]==1);
        assert(e.observe(1).obs.armies.empty()); // Other nations never see the queued own-unit overlay.
        int aid=e.world.recruit_reservations[0].aid;e.act(0,{Type::Disband,{aid}});assert(e.world.recruit_reservations.empty());assert(e.world.nations[0].res==before);
        e.act(0,{Type::Recruit,{x,y,INF}});end_round(e);assert(e.world.recruit_reservations.empty());
        assert(std::count_if(e.world.armies.begin(),e.world.armies.end(),[](const Army& a){return a.owner==0;})==1);
        assert(e.world.nations[0].starved_units==0);
    }
    {
        auto e=fresh();int cell=capital(e.world),x=cell/24,y=cell%24;auto before=e.world.nations[0].res;e.world.build(0,x,y,WOODPOWER);
        e.world.conquer(x,y,1);assert(e.world.nations[0].res==before);assert(e.world.tiles[cell].pending[WOODPOWER]==0);
    }
    {
        auto e=fresh();int cell=capital(e.world),x=cell/24,y=cell%24;
        e.world.tiles[cell].tres[TR_FARM]=1;e.world.tiles[cell].bld[FARM]=1;
        assert(e.world.build_cost(0,x,y,MILITIA)<0); // Farms and militia camps share farmland capacity.
    }
    {
        auto e=fresh();auto& w=e.world;w.armies.clear();for(int actor=0;actor<4;++actor)w.spawn_recruited(actor,actor<2?5:18,actor<2?5:18,1,INF);
        auto groups=Episode::action_groups(w,e.first_player(w.turn));assert(groups.size()==2);
        w.armies.clear();for(int actor=0;actor<4;++actor)w.spawn_recruited(actor,5,5,1,INF);
        assert(Episode::action_groups(w,0).size()==1);
    }
    std::cout<<"dynamic boundary, occupancy, prepaid refunds, own-only recruit projection and conflict groups passed\n";
}
