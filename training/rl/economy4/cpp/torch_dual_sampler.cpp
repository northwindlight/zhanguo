#include "torch_inference.hpp"
#include <c10/core/DeviceGuard.h>
#include <future>
#include <iomanip>

using namespace rl_torch;

namespace {
int owner(uint64_t seed,int actor) {
    static constexpr int seats[6][2]={{0,1},{0,2},{0,3},{1,2},{1,3},{2,3}};
    return actor==seats[seed%6][0]||actor==seats[seed%6][1]?0:1;
}
struct Slot {
    rl::Episode episode;
    std::array<Tensor,4> memory;
    std::array<std::shared_ptr<rl::Trajectory>,4> tracks;
    std::ofstream events;
    int event=0;
    std::array<int,4> local_index{};
    Slot(uint64_t seed,int size,int turns,int width,const std::array<c10::Device,2>& devices,const std::filesystem::path& output)
        :episode(seed,size,turns,rl::default_opponents(),rl::Scenario::Market4SelfPlay) {
        episode.enable_dynamic();
        if(!output.empty()){
            std::filesystem::create_directories(output/"events");
            events.open(output/"events"/(std::to_string(seed)+".jsonl"));
            if(!events)throw std::runtime_error("cannot open global event stream");
            events<<std::setprecision(10);
        }
        for(int actor=0;actor<4;++actor){int policy=owner(seed,actor);
            memory[actor]=at::zeros({width},at::TensorOptions().device(devices[policy]).dtype(at::kFloat));
            if(!output.empty())tracks[actor]=std::make_shared<rl::Trajectory>(output/(policy?"B":"A"),seed,true,actor);
        }
    }
};
struct Request {Slot* slot;int actor,turn;rl::View view;int snapshot;};
struct Result {int action;float logp,value;};

std::vector<Result> infer(torch::jit::Module& policy,c10::Device device,std::vector<Request*>& rows) {
    if(rows.empty())return {};
    c10::InferenceMode inference;c10::OptionalDeviceGuard guard(device);
    std::vector<rl::Observation*> observations;std::vector<Tensor> memory;
    std::vector<Request*> fresh;std::vector<Tensor> previous;
    for(auto* r:rows){observations.push_back(&r->view.obs);if(r->view.new_turn){fresh.push_back(r);previous.push_back(r->slot->memory[r->actor]);}}
    if(!fresh.empty()){
        auto features=at::empty({static_cast<int64_t>(fresh.size()),rl::f_dim},at::kFloat);
        for(size_t i=0;i<fresh.size();++i)std::copy(fresh[i]->view.summary.begin(),fresh[i]->view.summary.end(),features.data_ptr<float>()+i*rl::f_dim);
        auto next=policy.run_method("mem_step",features.to(device),at::stack(previous)).toTensor();
        for(size_t i=0;i<fresh.size();++i)fresh[i]->slot->memory[fresh[i]->actor]=next[i];
    }
    for(auto* r:rows)memory.push_back(r->slot->memory[r->actor]);
    auto inputs=collate(observations,device.is_cuda());upload(inputs,device);
    auto [logits,values]=predict(policy,inputs,at::stack(memory),false);
    auto probabilities=at::softmax(logits,-1);auto action=at::multinomial(probabilities,1);
    auto logp=at::log(probabilities.gather(1,action).squeeze(1)+1e-12);
    auto output=at::stack({action.squeeze(1).to(at::kFloat),logp,values}).cpu().contiguous();
    const auto* data=output.data_ptr<float>();size_t count=rows.size();std::vector<Result> result;
    for(size_t i=0;i<count;++i){
        if(!std::isfinite(data[count+i])||!std::isfinite(data[2*count+i]))throw std::runtime_error("nonfinite dual inference");
        result.push_back({static_cast<int>(data[i]),data[count+i],data[2*count+i]});}
    return result;
}

int sample(std::array<torch::jit::Module,2>& policies,const std::array<c10::Device,2>& devices,int games,int batch_size,int size,int turns,uint64_t seed,int threads,const std::filesystem::path& output) {
    if(games<1||batch_size<1||size<8||turns<1||threads<1)throw std::invalid_argument("invalid dual sampler dimensions");
    if(!output.empty()){std::filesystem::create_directories(output/"A");std::filesystem::create_directories(output/"B");}
    const int width=policies[0].run_method("memory_width").toTensor().item<int>();
    if(width!=policies[1].run_method("memory_width").toTensor().item<int>())throw std::invalid_argument("policy memory widths differ");
    std::unique_ptr<rl::TrajectoryWriter> writer;if(!output.empty())writer=std::make_unique<rl::TrajectoryWriter>();
    int started=0,completed=0;uint64_t decisions=0;std::array<uint64_t,2> calls{},rows_total{};
    std::vector<std::unique_ptr<Slot>> slots;
    auto create=[&]{return std::make_unique<Slot>(seed+started++,size,turns,width,devices,output);};
    for(int i=0;i<std::min(games,batch_size);++i)slots.push_back(create());
    const auto start=Clock::now();
    while(!slots.empty()){
        std::vector<std::vector<Request>> per_world(slots.size());std::vector<int> periods(slots.size());
        #pragma omp parallel for schedule(static) num_threads(threads)
        for(int i=0;i<static_cast<int>(slots.size());++i){auto& slot=*slots[i];periods[i]=slot.episode.world.mperiod;
            for(int actor:slot.episode.ready_actors()){
                auto view=slot.episode.observe(actor);int turn=slot.episode.player(actor).turn_index;
                per_world[i].push_back({&slot,actor,turn,std::move(view),slot.event});
            }
        }
        std::array<std::vector<Request*>,2> requests;
        for(auto& world:per_world)for(auto& request:world)requests[owner(request.slot->episode.world.seed,request.actor)].push_back(&request);
        if(requests[0].empty()&&requests[1].empty())throw std::logic_error("dynamic scheduler deadlock");
        std::array<std::future<std::vector<Result>>,2> pending;
        for(int p=0;p<2;++p)pending[p]=std::async(std::launch::async,[&,p]{return infer(policies[p],devices[p],requests[p]);});
        std::array<std::vector<Result>,2> result;
        for(int p=0;p<2;++p){result[p]=pending[p].get();if(!requests[p].empty()){++calls[p];rows_total[p]+=requests[p].size();}}
        // All inference uses detached snapshots. Only this coordinator mutates worlds.
        // Merge in request order, preserving the turn-order precedence inside each conflict group.
        std::array<size_t,2> cursor{};
        for(auto& world:per_world)for(auto& request:world){int policy=owner(request.slot->episode.world.seed,request.actor);
            const auto pick=result[policy].at(cursor[policy]++);auto& e=request.slot->episode;
            auto candidate=request.view.choices.at(pick.action);
            const int previous_steps=e.player(request.actor).steps,previous_invalid=e.player(request.actor).invalid,period=e.world.turn;
            if(writer){auto track=request.slot->tracks[request.actor];writer->submit([track,view=std::move(request.view),turn=request.turn,pick]{track->step(view,turn,pick.action,pick.logp,pick.value);});}
            e.act(request.actor,candidate);++decisions;
            auto& slot=*request.slot;
            if(slot.events.is_open()){
                slot.events<<"{\"kind\":\"decision\",\"event\":"<<slot.event++<<",\"snapshot\":"<<request.snapshot
                    <<",\"turn\":"<<request.turn<<",\"seat\":"<<request.actor<<",\"policy\":"<<policy
                    <<",\"index\":"<<slot.local_index[request.actor]++<<",\"type\":"<<int(candidate.type)
                    <<",\"args\":["<<candidate.args[0]<<','<<candidate.args[1]<<','<<candidate.args[2]<<"],\"executed\":"
                    <<(e.player(request.actor).invalid==previous_invalid?"true":"false")<<"}\n";
            }
            const auto kind=candidate.type;
            if(e.player(request.actor).steps>previous_steps && e.player(request.actor).invalid==previous_invalid &&
               (kind==rl::Type::Build||kind==rl::Type::Demolish||kind==rl::Type::Recruit||kind==rl::Type::Move||kind==rl::Type::Attack||kind==rl::Type::Retreat||kind==rl::Type::Disband||kind==rl::Type::DeclareWar)) {
                std::cout<<"action seed="<<e.world.seed<<" turn="<<period<<" seat="<<request.actor<<" policy="<<(policy?'B':'A')
                         <<" type="<<static_cast<int>(kind)<<" arg0="<<candidate.args[0]<<" arg1="<<candidate.args[1]<<" arg2="<<candidate.args[2]
                         <<" cash="<<e.world.res(request.actor,GOLD)<<" food="<<e.world.res(request.actor,FOOD)<<" equipment="<<e.world.res(request.actor,EQUIP)<<" supply="<<e.world.res(request.actor,SUPPLY)<<'\n';
            }
        }
        for(size_t i=0;i<slots.size();++i){auto& slot=*slots[i];auto& e=slot.episode;auto& w=e.world;
            if(w.mperiod!=periods[i]) {
                if(w.mperiod!=periods[i]+1)throw std::logic_error("multiple settlements in one scheduler wave");
                if(slot.events.is_open()){
                    slot.events<<"{\"kind\":\"settlement\",\"event\":"<<slot.event++<<",\"turn\":"<<e.player(0).turn_index<<",\"nominal_gdp\":[";
                    for(int actor=0;actor<4;++actor){if(actor)slot.events<<',';slot.events<<e.player(actor).last_current_gdp;}
                    slot.events<<"],\"land\":[";
                    for(int actor=0;actor<4;++actor){if(actor)slot.events<<',';slot.events<<std::count_if(w.tiles.begin(),w.tiles.end(),[&](const Tile& t){return t.owner==actor;});}
                    slot.events<<"],\"initial_land\":[";
                    for(int actor=0;actor<4;++actor){if(actor)slot.events<<',';slot.events<<e.player(actor).initial_land;}
                    slot.events<<"]}\n";
                }
                for(int actor=0;actor<4;++actor){const auto& p=e.player(actor);int turn=p.turn_index;
                    int land=std::count_if(w.tiles.begin(),w.tiles.end(),[&](const Tile& tile){return tile.owner==actor;});
                    if(writer){auto track=slot.tracks[actor];writer->submit([track,turn,fixed=p.last_fixed_gdp,current=p.last_current_gdp,first=e.first_player(turn+1)]{track->gdp_turn(turn,fixed,current,first);});}
                    std::cout<<"market seed="<<w.seed<<" turn="<<turn+1<<" first="<<e.first_player(turn+1);
                    for(double price:w.price)std::cout<<' '<<price;
                    std::cout<<" seat="<<actor<<" land="<<land<<" initial_land="<<p.initial_land<<" policy="<<(owner(w.seed,actor)?'B':'A')<<'\n';
                }
            }
            if(!e.done)continue;++completed;
            if(slot.events.is_open()){slot.events.flush();if(!slot.events)throw std::runtime_error("global event write failed");}
            for(int actor=0;actor<4;++actor){const auto& p=e.player(actor);const auto& n=w.nations[actor];
                if(writer){auto track=slot.tracks[actor];writer->submit([track,reward=e.reward(actor),actor,turn=w.turn]{track->finish(reward,-1,actor,turn,rl::Opponent::Rule);});}
                int land=std::count_if(w.tiles.begin(),w.tiles.end(),[&](const Tile& tile){return tile.owner==actor;});
                int regular=0,militia=0,queued=0;double hp=0;
                for(const auto& unit:w.armies)if(unit.owner==actor&&unit.hp>0){if(unit.kind==MIL)++militia;else ++regular;hp+=unit.hp;}
                for(const auto& unit:w.recruit_reservations)queued+=unit.owner==actor;
                std::cout<<"game seed="<<w.seed<<" opp=market4_selfplay seat="<<actor<<" turns="<<w.turn<<" winner=-1 reward="<<p.total_fixed_gdp<<" decisions="<<p.decisions<<" invalid="<<p.invalid
                         <<" current_gdp="<<p.total_current_gdp<<" blackout="<<p.blackout_turns<<" land="<<land<<" initial_land="<<p.initial_land<<" peak_land="<<p.peak_land
                         <<" recruits="<<p.recruits<<" attacks="<<p.attacks<<" disbands="<<p.disbands<<" supply_short_turns="<<n.supply_short_turns<<" starved_units="<<n.starved_units
                         <<" cash="<<n.res[GOLD]<<" supply="<<n.res[SUPPLY]<<" policy="<<(owner(w.seed,actor)?'B':'A')<<'\n';
                std::cout<<"military_end seed="<<w.seed<<" seat="<<actor<<" regular="<<regular<<" militia="<<militia<<" queued="<<queued<<" total_hp="<<hp<<'\n';
            }
            slots[i]=started<games?create():nullptr;
        }
        slots.erase(std::remove(slots.begin(),slots.end(),nullptr),slots.end());
    }
    if(writer)writer->join();const double seconds=std::chrono::duration<double>(Clock::now()-start).count();
    for(int p=0;p<2;++p)std::cout<<"inference policy="<<(p?'B':'A')<<" calls="<<calls[p]<<" rows="<<rows_total[p]<<" mean_batch="<<double(rows_total[p])/std::max<uint64_t>(1,calls[p])<<'\n';
    std::cout<<"completed="<<completed<<" decisions="<<decisions<<" seconds="<<seconds<<" decisions_per_second="<<decisions/seconds<<std::endl;
    return 0;
}
}

int main(int argc,char** argv) {
    if(argc!=12){std::cerr<<"usage: zhanguo_dual MODEL_A MODEL_B DEVICE_A DEVICE_B GAMES BATCH SIZE TURNS SEED THREADS OUTPUT_OR_DASH\n";return 2;}
    try {c10::InferenceMode inference;at::set_num_threads(1);at::set_num_interop_threads(1);at::manual_seed(std::stoull(argv[9]));
        std::array<c10::Device,2> devices{c10::Device(argv[3]),c10::Device(argv[4])};
        std::array<torch::jit::Module,2> policies{torch::jit::load(argv[1],devices[0]),torch::jit::load(argv[2],devices[1])};
        for(auto& p:policies)p.eval();std::cout<<std::setprecision(10);
        return sample(policies,devices,std::stoi(argv[5]),std::stoi(argv[6]),std::stoi(argv[7]),std::stoi(argv[8]),std::stoull(argv[9]),std::stoi(argv[10]),std::string(argv[11])=="-"?std::filesystem::path{}:std::filesystem::path(argv[11]));
    } catch(const std::exception& e){std::cerr<<"dual sampler error: "<<e.what()<<std::endl;return 1;}
}
