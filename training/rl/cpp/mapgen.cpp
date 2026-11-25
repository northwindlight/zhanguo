// 地图生成：BLAKE2b + fBm + 蓝噪声 + 自然边界 + 配额落位。与 main 逐格等价。
#include "engine.hpp"
#include <cstdio>

// ---------------- BLAKE2b（digest_size=8，与 Python hashlib 同参） ----------------
namespace {
const uint64_t IV[8] = {
    0x6a09e667f3bcc908ULL, 0xbb67ae8584caa73bULL, 0x3c6ef372fe94f82bULL,
    0xa54ff53a5f1d36f1ULL, 0x510e527fade682d1ULL, 0x9b05688c2b3e6c1fULL,
    0x1f83d9abfb41bd6bULL, 0x5be0cd19137e2179ULL};
const uint8_t SIGMA[12][16] = {
    {0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15},
    {14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3},
    {11,8,12,0,5,2,15,13,10,14,3,6,7,1,9,4},
    {7,9,3,1,13,12,11,14,2,6,5,10,4,0,15,8},
    {9,0,5,7,2,4,10,15,14,1,11,12,6,8,3,13},
    {2,12,6,10,0,11,8,3,4,13,7,5,15,14,1,9},
    {12,5,1,15,14,13,4,10,0,7,6,3,9,2,8,11},
    {13,11,7,14,12,1,3,9,5,0,15,4,8,6,2,10},
    {6,15,14,9,11,3,0,8,12,2,13,7,1,4,10,5},
    {10,2,8,4,7,6,1,5,15,11,9,14,3,12,13,0},
    {0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15},
    {14,10,4,8,9,15,13,6,1,12,0,2,11,7,5,3}};
inline uint64_t ror64(uint64_t x, int n) { return (x >> n) | (x << (64 - n)); }
inline void G(uint64_t* v, int a, int b, int c, int d, uint64_t x, uint64_t y) {
    v[a] = v[a] + v[b] + x; v[d] = ror64(v[d] ^ v[a], 32);
    v[c] = v[c] + v[d];     v[b] = ror64(v[b] ^ v[c], 24);
    v[a] = v[a] + v[b] + y; v[d] = ror64(v[d] ^ v[a], 16);
    v[c] = v[c] + v[d];     v[b] = ror64(v[b] ^ v[c], 63);
}
void compress(uint64_t h[8], const uint8_t block[128], uint64_t t, bool last) {
    uint64_t v[16], m[16];
    for (int i = 0; i < 8; i++) v[i] = h[i], v[i + 8] = IV[i];
    v[12] ^= t;
    if (last) v[14] = ~v[14];
    for (int i = 0; i < 16; i++) {
        uint64_t w = 0;
        for (int b = 7; b >= 0; b--) w = (w << 8) | block[i * 8 + b];
        m[i] = w;
    }
    for (int r = 0; r < 12; r++) {
        const uint8_t* s = SIGMA[r];
        G(v, 0, 4, 8, 12, m[s[0]], m[s[1]]);
        G(v, 1, 5, 9, 13, m[s[2]], m[s[3]]);
        G(v, 2, 6, 10, 14, m[s[4]], m[s[5]]);
        G(v, 3, 7, 11, 15, m[s[6]], m[s[7]]);
        G(v, 0, 5, 10, 15, m[s[8]], m[s[9]]);
        G(v, 1, 6, 11, 12, m[s[10]], m[s[11]]);
        G(v, 2, 7, 8, 13, m[s[12]], m[s[13]]);
        G(v, 3, 4, 9, 14, m[s[14]], m[s[15]]);
    }
    for (int i = 0; i < 8; i++) h[i] ^= v[i] ^ v[i + 8];
}
}  // namespace

