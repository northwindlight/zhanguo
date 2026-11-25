// Pure CPU script demonstrations. No policy model is loaded or invoked.
#include "trajectory.hpp"
#include <iomanip>
#include <iostream>
#include <memory>

using namespace rl;
int land(const World& w,int seat){return std::count_if(w.tiles.begin(),w.tiles.end(),[&](const Tile& t){return t.owner==seat;});}
bool economic(Type type){return type==Type::Build||type==Type::Demolish||type==Type::Buy||type==Type::Sell||type==Type::EndTurn;}
int main(int argc,char** argv){
    try {
        if(argc!=5)throw std::invalid_argument("usage: zhanguo_script GAMES SEED TURNS OUTPUT");
        int games=std::stoi(argv[1]),turns=std::stoi(argv[3]);uint64_t seed=std::stoull(argv[2]);
        if(games<1||turns<32)throw std::invalid_argument("invalid script campaign dimensions");
        std::filesystem::path output=argv[4];std::filesystem::create_directories(output);
        setenv("ZHANGUO_TEACHER_INDUSTRY","1",1);
        std::cout<<std::setprecision(12)<<std::unitbuf;
        for(int game=0;game<games;++game){
            Episode e(seed+game,24,turns,{},Scenario::Market4SelfPlay);e.enable_dynamic();int focus=e.world.seed%4;
            Trajectory track(output,e.world.seed,true,focus,true);
            std::array<IndustryTutorState,4> teachers;for(auto& t:teachers){t.allow_ore_import=true;t.allow_capacity_import=true;}
            std::array<std::optional<View>,4> cached;
            int reached=0,recorded=0,early_military=0,supply_buys=0,budget_hits=0,event=0;
            int last_period=e.world.mperiod;
            while(!e.done){
                for(int actor:e.ready_actors()){
                    auto view=[&]() -> View& {if(!cached[actor])cached[actor]=e.observe(actor);return *cached[actor];};
                    auto apply_index=[&](int index){
                        auto& v=view();auto choice=v.choices.at(index);int turn=e.world.turn,ti=e.player(actor).turn_index;
                        if(actor==focus){track.step(v,ti,index,0,0,true,turn<=30);++recorded;}
                        if(turn<=30&&!economic(choice.type))++early_military;
                        if(choice.type==Type::Buy&&choice.args[0]==SUPPLY-1)++supply_buys;
                        e.act(actor,choice);cached[actor].reset();
                        std::cout<<"script_action seed="<<e.world.seed<<" event="<<event++<<" turn="<<turn<<" seat="<<actor
                            <<" type="<<int(choice.type)<<" a="<<choice.args[0]<<" b="<<choice.args[1]<<" c="<<choice.args[2]<<'\n';
                        if(land(e.world,focus)>=12&&!reached)reached=turn;
                        return true;
                    };
                    W3Executor execute=[&](const Candidate& request,double keep){
                        if(reached)return false;
                        if(e.world.turn<=30&&!economic(request.type))throw std::logic_error("military action in economic opening");
                        if(request.type==Type::Buy&&request.args[0]==SUPPLY-1)throw std::logic_error("forbidden supply purchase");
                        bool trade=request.type==Type::Buy||request.type==Type::Sell;int remaining=request.args[1];bool done=false;
                        do{
                            auto& v=view();int selected=-1,best=-1;
                            for(int i=0;i<int(v.choices.size());++i){const auto& c=v.choices[i];if(c.type!=request.type||!c.executable)continue;
                                if(trade){
                                    if(c.args[0]!=request.args[0]||c.args[1]>remaining)continue;
                                    if(c.type==Type::Buy){int price=e.world.market_cash(actor,e.world.market_walk(c.args[0],c.args[1],0,actor).second,0).first;
                                        if(e.world.res(actor,GOLD)-price<keep)continue;}
                                    if(c.args[1]>best){selected=i;best=c.args[1];}
                                }else if(c.args==request.args){selected=i;break;}
                            }
                            if(selected<0)return false;
                            if(trade)remaining-=v.choices[selected].args[1];
                            apply_index(selected);done=true;
                        }while(trade&&remaining>0&&!reached);
                        return done&&(!trade||remaining<=0);
                    };
                    if(!reached){
                        if(e.world.turn<=30)script_economy_turn(e.world,actor,execute);
                        else {industry_takeover(e.world,actor,teachers[actor]);industry_turn(e.world,actor,teachers[actor],true,execute);}
                    }
                    auto& end=view();if(end.obs.globals[budget_remaining_feature]<=0)++budget_hits;
                    apply_index(0);
                }
                if(e.world.mperiod!=last_period){
                    const auto& p=e.player(focus);track.gdp_turn(p.turn_index,p.last_fixed_gdp,p.last_current_gdp,e.first_player(p.turn_index+1));
                    std::cout<<"script_turn seed="<<e.world.seed<<" event="<<event++<<" turn="<<p.turn_index+1<<" land="<<land(e.world,focus)
                        <<" gdp="<<p.last_current_gdp<<" blackout="<<e.world.nations[focus].grid_short;
                    for(int good=0;good<6;++good)std::cout<<" price"<<good<<'='<<e.world.price[good];std::cout<<'\n';
                    last_period=e.world.mperiod;if(reached)e.done=true;
                }
            }
            const auto& p=e.player(focus);const auto& n=e.world.nations[focus];
            track.finish(p.total_fixed_gdp,-1,focus,p.turn_index+1,Opponent::Rule);
            std::cout<<"script_game seed="<<e.world.seed<<" seat="<<focus<<" turns="<<p.turn_index+1<<" land="<<land(e.world,focus)
                <<" reached="<<bool(reached)<<" decisions="<<recorded<<" starved="<<n.starved_units<<" shortage="<<n.supply_short_turns
                <<" blackout="<<p.blackout_turns<<" early_military="<<early_military<<" supply_buys="<<supply_buys<<" budget_hits="<<budget_hits
                <<" nominal_gdp="<<p.total_current_gdp/(p.turn_index+1)<<'\n';
        }
    } catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
