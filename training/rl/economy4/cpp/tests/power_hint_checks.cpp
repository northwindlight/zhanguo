#include "rollout.hpp"
#include <cmath>
#include <iostream>
#include <stdexcept>
using namespace rl;
void check(bool b,const char* m){if(!b)throw std::runtime_error(m);}
int main(){
    Episode e(550000,24,100,{},Scenario::Market4SelfPlay);e.enable_dynamic();auto& w=e.world;int home=-1;
    for(int i=0;i<int(w.tiles.size());++i)if(w.tiles[i].owner==0){if(w.tiles[i].bld[TOWNHALL])home=i;w.tiles[i].bld.fill(0);w.tiles[i].pending.fill(0);}
    auto& tile=w.tiles[home];tile.bld[TOWNHALL]=1;tile.bld[WOODPOWER]=1;tile.bld[SUPPLYFAC]=1;
    w.nations[0].res.fill(100);w.nations[0].res[GOLD]=1000;w.nations[0].res[FOOD]=0;w.nations[0].res[ORE]=0;
    w.nations[0].gdp_last=37.5;w.nations[0].fixed_gdp_last=999;
    auto view=e.observe(0);check(std::abs(view.obs.globals[10]-.375)<1e-6,"actor GDP is not actual nominal GDP");
    bool found=false;
    for(int i=0;i<int(view.choices.size());++i){const auto& c=view.choices[i];if(c.type!=Type::Build||c.args[2]!=SUPPLYFAC||!c.executable)continue;
        const auto& f=view.obs.cand[i];check(f[28]==1&&f[30]>.29&&f[31]<.21&&f[33]==1,"missing conditional construction blackout warning");
        check(c.executable&&view.obs.cand_exec[i]==1,"power warning hard-masked executable construction");found=true;break;}
    check(found,"missing legal factory construction");
    tile.bld[WOODPOWER]=0;tile.bld[OILPOWER]=1;w.nations[0].res[OIL]=0;
    Observer o;auto obs=o.encode(w,0,candidates(w,0));check(obs.cand[0][33]==1,"fuel-limited power not reported");
    bool buy=false;auto choices=candidates(w,0);
    for(int i=0;i<int(choices.size());++i)if(choices[i].type==Type::Buy&&choices[i].args[0]==OIL-1&&choices[i].args[1]==1){
        check(obs.cand[i][33]==0&&obs.cand[i][31]>.79,"fuel purchase does not repair conditional power forecast");buy=true;}
    check(buy,"missing oil purchase");
    std::cout<<"{\"passed\":true,\"nominal_gdp_only\":true,\"fixed_factory_load\":true,\"construction_warning\":true,\"fuel_purchase_warning\":true,\"no_power_hard_mask\":true}\n";
}