uint64_t blake2b8(const std::string& in) {
    uint64_t h[8];
    for (int i = 0; i < 8; i++) h[i] = IV[i];
    h[0] ^= 0x01010000ULL ^ (uint64_t)8;   // outlen=8, keylen=0, fanout=1, depth=1
    std::string data = in;
    size_t off = 0;
    uint64_t total = 0;
    while (data.size() - off > 128) {
        total += 128;
        compress(h, (const uint8_t*)data.data() + off, total, false);
        off += 128;
    }
    uint8_t block[128] = {0};
    size_t remain = data.size() - off;
    std::memcpy(block, data.data() + off, remain);
    compress(h, block, total + (uint64_t)remain, true);
    uint64_t out = 0;   // 前 8 字节 big-endian
    for (int i = 0; i < 8; i++) out = (out << 8) | block[i];
    // 注意：BLAKE2b 输出是 h 的小端字节串；digest = LE(h[0..7])，取前 8 字节
    uint8_t digest[64];
    for (int i = 0; i < 8; i++)
        for (int b = 0; b < 8; b++) digest[i * 8 + b] = (uint8_t)(h[i] >> (8 * b));
    uint64_t r = 0;
    for (int i = 0; i < 8; i++) r = (r << 8) | digest[i];
    return r;
}
namespace {
template <typename... A> uint64_t H(A... parts) {
    std::string blob;
    bool first = true;
    auto add = [&](const std::string& s) {
        if (!first) blob += ':';
        blob += s;
        first = false;
    };
    (add(std::to_string(parts)), ...);
    return blake2b8(blob);
}
uint64_t Hstr(uint64_t seed, const char* salt, int x, int y) {
    return blake2b8(std::to_string(seed) + ":" + salt + ":" + std::to_string(x) + ":" + std::to_string(y));
}
uint64_t Hname(uint64_t seed, int size, const std::string& name) {
    return blake2b8(std::to_string(seed) + ":name-order:" + std::to_string(size) + ":" + name);
}
const double U24 = 16777216.0;
double unit(uint64_t h) { return (double)(h % 16777216ULL) / U24; }

double value_noise(uint64_t seed, const char* salt, double x, double y) {
    int x0 = (int)std::floor(x), y0 = (int)std::floor(y);
    double tx = x - x0, ty = y - y0;
    double sx = tx * tx * (3 - 2 * tx), sy = ty * ty * (3 - 2 * ty);
    auto g = [&](int dx, int dy) { return unit(Hstr(seed, salt, x0 + dx, y0 + dy)); };
    double a = g(0, 0), b = g(1, 0), c = g(0, 1), d = g(1, 1);
    return (a * (1 - sx) + b * sx) * (1 - sy) + (c * (1 - sx) + d * sx) * sy;
}
double fbm(uint64_t seed, const char* salt, int x, int y, int octaves) {
    double total = 0, amp = 1, freq = 0.22, norm = 0;
    for (int i = 0; i < octaves; i++) {
        char s[16];
        std::snprintf(s, sizeof s, "%s%d", salt, i);
        total += amp * value_noise(seed, s, x * freq, y * freq);
        norm += amp;
        amp *= 0.5;
        freq *= 2.0;
    }
    return total / norm;
}
void norm2d(std::vector<double>& f) {
    double lo = f[0], hi = f[0];
    for (double v : f) { lo = std::min(lo, v); hi = std::max(hi, v); }
    double span = hi > lo ? hi - lo : 1.0;
    for (double& v : f) v = (v - lo) / span;
}
std::vector<double> blue_rank(uint64_t seed, int size, const std::string& salt, int radius) {
    int n = size * size;
    std::vector<double> rank(n, 0.0);
    std::vector<char> done(n, 0);
    std::vector<int> ord(n);
    std::iota(ord.begin(), ord.end(), 0);
    std::sort(ord.begin(), ord.end(), [&](int a, int b) {
        return Hstr(seed, salt.c_str(), a / size, a % size) <
               Hstr(seed, salt.c_str(), b / size, b % size);
    });
    int placed = 0;
    for (int r = radius; r >= 0; r--) {
        std::vector<char> blocked(n, 0);
        for (int i : ord) {
            if (done[i] || blocked[i]) continue;
            int x = i % size, y = i / size;
            done[i] = 1;
            rank[i] = (double)(n - 1 - placed) / n;
            placed++;
            for (int dy = -r; dy <= r; dy++)
                for (int dx = -r; dx <= r; dx++) {
                    int nx = x + dx, ny = y + dy;
                    if (nx >= 0 && nx < size && ny >= 0 && ny < size) blocked[ny * size + nx] = 1;
                }
        }
    }
    return rank;
}
}  // namespace

