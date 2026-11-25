#include "rollout.hpp"
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>

using namespace rl;
void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
bool same(const Observation& a,const Observation& b){
    return a.globals==b.globals&&a.market==b.market&&a.nations==b.nations&&a.tiles==b.tiles&&
        a.armies==b.armies&&a.cand==b.cand&&a.cand_ptr==b.cand_ptr&&a.cand_type==b.cand_type;
}
Episode fresh(){Episode e(540000,24,100,{},Scenario::Market4SelfPlay);e.enable_dynamic();return e;}
int capital(const World& w,int n){for(int i=0;i<int(w.tiles.size());++i)if(w.tiles[i].owner==n&&w.tiles[i].bld[TOWNHALL])return i;throw 1;}
Candidate pick(const View& v,Type type,int good,int qty){for(const auto& c:v.choices)if(c.type==type&&c.args[0]==good&&c.args[1]==qty)return c;throw 2;}
void end_round(Episode& e){int period=e.world.mperiod;while(e.world.mperiod==period){auto actors=e.ready_actors();for(int actor:actors){auto v=e.observe(actor);e.act(actor,v.choices[0]);}}}
std::pair<double,double> combination(bool buy,bool build){
    auto e=fresh();auto& w=e.world;int cell=capital(w,0);
    for(auto& t:w.tiles)if(t.owner==0){t.bld.fill(0);t.pending.fill(0);}
    w.tiles[cell].bld[TOWNHALL]=1;w.nations[0].res[WOOD]=0;
    if(buy){auto v=e.observe(0);e.act(0,pick(v,Type::Buy,1,60));}
    if(build){auto v=e.observe(0);e.act(0,{Type::Build,{cell/w.size,cell%w.size,WOODPOWER}});}
    end_round(e);end_round(e);
    return {e.player(0).total_current_gdp,e.player(0).total_fixed_gdp};
}
int main(){
    std::cout<<std::setprecision(12)<<std::unitbuf;
    {
        auto e=fresh();auto first=e.observe(0);
        auto invalid=pick(first,Type::Sell,3,60);require(!invalid.executable,"expected invalid oil sale");
        e.act(0,invalid);auto after_one=e.observe(0);
        for(int i=1;i<30;++i){auto v=e.observe(0);e.act(0,pick(v,Type::Sell,3,60));}
        auto exhausted=e.observe(0);require(same(after_one.obs,exhausted.obs),"budget changes leaked into observation");
        auto buy=pick(exhausted,Type::Buy,0,1);require(buy.executable,"expected executable food purchase");
        auto before=e.world.nations[0].res;e.act(0,buy);auto after=e.world.nations[0].res;
        auto ready=e.ready_actors();bool ended=std::find(ready.begin(),ready.end(),0)==ready.end();
        require(before==after&&ended,"budget did not replace purchase with end");
        std::cout<<"{\"test\":\"budget_alias\",\"passed\":true,\"identical_observation_after_steps\":[1,30],\"sampled_type\":4,\"purchase_executable\":true,\"resources_changed\":false,\"actor_ended\":true}\n";
    }
    {
        auto e=fresh();auto& w=e.world;for(auto& t:w.tiles)if(t.owner==0){t.bld.fill(0);t.pending.fill(0);}
        auto& t=w.tiles[capital(fresh().world,0)];t.bld[TOWNHALL]=1;t.bld[WOODPOWER]=1;
        w.nations[0].res[WOOD]=0;w.production_phase();require(w.nations[0].grid_short,"expected blackout");
        Observer o;auto before=o.encode(w,0,candidates(w,0));
        require(apply(w,0,pick(e.observe(0),Type::Buy,1,1)),"fuel purchase failed");
        auto after=o.encode(w,0,candidates(w,0));World projected=w;projected.production_phase();
        require(before.globals[20]==1&&after.globals[20]==1&&!projected.nations[0].grid_short,"expected stale blackout flag");
        std::cout<<"{\"test\":\"power_observation_is_last_settlement\",\"passed\":true,\"blackout_flag_before_buy\":1,\"blackout_flag_after_buy\":1,\"next_production_blackout\":false,\"fuel_after_buy\":"<<w.res(0,WOOD)<<"}\n";
    }
    {
        auto e=fresh();auto& w=e.world;int i=capital(w,0);for(auto& t:w.tiles)if(t.owner==0)t.bld.fill(0);
        auto& t=w.tiles[i];t.bld[TOWNHALL]=1;t.bld[WOODPOWER]=1;t.bld[SUPPLYFAC]=2;
        w.nations[0].res[WOOD]=10;w.nations[0].res[FOOD]=0;w.nations[0].res[ORE]=0;w.production_phase();
        require(w.nations[0].energy_use==3&&w.nations[0].grid_short,"factories without material did not draw power");
        std::cout<<"{\"test\":\"fixed_factory_load\",\"passed\":true,\"power\":"<<w.nations[0].energy_out<<",\"demand\":"<<w.nations[0].energy_use<<",\"blackout\":true}\n";
    }
    {
        auto e=fresh();auto first=e.observe(0);e.act(0,pick(first,Type::Sell,3,60));
        auto initial=e.observe(0);auto market_before=initial.obs.market;
        auto other=e.observe(1);e.act(1,pick(other,Type::Buy,1,60));
        auto later=e.observe(0);require(market_before==later.obs.market,"market changed before settlement");
        require(!later.new_turn,"unexpected own-country new turn");
        e.world.market_settle();Observer observer;auto settled=observer.encode(e.world,0,candidates(e.world,0));
        require(market_before!=settled.market,"market did not update at settlement");
        std::cout<<"{\"test\":\"foreign_trade_market_update_is_deferred\",\"passed\":true,\"own_turn_index\":"<<e.player(0).turn_index<<",\"foreign_actor\":1,\"market_changed_before_settlement\":false,\"market_changed_after_settlement\":true}\n";
    }
    {
        auto neither=combination(false,false),buy=combination(true,false),build=combination(false,true),both=combination(true,true);
        double interaction=both.first-buy.first-build.first+neither.first;
        require(interaction>0,"expected positive action-combination interaction");
        std::cout<<"{\"test\":\"purchase_and_construction_joint_effect\",\"passed\":true,\"horizon_turns\":2,\"nominal_gdp\":{\"neither\":"<<neither.first<<",\"purchase_only\":"<<buy.first<<",\"construction_only\":"<<build.first<<",\"purchase_then_construction\":"<<both.first<<"},\"interaction\":"<<interaction<<",\"limitation\":\"Controlled engine intervention; not a causal diagnosis of the historical PPO collapse\"}\n";
    }
}
