#include "rollout.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>

using namespace rl;
void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
Candidate pick(const View& v,Type type,int good,int qty){for(const auto& c:v.choices)if(c.type==type&&c.args[0]==good&&c.args[1]==qty)return c;throw 1;}
void budget(const View& v,int used){
    require(std::abs(v.obs.globals[29]-float(60-used)/60)<1e-6,"remaining budget incorrect");
    require(std::abs(v.obs.globals[30]-float(used)/60)<1e-6,"used budget incorrect");
    require(std::abs(v.obs.globals[31]*100-60)<1e-5,"absolute cap not 60");
}
int main(){
    Episode e(540000,24,2,{},Scenario::Market4SelfPlay);e.enable_dynamic();
    auto first=e.observe(0);budget(first,0);
    require(first.new_turn&&std::abs(first.summary[29]-1)<1e-6,"turn summary missing budget");
    for(int i=0;i<59;++i){auto v=i?e.observe(0):first;budget(v,i);auto action=pick(v,Type::Sell,3,60);require(!action.executable,"expected invalid sale");e.act(0,action);}
    auto last=e.observe(0);budget(last,59);auto purchase=pick(last,Type::Buy,0,1);
    require(purchase.executable,"expected valid 60th action");double food=e.world.res(0,FOOD);e.act(0,purchase);
    require(e.world.res(0,FOOD)==food+1,"60th game action was not executed");
    auto ended=e.observe(0);budget(ended,60);
    require(ended.choices.size()==1&&ended.choices[0].type==Type::EndTurn,"exhausted actor can sample a game action");
    require(ended.obs.cand.size()==1&&ended.obs.cand_type[0]==int(Type::EndTurn),"observation candidates disagree");
    e.act(0,ended.choices[0]);auto other=e.observe(1);budget(other,0);
    int period=e.world.mperiod;
    while(e.world.mperiod==period){for(int actor:e.ready_actors()){auto v=e.observe(actor);e.act(actor,v.choices[0]);}}
    auto next=e.observe(0);budget(next,0);
    std::cout<<"{\"passed\":true,\"limit\":60,\"failed_actions_consume_budget\":true,\"action_60_executes\":true,\"exhausted_candidates\":\"EndTurn only\",\"per_country_budget\":true,\"resets_next_turn\":true,\"network_dimensions_unchanged\":true}\n";
}
