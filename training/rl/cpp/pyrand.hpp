// Python 兼容的 MT19937（random.Random(seed) 的 randint(1,6) 序列），用于逐位对齐 main 的战斗骰子。
#pragma once
#include <cstdint>
#include <algorithm>

struct PyRandom {
    uint32_t mt[624];
    int mti = 624;

    void init_genrand(uint32_t s) {
        mt[0] = s & 0xffffffffU;
        for (int i = 1; i < 624; i++)
            mt[i] = (1812433253U * (mt[i - 1] ^ (mt[i - 1] >> 30)) + (uint32_t)i) & 0xffffffffU;
        mti = 624;
    }

    // CPython _randommodule.c init_by_array
    void init_by_array(const uint32_t* key, int len) {
        init_genrand(19650218U);
        int i = 1, j = 0, k = std::max(624, len);
        for (; k; k--) {
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1664525U)) + key[j] + (uint32_t)j) & 0xffffffffU;
            i++; j++;
            if (i >= 624) { mt[0] = mt[623]; i = 1; }
            if (j >= len) j = 0;
        }
        for (k = 623; k; k--) {
            mt[i] = ((mt[i] ^ ((mt[i - 1] ^ (mt[i - 1] >> 30)) * 1566083941U)) - (uint32_t)i) & 0xffffffffU;
            i++;
            if (i >= 624) { mt[0] = mt[623]; i = 1; }
        }
        mt[0] = 0x80000000U;
    }

    void seed(uint64_t s) {
        if (s <= 0xffffffffULL) {
            uint32_t k[1] = {(uint32_t)s};
            init_by_array(k, 1);
        } else {
            uint32_t k[2] = {(uint32_t)(s & 0xffffffffULL), (uint32_t)(s >> 32)};
            init_by_array(k, 2);
        }
        mti = 624;
    }

    uint32_t genrand_uint32() {
        if (mti >= 624) {
            for (int kk = 0; kk < 624; kk++) {
                uint32_t y = (mt[kk] & 0x80000000U) | (mt[(kk + 1) % 624] & 0x7fffffffU);
                mt[kk] = mt[(kk + 397) % 624] ^ (y >> 1) ^ ((y & 1U) ? 0x9908b0dfU : 0U);
            }
            mti = 0;
        }
        uint32_t y = mt[mti++];
        y ^= y >> 11;
        y ^= (y << 7) & 0x9d2c5680U;
        y ^= (y << 15) & 0xefc60000U;
        y ^= y >> 18;
        return y;
    }

    // random.getrandbits(k)，k<=32
    uint32_t getrandbits(int k) { return genrand_uint32() >> (32 - k); }

    // random.randint(1, 6)
    int randint_1_6() {
        uint32_t r;
        do { r = getrandbits(3); } while (r >= 6);
        return (int)r + 1;
    }
};
