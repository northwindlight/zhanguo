#include "rollout.hpp"

#include <iomanip>
#include <iostream>

int main(int argc, char** argv) {
    if (argc < 4 || argc > 5) return 2;
    const auto seed = std::stoull(argv[1]);
    const int size = std::stoi(argv[2]);
    const int turns = std::stoi(argv[3]);
    rl::Episode ep(seed, size, turns);
    PyRandom random;
    random.seed(seed ^ 0x12345678);
    bool sample_learner = argc == 5;
    auto draw = [&](unsigned count) {
        int bits = 0;
        for (unsigned x = count; x; x >>= 1) ++bits;
        unsigned selected;
        do { selected = random.getrandbits(bits); } while (selected >= count);
        return selected;
    };
    std::cout << std::setprecision(15);
    std::cout << ep.seat << ' ' << rl::opponent_name(ep.opponent) << '\n';
    while (!ep.done) {
        auto& w = ep.world;
        std::cout << "turn " << w.turn << '\n';
        for (const auto& n : w.nations) {
            std::cout << "stock " << n.id;
            for (auto v : n.res) std::cout << ' ' << v;
            std::cout << '\n';
        }
        for (size_t i = 0; i < w.tiles.size(); ++i) {
            const auto& t = w.tiles[i];
            if (t.owner < 0) continue;
            std::cout << "tile " << i / w.size << ' ' << i % w.size << ' ' << t.owner << ' ' << t.terrain;
            for (int r : t.tres) std::cout << ' ' << r;
            for (int b : t.bld) std::cout << ' ' << b;
            for (int b : t.pending) std::cout << ' ' << b;
            std::cout << '\n';
        }
        for (const auto& a : w.armies)
            if (a.owner >= 0)
                std::cout << "army " << a.owner << ' ' << a.aid << ' ' << a.x << ' ' << a.y << ' '
                          << a.kind << ' ' << a.hp << ' ' << a.moved_turn << ' ' << a.engaged << '\n';
        for (const auto& p : w.proposals)
            std::cout << "proposal " << p.kind << ' ' << p.a << ' ' << p.b << '\n';
        int current = w.turn;
        for (int step = 0; sample_learner && w.turn == current && !ep.done && step < 12; ++step) {
            auto view = ep.observe();
            unsigned i = draw(view.choices.size());
            std::cout << "action " << i << ' ' << static_cast<int>(view.choices[i].type) << '\n';
            auto type = view.choices[i].type;
            ep.act(view.choices[i]);
            if (type == rl::Type::EndTurn) break;
        }
        if (w.turn == current && !ep.done) ep.act(rl::Candidate{rl::Type::EndTurn});
    }
    std::cout << "done " << ep.winner() << ' ' << ep.world.turn << ' ' << ep.reward() << '\n';
}