// ---------------- 生成 ----------------
void World::gen_map() {
    tiles.assign(size * size, Tile{});
    materialized.assign(size * size, 0);
    tile_order.clear();
    int N = size * size;
    std::vector<double> elev(N), moist(N);
    for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++) {
            elev[y * size + x] = fbm(seed, "e", x, y, 4);
            moist[y * size + x] = fbm(seed, "m", x, y, 3);
        }
    norm2d(elev); norm2d(moist);

    auto mask = blue_rank(seed, size, "terrain", 2);
    tmask = mask;
    rmask.assign(TR_N, {});
    for (int r = 0; r < TR_N; r++) {
        static const char* salt[TR_N] = {"res:native:ore", "res:native:gold", "res:native:farmland",
                                         "res:native:oil", "res:native:wood"};
        rmask[r] = blue_rank(seed, size, salt[r], 2);
    }

    // 打分
    std::vector<std::array<double, 5>> nu(N);
    for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++) {
            double e = elev[y * size + x], m = moist[y * size + x];
            double r_mtn = std::min(1.0, std::max(0.0, (e - 0.80) / 0.12));
            // 索引按 Terrain 枚举：PLAIN0 FOREST1 HILL2 MOUNTAIN3 DESERT4
            nu[y * size + x] = {
                0.0,
                std::min(1.0, std::max(0.0, (m - 0.68) / 0.12)),
                std::min(1.0, std::max(0.0, (e - 0.52) / 0.12)) * (1 - r_mtn),
                r_mtn,
                std::min(1.0, std::max(0.0, (0.32 - m) / 0.12))};
            auto& d = nu[y * size + x];
            d[0] = 1.0 - std::max({d[1], d[2], d[3], d[4]});
        }

    // ---- 自然边界 ----
    std::vector<double> coast(N), waves(N);
    for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++) {
            coast[y * size + x] = value_noise(seed, "coast", x * 0.07, y * 0.07);
            waves[y * size + x] = value_noise(seed, "rim", x * 0.19, y * 0.19);
        }
    norm2d(waves);
    std::vector<double> rim;
    for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
            if (std::min({x, y, size - 1 - x, size - 1 - y}) == 0) rim.push_back(coast[y * size + x]);
    std::sort(rim.begin(), rim.end());
    double level = rim[rim.size() / 2];

    std::unordered_map<int, int> result;      // idx -> terrain(5/6)
    std::unordered_map<int, double> strength;
    std::vector<int> outer;
    auto is_rim = [&](int x, int y) {
        return std::min({x, y, size - 1 - x, size - 1 - y}) == 0;
    };
    for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++) {
            int i = y * size + x;
            int edge = std::min({x, y, size - 1 - x, size - 1 - y});
            double wave = waves[i];
            double width = std::max(1.0, size * 0.055 * (0.5 + wave));
            bool low = coast[i] <= level;
            double cs = size * 0.10 * std::max(0.0, (level - coast[i]) / std::max(level, 1e-9));
            bool border;
            if (size >= 16) {
                double half = (size - 1) / 2.0;
                double nx = (x - half) / half + (value_noise(seed, "land-x", x * .08, y * .08) - .5) * 0.20;
                double ny = (y - half) / half + (value_noise(seed, "land-y", x * .08, y * .08) - .5) * 0.20;
                double radius = std::pow(std::pow(std::abs(nx), 2.2) + std::pow(std::abs(ny), 2.2),
                                         1.0 / 2.2);
                double limit = 0.90 + (wave - .5) * .16;
                border = edge == 0 || radius >= limit;
            } else {
                border = edge < std::max(width, cs) && (low || edge < width);
            }
            if (border) {
                result[i] = low ? OCEAN : HIGH_MTN;
                outer.push_back(i);
                strength[i] = (std::max(width, cs) - edge) / std::max(width, cs);
            } else if (size >= 12 && elev[i] >= 0.86) {
                result[i] = HIGH_MTN;
                strength[i] = .2 + 3 * (elev[i] - 0.86);
            }
        }
    // 小图预算修剪
    int rim_cnt = 0;
    for (auto& kv : result) {
        int i = kv.first;
        if (is_rim(i % size, i / size)) rim_cnt++;
    }
    int budget = std::max(rim_cnt, (int)(N * 0.20));
    if (size < 16) {
        std::vector<int> extra;
        for (auto& kv : result)
            if (!is_rim(kv.first % size, kv.first / size)) extra.push_back(kv.first);
        std::sort(extra.begin(), extra.end(), [&](int a, int b) {
            if (strength[a] != strength[b]) return strength[a] > strength[b];
            return a < b;
        });
        int drop_from = std::max(0, budget - rim_cnt);
        for (int k = drop_from; k < (int)extra.size(); k++) result.erase(extra[k]);
    } else {
        // 山脊雕刻：BFS 距离
        std::vector<int> dist(N, -1);
        std::vector<int> queue;
        for (int i = 0; i < N; i++)
            if (!result.count(i)) { dist[i] = 0; queue.push_back(i); }
        for (size_t q = 0; q < queue.size(); q++) {
            int i = queue[q], x = i % size, y = i / size;
            int nb[4][2] = {{x - 1, y}, {x + 1, y}, {x, y - 1}, {x, y + 1}};
            for (auto& p : nb) {
                if (p[0] < 0 || p[0] >= size || p[1] < 0 || p[1] >= size) continue;
                int j = p[1] * size + p[0];
                if (dist[j] < 0) { dist[j] = dist[i] + 1; queue.push_back(j); }
            }
        }
        std::sort(outer.begin(), outer.end());
        for (int i : outer) {
            int x = i % size, y = i / size;
            if (is_rim(x, y) || (result[i] == HIGH_MTN && (dist[i] < 0 || dist[i] > 2)))
                result[i] = OCEAN;   // 未到达的屏障格（main: distances.get(p,size) > 2）
        }
    }
    // 海连通分量：不触外圈 → 删
    {
        std::vector<char> water(N, 0);
        for (auto& kv : result) if (kv.second == OCEAN) water[kv.first] = 1;
        for (int i = 0; i < N; i++) {
            if (!water[i]) continue;
            std::vector<int> group{i};
            water[i] = 0;
            for (size_t q = 0; q < group.size(); q++) {
                int j = group[q], x = j % size, y = j / size;
                int nb[4][2] = {{x - 1, y}, {x + 1, y}, {x, y - 1}, {x, y + 1}};
                for (auto& p : nb) {
                    if (p[0] < 0 || p[0] >= size || p[1] < 0 || p[1] >= size) continue;
                    int k = p[1] * size + p[0];
                    if (water[k]) { water[k] = 0; group.push_back(k); }
                }
            }
            bool touches_rim = false;
            for (int j : group) if (is_rim(j % size, j / size)) touches_rim = true;
            if (!touches_rim)
                for (int j : group) result.erase(j);
        }
    }
    // 陆连通分量：留最大，其余封山
    {
        std::vector<char> land(N, 0);
        for (int i = 0; i < N; i++) if (!result.count(i)) land[i] = 1;
        std::vector<std::vector<int>> groups;
        for (int i = 0; i < N; i++) {
            if (!land[i]) continue;
            std::vector<int> g{i};
            land[i] = 0;
            for (size_t q = 0; q < g.size(); q++) {
                int j = g[q], x = j % size, y = j / size;
                int nb[4][2] = {{x - 1, y}, {x + 1, y}, {x, y - 1}, {x, y + 1}};
                for (auto& p : nb) {
                    if (p[0] < 0 || p[0] >= size || p[1] < 0 || p[1] >= size) continue;
                    int k = p[1] * size + p[0];
                    if (land[k]) { land[k] = 0; g.push_back(k); }
                }
            }
            groups.push_back(std::move(g));
        }
        if (!groups.empty()) {
            size_t best = 0;
            for (size_t k = 1; k < groups.size(); k++) {
                auto& a = groups[k];
                auto& b = groups[best];
                if (a.size() != b.size()) { if (a.size() > b.size()) best = k; continue; }
                std::vector<int> sa = a, sb = b;
                std::sort(sa.begin(), sa.end()); std::sort(sb.begin(), sb.end());
                if (sa > sb) best = k;
            }
            for (size_t k = 0; k < groups.size(); k++) {
                if (k == best) continue;
                for (int i : groups[k]) result[i] = HIGH_MTN;
            }
        }
    }

    // ---- 落位 ----
    std::vector<int> assign(N, -1);
    for (auto& kv : result) assign[kv.first] = kv.second;
    int vacancies = N - (int)result.size();
    // 配额：最大余数法
    int weights[5] = {30, 25, 20, 15, 10};
    int quota[5];
    {
        double tot = 100.0, raw[5];
        int base = 0;
        for (int i = 0; i < 5; i++) { raw[i] = vacancies * weights[i] / tot; quota[i] = (int)raw[i]; base += quota[i]; }
        int rest = vacancies - base;
        std::vector<int> idx(5);
        std::iota(idx.begin(), idx.end(), 0);
        // main `_quotas`：余数大者先补，平局按**名字字符串序**（desert<forest<hill<mountain<plain）
        auto name_rank = [](int i) {
            static const int r[5] = {4, 1, 2, 3, 0};   // PLAIN0→4 FOREST1→1 HILL2→2 MTN3→3 DESERT4→0
            return r[i];
        };
        std::sort(idx.begin(), idx.end(), [&](int a, int b) {
            double ra = raw[a] - (int)raw[a], rb = raw[b] - (int)raw[b];
            if (ra != rb) return ra > rb;
            return name_rank(a) < name_rank(b);
        });
        for (int k = 0; k < rest; k++) quota[idx[k]]++;
    }
    // 稀有先挑：沙漠10 山地15 丘陵20 森林25（地形号：FOREST1,HILL2,MOUNTAIN3,DESERT4）
    int torder[4] = {DESERT, MOUNTAIN, HILL, FOREST};
    for (int ti = 0; ti < 4; ti++) {
        int t = torder[ti];
        struct Key { int score; double rank; int x, y; };
        std::vector<Key> keyed;
        keyed.reserve(N);
        for (int y = 0; y < size; y++)
            for (int x = 0; x < size; x++) {
                int i = y * size + x;
                if (assign[i] != -1) continue;
                keyed.push_back({-(int)(nu[i][t] * 100), -mask[i], x, y});
            }
        std::sort(keyed.begin(), keyed.end(), [](const Key& a, const Key& b) {
            if (a.score != b.score) return a.score < b.score;
            if (a.rank != b.rank) return a.rank < b.rank;
            if (a.x != b.x) return a.x < b.x;
            return a.y < b.y;
        });
        int take = quota[t == FOREST ? 1 : t == HILL ? 2 : t == MOUNTAIN ? 3 : 4];
        for (int k = 0; k < (int)keyed.size() && k < take; k++)
            assign[keyed[k].y * size + keyed[k].x] = t;
    }
    for (int i = 0; i < N; i++) if (assign[i] == -1) assign[i] = PLAIN;

    // ---- 资源 ----
    static const int TERRAINS_W[TERRAIN_N][TR_N][6] = {
        // 平原 矿/金/耕/油/木
        {{80,16,3,1,0,0},{96,4,0,0,0,0},{6,26,32,21,11,4},{92,7,1,0,0,0},{20,47,21,9,3,0}},
        // 森林
        {{74,19,6,1,0,0},{95,5,0,0,0,0},{28,30,26,11,4,1},{95,4,1,0,0,0},{0,20,29,28,17,6}},
        // 丘陵
        {{18,28,25,17,8,4},{88,11,1,0,0,0},{32,30,24,10,3,1},{80,15,4,1,0,0},{15,38,25,14,6,2}},
        // 山地
        {{4,18,29,26,16,7},{76,20,4,0,0,0},{72,20,6,2,0,0},{74,20,5,1,0,0},{45,41,11,3,0,0}},
        // 沙漠
        {{68,21,8,3,0,0},{89,10,1,0,0,0},{90,7,2,1,0,0},{12,26,28,26,8},{90,9,1,0,0,0}},
        // 高山
        {{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0}},
        // 海洋
        {{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0}},
    };
    auto dither = [](double rank, const int* w) -> int {
        int tot = 0;
        for (int i = 0; i < 6; i++) tot += w[i];
        double acc = 0;
        for (int i = 0; i < 6; i++) {
            acc += w[i];
            if (rank < acc / tot) return i;
        }
        return 5;
    };
    for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++) {
            int i = y * size + x, t = assign[i];
            tile(x, y).terrain = t;
            for (int r = 0; r < TR_N; r++)
                tile(x, y).map_resources[r] = tile(x, y).tres[r] = dither(rmask[r][i], TERRAINS_W[t][r]);
        }
}

