#include "rollout.hpp"
#include <iostream>

int main(){
    rl::Episode e(432000,24,3,{},rl::Scenario::Market4SelfPlay);e.enable_dynamic();auto& w=e.world;
    for(auto& n:w.nations)n.res.fill(1000);
    int cell=0;for(size_t i=0;i<w.tiles.size();++i)if(w.tiles[i].owner==0&&w.tiles[i].bld[TOWNHALL]){cell=i;break;}
    auto& t=w.tiles[cell];t.terrain=PLAIN;const int x=cell/24,y=cell%24;
    auto print=[&](const char* phase){int army=0,reserved=0;for(auto& a:w.armies)army+=a.owner==0;for(auto& a:w.recruit_reservations)reserved+=a.owner==0;
        std::cout<<"{\"phase\":\""<<phase<<"\",\"gold\":"<<w.res(0,GOLD)<<",\"wood\":"<<w.res(0,WOOD)<<",\"food\":"<<w.res(0,FOOD)<<",\"equipment\":"<<w.res(0,EQUIP)
                 <<",\"queued_buildings\":"<<(t.pending_started?0:std::accumulate(t.pending.begin(),t.pending.end(),0))<<",\"real_units\":"<<army<<",\"queued_units\":"<<reserved<<"}\n";};
    w.build(0,x,y,WOODPOWER);print("build_submitted");w.demolish(0,x,y,WOODPOWER);print("build_cancelled");
    t.bld[BARRACKS]=1;t.bld[WOODPOWER]=1;
    w.recruit(0,x,y,1,INF);print("recruit_submitted");int id=w.recruit_reservations[0].id;w.disband(0,id);print("recruit_cancelled");
    w.recruit(0,x,y,1,INF);w.advance_production_orders();print("recruit_started");
}