std::array<int, TR_N> World::roll_res(int x, int y, Terrain t) const {
    static const int TERRAINS_W[TERRAIN_N][TR_N][6] = {
        {{80,16,3,1,0,0},{96,4,0,0,0,0},{6,26,32,21,11,4},{92,7,1,0,0,0},{20,47,21,9,3,0}},
        {{74,19,6,1,0,0},{95,5,0,0,0,0},{28,30,26,11,4,1},{95,4,1,0,0,0},{0,20,29,28,17,6}},
        {{18,28,25,17,8,4},{88,11,1,0,0,0},{32,30,24,10,3,1},{80,15,4,1,0,0},{15,38,25,14,6,2}},
        {{4,18,29,26,16,7},{76,20,4,0,0,0},{72,20,6,2,0,0},{74,20,5,1,0,0},{45,41,11,3,0,0}},
        {{68,21,8,3,0,0},{89,10,1,0,0,0},{90,7,2,1,0,0},{12,26,28,26,8},{90,9,1,0,0,0}},
        {{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0}},
        {{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0},{100,0,0,0,0,0}},
    };
    std::array<int, TR_N> out{};
    int i = y * size + x;   // blue_rank 是 y*size+x 布局
    for (int r = 0; r < TR_N; r++) {
        const int* w = TERRAINS_W[t][r];
        int tot = 0;
        for (int k = 0; k < 6; k++) tot += w[k];
        double acc = 0;
        int v = 5;
        for (int k = 0; k < 6; k++) {
            acc += w[k];
            if (rmask[r][i] < acc / tot) { v = k; break; }
        }
        out[r] = v;
    }
    return out;
}

