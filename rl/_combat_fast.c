/* RL 线自带的战斗 DP 加速扩展（可选；编不出来就整条回落纯 Python）。
 *
 * ★★ **与主线 `mods/experimental/combat_research/_combat_fast.c` 的关系**
 *
 *   主线那份是**从本文件移植过去的**（它的文件头写着
 *   "Research DP port from origin/feat/rl-tactics:rl/_combat_fast.c"）。
 *   但主线那份**还带着旧的整数摊分**（见下）⇒ 本分支**不能直接用**它。
 *
 *   ★ **本分支的纪律：引擎文件与 main 逐字一致** ⇒ 本文件放在 `rl/` 下，
 *     **不动** `mods/`（主线的 solver 继续用它自己的窄口径，行为不变）。
 *     ⇒ 代价：这份 C 是**主线那份的一个分支**（fork）——
 *     主线以后改了摊分口径，本分支要**自己**跟一次（记在 `rl/PLAN.md` §0.7）。
 *
 * ★★ **本文件修好的那件事：摊分（spread）**
 *
 *   引擎（`mods/native/army/domain_objects.py` 的 `Military.spread_damage` +
 *   `equal_allocation`）用的是**浮点注水式均分**：
 *       ratios = cover/100
 *       caps   = hp/ratio（ratio=0 ⇒ 不限）
 *       shares = equal_allocation(damage, caps)   ← **浮点**
 *       hp    -= share*ratio
 *   而**旧 C** 用的是**整数** divmod（`left/live` 的整数商 + 余数给前排）
 *   ⇒ **多方局会差 1 点血**（实测 20000 组随机局面里 35% 有差）
 *   ⇒ 主线与 RL 线都只能把 C 路径限死在"**每方只有一支军**"（那时整数=浮点）。
 *
 *   ⇒ 本文件把摊分改成**与引擎逐位同构的浮点注水**（`engine_spread`），
 *     并把 `hp` 从 int 改成 **double**（引擎掉血后是 96.5 这种小数）。
 *     ⇒ C 路径可以在**任意编队**上用了（多方、带撤退减伤都行），
 *       逐位一致性由 `tests/test_rl_dp_identity.py` 那份**冻结基准**钉死。
 *
 * ★ 逐位相同的四个条件（其余照抄主线那份，缺一个就会静默改掉观测特征）：
 *   ① **组合序** = `itertools.product(DIE_FACES, repeat=n)`：**最后一方变化最快**，
 *      `p_die` 的累加次序由它决定。
 *   ② **`p_die = 1.0 / nf**n`**：`nf**n` 在 Python 那边是**整数**（6^8=1679616，
 *      远小于 2^53 ⇒ 转 double 精确），除法两边都是"正确舍入" ⇒ 同一位型。
 *   ③ **两次独立舍入**：`share = power/ne` 一次、`share*(100-soak)/100` 两次
 *      （先乘后除，Python 的 `*` `/` 同优先左结合）。⇒ 编译必须
 *      `-ffp-contract=off`（已在 build 脚本里），否则 aarch64 把两次并成一次。
 *      `round()` = **就近、ties-to-even**（银行家舍入）⇒ 这里手写 `py_round`
 *      而不是用 `rint`：`rint` 跟**当前舍入模式**走，而 Python 的 `round` 不跟。
 *   ④ **伤害向量按首次触达序**返回（= Python dict 的插入序），`rec` 那边的
 *      `PyDict_Next` 才和纯 Python 的 `for dv, p in agg.items()` 同序。
 *   ★★ **⑤（本次新增）摊分的浮点求和次序**：Python 的 `equal_allocation` 里
 *      `active` 是 **`set`**（小整数 ⇒ 迭代序 = **升序**）⇒ C 必须**升序**扫，
 *      否则 `spent` 的求和次序不同 ⇒ 位型不同（1 ulp 级别）。
 *
 * Build: python3 rl/build_combat_fast.py
 * Combat power and defense are supplied by the Python domain adapter.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ 小工具 */
static void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) Py_FatalError("_combat_fast: 内存不足");
    return p;
}
static void *xcalloc(size_t n, size_t s) {
    void *p = calloc(n ? n : 1, s ? s : 1);
    if (!p) Py_FatalError("_combat_fast: 内存不足");
    return p;
}

/* 只增不释放的 bump 分配器：一趟 DP 跑完整体丢掉，逐个 free 纯属浪费。
 *
 * ★★ **必须是分块的，绝不能对已有缓冲 `realloc`** —— `Res*` 与各数组的指针会发给
 *    memo 表和递归的父层，`realloc` 一搬移它们**全变野指针**。
 *    （踩过：小局面侥幸没搬、5 方局直接段错误 —— 因为只有大局面才真的搬。）
 *    所以：当前块装不下就**再开一块**（老块原地不动），块之间用链表串起来。 */
typedef struct Block {
    struct Block *next;
    size_t cap, len;
    char buf[];
} Block;
typedef struct {
    Block *head;
    size_t blocksize;
} Arena;
static void *arena_alloc(Arena *a, size_t n) {
    n = (n + 15) & ~(size_t)15;
    if (!a->head || a->head->len + n > a->head->cap) {
        size_t cap = a->blocksize ? a->blocksize : (1u << 20);
        if (cap < n) cap = n;
        Block *b = (Block *)xmalloc(sizeof(Block) + cap);
        b->next = a->head;
        b->cap = cap;
        b->len = 0;
        a->head = b;
    }
    void *p = a->head->buf + a->head->len;
    a->head->len += n;
    return p;
}
static void arena_free(Arena *a) {
    Block *b = a->head;
    while (b) {
        Block *nx = b->next;
        free(b);
        b = nx;
    }
    a->head = NULL;
}

/* ------------------------------------------------------------------ 数据结构 */
/* ★★ `hp` 是 **double**：引擎掉血后是 96.5 这种小数（浮点注水的份额 × 减伤比例），
 *   用 int 存**必然**与引擎对不上（差 1 点血）⇒ C 路径以前只能限死在
 *   "每方一支军"（那时摊分退化成全打在那支军身上，int 恰好够用）。 */
typedef struct {
    int kind, retreat, soak;
    double hp;
} Unit;
typedef struct {
    Unit *units;
    int n;
} Side;

/* 每方军队数的上限（摊分用的定长缓冲）。超了**响亮报错**，不让它越界写。 */
#define MAX_UNITS 8

/* 引擎口径的**浮点注水**摊分（定义在文件后半，见那里的逐条说明）。 */
static void engine_spread(long damage, const Unit *units, int cnt, double *hp);

/* 一个状态的 DP 结果。★ 两个分布都是**稀疏 + 保序**的。 */
typedef struct Res {
    double *dist;          /* 按位掩码（= 幸存者集合）索引，长 1<<n */
    unsigned char *dtouch; /* 首次**触达**（不是首次非零）—— 复刻 dict 的键集合 */
    int *dorder;           /* 首次触达序 ⇒ `sum(dist.values())` 按它走 */
    int dn;
    double *rh;            /* 按轮数索引，长 max_rounds+1 */
    unsigned char *rtouch;
    int *rorder;
    int rn;
    double er;
    double *ehp;
} Res;

/* 按深度分层的临时缓冲（见文件头"必须小心的坑"） */
typedef struct {
    Side *nxt;      /* n 个 */
    double *lost_all;  /* n 个（★ 浮点：掉血是小数） */
    Unit *unitbuf;  /* n * maxunits */
} Scratch;

/* --------------------------------------------------------------- memo 表 */
typedef struct {
    char *key; /* NULL = 空槽 */
    int klen;
    Res *res;
} Slot;
typedef struct {
    Slot *slots;
    size_t cap, used;
} Memo;

static size_t fnv(const char *s, int n) {
    size_t h = 1469598103934665603ull;
    for (int i = 0; i < n; ++i) {
        h ^= (unsigned char)s[i];
        h *= 1099511628211ull;
    }
    return h;
}
static void memo_grow(Memo *m) {
    size_t nc = m->cap ? m->cap * 2 : 1024;
    Slot *ns = (Slot *)xcalloc(nc, sizeof(Slot));
    for (size_t i = 0; i < m->cap; ++i) {
        if (!m->slots[i].key) continue;
        size_t j = fnv(m->slots[i].key, m->slots[i].klen) & (nc - 1);
        while (ns[j].key) j = (j + 1) & (nc - 1);
        ns[j] = m->slots[i];
    }
    free(m->slots);
    m->slots = ns;
    m->cap = nc;
}
static Res *memo_get(Memo *m, const char *key, int klen) {
    if (!m->cap) return NULL;
    size_t j = fnv(key, klen) & (m->cap - 1);
    while (m->slots[j].key) {
        if (m->slots[j].klen == klen && !memcmp(m->slots[j].key, key, klen))
            return m->slots[j].res;
        j = (j + 1) & (m->cap - 1);
    }
    return NULL;
}
static void memo_put(Memo *m, Arena *a, const char *key, int klen, Res *r) {
    if (m->used * 10 >= m->cap * 7) memo_grow(m);
    size_t j = fnv(key, klen) & (m->cap - 1);
    while (m->slots[j].key) j = (j + 1) & (m->cap - 1);
    char *k = (char *)arena_alloc(a, (size_t)klen + 1);
    memcpy(k, key, klen);
    k[klen] = 0;
    m->slots[j].key = k;
    m->slots[j].klen = klen;
    m->slots[j].res = r;
    m->used++;
}

/* ------------------------------------------------------------- 一趟 DP 的上下文 */
typedef struct {
    int n, nmask, max_rounds, max_states, maxunits;
    long long n_exp;
    int **enemy;
    int *enemy_n;
    PyObject *agg_fn, *kinds;
    Arena arena;
    Memo memo;
    Scratch *scratch; /* [max_rounds + 2] */
    Res *trunc;       /* 截断分支的返回（Python 那版**不记忆**它） */
} Ctx;

static int encode_state(const Side *st, int n, char *out) {
    int k = 0;
    for (int i = 0; i < n; ++i) {
        out[k++] = (char)st[i].n;
        for (int u = 0; u < st[i].n; ++u) {
            int v;
            out[k++] = (char)st[i].units[u].kind;
            /* ★★ `hp` 现在是 **double** ⇒ 键里存它的**位型**（8 字节）。
             *   存整数会丢小数部分 ⇒ 两个不同的状态会撞成同一个键 ⇒ **静默算错**。 */
            {
                double h = st[i].units[u].hp;
                memcpy(out + k, &h, sizeof(double));
                k += (int)sizeof(double);
            }
            out[k++] = (char)st[i].units[u].retreat;
            v = st[i].units[u].soak;
            out[k++] = (char)(v & 0xFF);
            out[k++] = (char)((v >> 8) & 0xFF);
        }
    }
    return k;
}

static int is_absorbed(Ctx *c, const Side *st) {
    for (int i = 0; i < c->n; ++i) {
        if (!st[i].n) continue;
        for (int e = 0; e < c->enemy_n[i]; ++e)
            if (st[c->enemy[i][e]].n) return 0;
    }
    return 1;
}
static int mask_of(const Side *st, int n) {
    int m = 0;
    for (int i = 0; i < n; ++i)
        if (st[i].n) m |= (1 << i);
    return m;
}

static Res *new_res(Ctx *c) {
    Res *r = (Res *)arena_alloc(&c->arena, sizeof(Res));
    memset(r, 0, sizeof(*r));
    r->dist = (double *)arena_alloc(&c->arena, sizeof(double) * (size_t)c->nmask);
    r->dtouch = (unsigned char *)arena_alloc(&c->arena, (size_t)c->nmask);
    r->dorder = (int *)arena_alloc(&c->arena, sizeof(int) * (size_t)c->nmask);
    memset(r->dist, 0, sizeof(double) * (size_t)c->nmask);
    memset(r->dtouch, 0, (size_t)c->nmask);
    r->rh = (double *)arena_alloc(&c->arena, sizeof(double) * (size_t)(c->max_rounds + 1));
    r->rtouch = (unsigned char *)arena_alloc(&c->arena, (size_t)(c->max_rounds + 1));
    r->rorder = (int *)arena_alloc(&c->arena, sizeof(int) * (size_t)(c->max_rounds + 1));
    memset(r->rh, 0, sizeof(double) * (size_t)(c->max_rounds + 1));
    memset(r->rtouch, 0, (size_t)(c->max_rounds + 1));
    r->ehp = (double *)arena_alloc(&c->arena, sizeof(double) * (size_t)c->n);
    memset(r->ehp, 0, sizeof(double) * (size_t)c->n);
    return r;
}

/* 复刻 `dist[k] = dist.get(k, 0.0) + v`：**键在第一次被碰时就进 dict**，
 * 与这次贡献是否为 0 无关 ⇒ 触达即登记。 */
static inline void d_add(Res *r, int k, double v) {
    if (!r->dtouch[k]) {
        r->dtouch[k] = 1;
        r->dorder[r->dn++] = k;
    }
    r->dist[k] += v;
}
static inline void r_add(Res *r, int k, double v) {
    if (!r->rtouch[k]) {
        r->rtouch[k] = 1;
        r->rorder[r->rn++] = k;
    }
    r->rh[k] += v;
}

static PyObject *build_sig(Ctx *c, const Side *st) {
    PyObject *outer = PyTuple_New(c->n);
    if (!outer) return NULL;
    for (int i = 0; i < c->n; ++i) {
        PyObject *inner = PyTuple_New(st[i].n);
        if (!inner) {
            Py_DECREF(outer);
            return NULL;
        }
        for (int u = 0; u < st[i].n; ++u) {
            PyObject *kind = PyList_GetItem(c->kinds, st[i].units[u].kind);
            if (!kind) {
                Py_DECREF(inner);
                Py_DECREF(outer);
                return NULL;
            }
            Py_INCREF(kind);
            PyObject *ret = PyBool_FromLong(st[i].units[u].retreat);
            if (!ret) {
                Py_DECREF(kind);
                Py_DECREF(inner);
                Py_DECREF(outer);
                return NULL;
            }
            PyObject *pair = PyTuple_New(2);
            if (!pair) {
                Py_DECREF(kind);
                Py_DECREF(ret);
                Py_DECREF(inner);
                Py_DECREF(outer);
                return NULL;
            }
            PyTuple_SET_ITEM(pair, 0, kind);
            PyTuple_SET_ITEM(pair, 1, ret);
            PyTuple_SET_ITEM(inner, u, pair);
        }
        PyTuple_SET_ITEM(outer, i, inner);
    }
    return outer;
}

/* ================================================== `_agg_damage` 的 6^方数 枚举 */
/* Python `_agg_damage` 循环体的搬运（约束①：伤害**基数**由 Python 传进来）。
 *
 * ★★ 逐位相同的四个条件，缺一个就会静默改掉观测特征：
 *   ① **组合序** = `itertools.product(DIE_FACES, repeat=n)`：**最后一方变化最快**
 *      （product 的最右索引先走），`p_die` 的累加次序由它决定。
 *   ② **`p_die = 1.0 / nf**n`**：`nf**n` 在 Python 那边是**整数**（6^8=1679616，
 *      远小于 2^53 ⇒ 转 double 精确），除法两边都是"正确舍入" ⇒ 同一位型。
 *   ③ **两次独立舍入**：`share = power/ne` 一次、`share*(100-soak)/100` 两次
 *      （先乘后除，Python 的 `*` `/` 同优先左结合）。⇒ 编译必须
 *      `-ffp-contract=off`（已在 build 脚本里），否则 aarch64 把两次并成一次。
 *      `round()` = **就近、ties-to-even**（银行家舍入）⇒ 这里手写 `py_round`
 *      而不是用 `rint`：`rint` 跟**当前舍入模式**走，而 Python 的 `round` 不跟。
 *   ④ **伤害向量按首次触达序**返回（= Python dict 的插入序），`rec` 那边的
 *      `PyDict_Next` 才和纯 Python 的 `for dv, p in agg.items()` 同序。
 *
 * ★ 与 Python 版**唯一**的结构差别：把只依赖 (方, 骰面, 目标减伤) 的
 *   `max(1, round(share*(100-soak)/100))` 提到组合循环**外**预计算成 `dmgtab`。
 *   同一个表达式、同一批输入 ⇒ 位型相同；而**累加顺序**（方 i 升序、其敌人在
 *   `enemy_idx[i]` 里的原序）一个字没动。热循环因此是纯整数加法。
 */

/* 「伤害向量 → 概率质量」的临时表：**紧凑 + 保序**（首次触达序），
 * 外加一张开放寻址的哈希用于查重复。只在 miss 时活一会儿，用完即弃 ⇒ malloc。 */
typedef struct {
    int *dmg;      /* [cap*n] 伤害向量，按首次触达序 */
    double *p;     /* [cap]   概率质量 */
    int *hash;     /* [hcap]  存 "下标+1"，0 = 空槽 */
    size_t cap, used, hcap;
    int n;
} AggTab;

static void aggtab_init(AggTab *t, int n) {
    memset(t, 0, sizeof(*t));
    t->n = n;
}
static void aggtab_free(AggTab *t) {
    free(t->dmg);
    free(t->p);
    free(t->hash);
}
static size_t dvec_hash(const int *v, int n) {
    return fnv((const char *)v, n * (int)sizeof(int));
}
static void aggtab_rehash(AggTab *t, size_t nc) {
    int *nh = (int *)xcalloc(nc, sizeof(int));
    for (size_t i = 0; i < t->used; ++i) {
        size_t j = dvec_hash(t->dmg + i * (size_t)t->n, t->n) & (nc - 1);
        while (nh[j]) j = (j + 1) & (nc - 1);
        nh[j] = (int)i + 1;
    }
    free(t->hash);
    t->hash = nh;
    t->hcap = nc;
}
static int aggtab_find(AggTab *t, const int *v) {
    if (!t->hcap) return -1;
    size_t j = dvec_hash(v, t->n) & (t->hcap - 1);
    while (t->hash[j]) {
        int idx = t->hash[j] - 1;
        if (!memcmp(t->dmg + (size_t)idx * (size_t)t->n, v, sizeof(int) * (size_t)t->n))
            return idx;
        j = (j + 1) & (t->hcap - 1);
    }
    return -1;
}
/* 复刻 `agg[dv] = agg.get(dv, 0.0) + p`：键在**第一次被碰**时进表（顺序即插入序）。 */
static void aggtab_add(AggTab *t, const int *v, double p) {
    int idx = aggtab_find(t, v);
    if (idx < 0) {
        if (t->used == t->cap) {
            size_t nc = t->cap ? t->cap * 2 : 32;
            t->dmg = (int *)realloc(t->dmg, nc * sizeof(int) * (size_t)t->n);
            t->p = (double *)realloc(t->p, nc * sizeof(double));
            if (!t->dmg || !t->p) Py_FatalError("_combat_fast: 内存不足");
            t->cap = nc;
        }
        idx = (int)t->used++;
        memcpy(t->dmg + (size_t)idx * (size_t)t->n, v, sizeof(int) * (size_t)t->n);
        t->p[idx] = 0.0;
        if (t->used * 10 >= t->hcap * 7)
            aggtab_rehash(t, t->hcap ? t->hcap * 2 : 64);
        size_t j = dvec_hash(v, t->n) & (t->hcap - 1);
        while (t->hash[j]) j = (j + 1) & (t->hcap - 1);
        t->hash[j] = idx + 1;
    }
    t->p[idx] += p;
}

/* Python `round(float)`：就近、**ties-to-even**。见上面条件③。 */
static long py_round(double x) {
    long i = (long)x; /* 向零截断 */
    double frac = x - (double)i;
    double a = frac < 0 ? -frac : frac;
    int sgn = x < 0 ? -1 : 1;
    if (a > 0.5) return i + sgn;
    if (a < 0.5) return i;
    return (i % 2) ? i + sgn : i; /* 正中间 ⇒ 取偶 */
}

/* 已经解析成 C 数组的一局骰子展开。见 `fast_agg_damage` 的入口校验。 */
static PyObject *agg_core(int n, const int *faces, int nf, const int *side_n,
                          const int *en_off, const int *en_n, const int *en_flat,
                          const long *soak, const long *power) {
    AggTab tab;
    aggtab_init(&tab, n);

    /* 只有"活着 **且**还有活敌人"的方才出手（`if not live[i]/if not en: continue`）*/
    int active[8], a_enoff[8], a_ne[8], a_base[8];
    int nact = 0, base = 0;
    for (int i = 0; i < n; ++i) {
        if (!side_n[i]) continue;
        if (!en_n[i]) continue;
        active[nact] = i;
        a_enoff[nact] = en_off[i];
        a_ne[nact] = en_n[i];
        a_base[nact] = base;
        base += nf * en_n[i];
        ++nact;
    }

    /* `dmgtab[ a_base[i] + f*ne + k ]` = 方 i 掷出 faces[f] 时，打它的第 k 个敌人**独自**
     * 吃到的伤害（还没跟别的方相加）。★ 逐条照抄 `share` 那三行的舍入次数。 */
    int *dmgtab = (int *)xmalloc(sizeof(int) * (size_t)(base > 0 ? base : 1));
    for (int a = 0; a < nact; ++a) {
        int i = active[a], ne = a_ne[a];
        const int *tg = en_flat + a_enoff[a];
        for (int f = 0; f < nf; ++f) {
            /* ★ `combo[i]` 是**骰面的值**，Python 用 `power[combo[i]-1]` ⇒ 下标是
             *   `faces[f]-1`，**不是** `f`（两者只在 faces=(1..6) 时重合，别赌）。 */
            double share = (double)power[(size_t)i * nf + (faces[f] - 1)] / (double)ne;
            int *row = dmgtab + a_base[a] + (size_t)f * ne;
            for (int k = 0; k < ne; ++k) {
                double v = share * (double)(100 - soak[tg[k]]) / 100.0;
                long r = py_round(v);
                row[k] = r > 1 ? (int)r : 1; /* `max(1, round(...))` */
            }
        }
    }

    long long total = 1;
    for (int i = 0; i < n; ++i) total *= nf;
    double p_die = 1.0 / (double)total;

    int digits[8], dmg[8];
    for (int i = 0; i < n; ++i) digits[i] = 0;
    for (long long c = 0; c < total; ++c) {
        for (int j = 0; j < n; ++j) dmg[j] = 0;
        for (int a = 0; a < nact; ++a) {
            const int *row = dmgtab + a_base[a] + (size_t)digits[active[a]] * (size_t)a_ne[a];
            const int *tg = en_flat + a_enoff[a];
            for (int k = 0; k < a_ne[a]; ++k) dmg[tg[k]] += row[k];
        }
        aggtab_add(&tab, dmg, p_die);
        for (int d = n - 1; d >= 0; --d) { /* 最后一方最快（product 同序） */
            if (++digits[d] < nf) break;
            digits[d] = 0;
        }
    }
    free(dmgtab);

    PyObject *out = PyDict_New();
    if (!out) {
        aggtab_free(&tab);
        return NULL;
    }
    for (size_t e = 0; e < tab.used; ++e) {
        PyObject *key = PyTuple_New(n);
        if (!key) break;
        for (int j = 0; j < n; ++j) {
            PyObject *x = PyLong_FromLong(tab.dmg[e * (size_t)n + j]);
            if (!x) {
                Py_DECREF(key);
                key = NULL;
                break;
            }
            PyTuple_SET_ITEM(key, j, x);
        }
        PyObject *val = key ? PyFloat_FromDouble(tab.p[e]) : NULL;
        int rc = (key && val) ? PyDict_SetItem(out, key, val) : -1;
        Py_XDECREF(key);
        Py_XDECREF(val);
        if (rc) {
            Py_DECREF(out);
            out = NULL;
            break;
        }
    }
    aggtab_free(&tab);
    return out;
}

/* `agg_damage(sig, enemies, soaks, powers, faces) -> {伤害向量: 概率质量}`
 *  —— Python `_agg_damage` 的 try-C-first 分支。`sig` 只用它的**每方长度**
 *  （= 这一方还活着没有），伤害基数一律走 `powers`（约束①）。 */
static int get_long(PyObject *o, long *out) {
    long v = PyLong_AsLong(o);
    if (v == -1 && PyErr_Occurred()) return -1;
    *out = v;
    return 0;
}

static PyObject *fast_agg_damage(PyObject *self, PyObject *args) {
    PyObject *sig, *enemies, *soaks, *powers, *faces;
    if (!PyArg_ParseTuple(args, "OOOOO", &sig, &enemies, &soaks, &powers, &faces))
        return NULL;
    PyObject *f[5] = {NULL, NULL, NULL, NULL, NULL};
    PyObject *out = NULL;
    int n = 0, nf = 0;
    int *side_n = NULL, *all_off = NULL, *all_n = NULL, *all_en = NULL;
    int *en_off = NULL, *en_n = NULL, *en_flat = NULL;
    long *soak = NULL, *power = NULL;
    int faceval[64];

    const char *what[5] = {"sig", "enemies", "soaks", "powers", "faces"};
    PyObject *raw[5] = {sig, enemies, soaks, powers, faces};
    for (int i = 0; i < 5; ++i) {
        f[i] = PySequence_Fast(raw[i], "agg_damage: 参数必须是序列");
        if (!f[i]) goto done;
    }
    if (PySequence_Fast_GET_SIZE(f[0]) < 1 || PySequence_Fast_GET_SIZE(f[0]) > 8) {
        PyErr_SetString(PyExc_ValueError, "agg_damage: 方数必须在 1..8");
        goto done;
    }
    n = (int)PySequence_Fast_GET_SIZE(f[0]);
    nf = (int)PySequence_Fast_GET_SIZE(f[4]);
    for (int i = 1; i < 4; ++i) {
        if ((int)PySequence_Fast_GET_SIZE(f[i]) != n) {
            PyErr_Format(PyExc_ValueError, "agg_damage: %s 的长度必须等于方数 %d", what[i], n);
            goto done;
        }
    }
    if (nf < 1 || nf > 64) {
        PyErr_SetString(PyExc_ValueError, "agg_damage: 骰面数必须在 1..64");
        goto done;
    }
    /* faces 的值就是 `combo[i]-1` 那个下标 ⇒ 必须是 1..nf 的排列，否则 Python
     * 那边会 IndexError、而 C 这边会**越界读**（宁可在这里报出来）。 */
    for (int k = 0; k < nf; ++k) {
        long v;
        if (get_long(PySequence_Fast_GET_ITEM(f[4], k), &v)) goto done;
        if (v < 1 || v > nf) {
            PyErr_SetString(PyExc_ValueError,
                            "agg_damage: faces 必须是 1..nf 的排列（伤害基数按它取下标）");
            goto done;
        }
        faceval[k] = (int)v;
    }

    side_n = (int *)xmalloc(sizeof(int) * (size_t)n);
    all_off = (int *)xmalloc(sizeof(int) * (size_t)n);
    all_n = (int *)xmalloc(sizeof(int) * (size_t)n);
    en_off = (int *)xmalloc(sizeof(int) * (size_t)n);
    en_n = (int *)xmalloc(sizeof(int) * (size_t)n);
    soak = (long *)xmalloc(sizeof(long) * (size_t)n);
    power = (long *)xmalloc(sizeof(long) * (size_t)n * (size_t)nf);

    /* 第一趟：逐方读长度 / 减伤 / 6 个基数 / 原始敌人清单 */
    int etotal = 0;
    for (int i = 0; i < n; ++i) {
        Py_ssize_t L = PySequence_Size(PySequence_Fast_GET_ITEM(f[0], i));
        if (L < 0) goto done;
        side_n[i] = (int)L;

        if (get_long(PySequence_Fast_GET_ITEM(f[2], i), &soak[i])) goto done;

        PyObject *pw = PySequence_Fast(PySequence_Fast_GET_ITEM(f[3], i),
                                       "agg_damage: powers 每项必须是序列");
        if (!pw) goto done;
        int ok = ((int)PySequence_Fast_GET_SIZE(pw) == nf);
        for (int k = 0; ok && k < nf; ++k)
            ok = get_long(PySequence_Fast_GET_ITEM(pw, k), &power[(size_t)i * nf + k]) == 0;
        Py_DECREF(pw);
        if (!ok) {
            PyErr_Format(PyExc_ValueError, "agg_damage: powers[%d] 必须是 %d 个整数", i, nf);
            goto done;
        }

        PyObject *er = PySequence_Fast(PySequence_Fast_GET_ITEM(f[1], i),
                                       "agg_damage: enemies 每项必须是序列");
        if (!er) goto done;
        Py_ssize_t m = PySequence_Fast_GET_SIZE(er);
        all_off[i] = etotal;
        all_n[i] = (int)m;
        etotal += (int)m;
        all_en = (int *)realloc(all_en, sizeof(int) * (size_t)(etotal > 0 ? etotal : 1));
        if (!all_en) Py_FatalError("_combat_fast: 内存不足");
        for (Py_ssize_t k = 0; k < m; ++k) {
            long v;
            if (get_long(PySequence_Fast_GET_ITEM(er, k), &v) || v < 0 || v >= n) {
                Py_DECREF(er);
                if (!PyErr_Occurred())
                    PyErr_Format(PyExc_ValueError, "agg_damage: enemies[%d] 下标越界", i);
                goto done;
            }
            all_en[all_off[i] + (int)k] = (int)v;
        }
        Py_DECREF(er);
    }

    /* 第二趟：按 live 过滤（`en = [j for j in b.enemy_idx[i] if live[j]]`，**原序**） */
    en_flat = (int *)xmalloc(sizeof(int) * (size_t)(etotal > 0 ? etotal : 1));
    int ep = 0;
    for (int i = 0; i < n; ++i) {
        en_off[i] = ep;
        if (!side_n[i]) {
            en_n[i] = 0;
            continue;
        }
        for (int k = 0; k < all_n[i]; ++k) {
            int j = all_en[all_off[i] + k];
            if (side_n[j]) en_flat[ep++] = j;
        }
        en_n[i] = ep - en_off[i];
    }

    out = agg_core(n, faceval, nf, side_n, en_off, en_n, en_flat, soak, power);

done:
    for (int i = 0; i < 5; ++i) Py_XDECREF(f[i]);
    free(side_n);
    free(all_off);
    free(all_n);
    free(all_en);
    free(en_off);
    free(en_n);
    free(en_flat);
    free(soak);
    free(power);
    return out;
}

static Res *rec(Ctx *c, const Side *st, int depth) {
    /* ★ 每支军 12 字节（kind 1 + hp 8 + retreat 1 + soak 2）⇒ 8 方 × 8 支 = 768 */
    char key[8 * 8 * 12 + 8 + 2];
    int klen = encode_state(st, c->n, key);
    key[klen++] = (char)(depth & 0xFF);
    key[klen++] = (char)((depth >> 8) & 0xFF);
    Res *hit = memo_get(&c->memo, key, klen);
    if (hit) return hit;

    if (is_absorbed(c, st)) { /* ★ 吸收态**也进 memo**（与改后的 Python 版一致） */
        Res *r = new_res(c);
        d_add(r, mask_of(st, c->n), 1.0);
        r_add(r, 0, 1.0);
        memo_put(&c->memo, &c->arena, key, klen, r);
        return r;
    }
    if (depth >= c->max_rounds || c->n_exp >= c->max_states) return c->trunc;
    c->n_exp++;

    Res *r = new_res(c);
    Scratch *sc = &c->scratch[depth]; /* ★ 按深度分层，子层不会踩父层 */

    /* ★ 旧 C 这里算过一个 `flat[i]`（"这一方没人带撤退减伤"），
     *   用来在**整数**摊分和"带减伤的整数摊分"之间二选一。
     *   现在摊分是**统一的浮点注水**（`engine_spread`，减伤是逐人比例）⇒
     *   `flat` 这个概念**没了**（少一条分支，也少一个能写错的地方）。 */

    PyObject *sig = build_sig(c, st);
    if (!sig) return NULL;
    PyObject *agg = PyObject_CallOneArg(c->agg_fn, sig);
    Py_DECREF(sig);
    if (!agg) return NULL;
    if (!PyDict_Check(agg)) {
        Py_DECREF(agg);
        PyErr_SetString(PyExc_TypeError, "_combat_fast: agg 回调必须返回 dict");
        return NULL;
    }

    Py_ssize_t pos = 0;
    PyObject *dkey, *dval;
    /* ★ `PyDict_Next` 就是**插入序** —— 与 Python 侧 `agg.items()` 一致 */
    while (PyDict_Next(agg, &pos, &dkey, &dval)) {
        double p = PyFloat_AsDouble(dval);
        if (p == -1.0 && PyErr_Occurred()) {
            Py_DECREF(agg);
            return NULL;
        }
        int ub = 0;
        for (int i = 0; i < c->n; ++i) {
            const Side *units = &st[i];
            PyObject *dobj = PyTuple_GetItem(dkey, i);
            if (!dobj) {
                Py_DECREF(agg);
                return NULL;
            }
            long d = PyLong_AsLong(dobj);
            if (d == -1 && PyErr_Occurred()) {
                Py_DECREF(agg);
                return NULL;
            }
            if (units->n == 0 || d == 0) {
                sc->nxt[i].units = units->units;
                sc->nxt[i].n = units->n;
                sc->lost_all[i] = 0;
                continue;
            }
            int cnt = units->n;
            double lost = 0.0;
            int kn = 0;
            Unit *keep = sc->unitbuf + (size_t)i * (size_t)c->maxunits;
            double hp[MAX_UNITS];
            /* ★★ 摊分：**引擎的浮点注水**（`engine_spread`）——
             *   旧 C 用的是**整数** divmod（余数给前排）⇒ 多方局与引擎**差 1 点血**
             *   ⇒ C 路径以前只能限死在"每方一支军"。现在不限了。 */
            engine_spread(d, units->units, cnt, hp);
            for (int k = 0; k < cnt; ++k) {
                lost += units->units[k].hp - hp[k];    /* ★ 次序同 Python（k 升序） */
                if (hp[k] > 0) {
                    keep[kn] = units->units[k];
                    keep[kn++].hp = hp[k];
                }
            }
            sc->nxt[i].units = keep;
            sc->nxt[i].n = kn;
            sc->lost_all[i] = lost;
            (void)ub;
        }

        Res *sub = rec(c, sc->nxt, depth + 1);
        if (!sub) {
            Py_DECREF(agg);
            return NULL;
        }
        for (int k = 0; k < sub->dn; ++k) {
            int idx = sub->dorder[k];
            d_add(r, idx, sub->dist[idx] * p);
        }
        for (int k = 0; k < sub->rn; ++k) {
            int idx = sub->rorder[k];
            /* 复刻 `rh[k+1] = rh.get(k+1, 0.0) + v*p`：越界的键 Python 也**会写进
             * dict**，只是最后 `if k <= max_rounds` 时被丢掉 ⇒ 这里直接丢，等价 */
            if (idx + 1 <= c->max_rounds) r_add(r, idx + 1, sub->rh[idx] * p);
        }
        r->er += p * (1.0 + sub->er);
        for (int i = 0; i < c->n; ++i)
            r->ehp[i] += p * (sc->lost_all[i] + sub->ehp[i]);
    }
    Py_DECREF(agg);
    memo_put(&c->memo, &c->arena, key, klen, r);
    return r;
}

/* ============================================ 摊分：引擎的**浮点注水**（逐位同构）
 *
 * 引擎口径（`mods/native/army/domain_objects.py`）：
 *     `Military.spread_damage(damage, units)`：
 *         ratios = [(cover if cover is not None else 100)/100 for u in units]
 *         caps   = [inf if ratio == 0 else u.hp/ratio for u, ratio in ...]
 *         shares = Military.equal_allocation(damage, caps)
 *         u.hp  -= share*ratio
 *     `Military.equal_allocation(amount, limits)`：**先平分**，谁份额超过其上限
 *     （= hp/ratio）就只吃那么多，**没打出去的那份重分给还活着的人**。
 *
 * ★ 与 RL 的 Python `_apply` / `rec` 热路径**同一套算法**（那边是参考实现）——
 *   **两边必须一起改**（曾只改一边，就是那个坑）。
 * ★ 逐位相同的关键（浮点求和次序决定位型）：
 *   ① Python 的 `active` 是 **`set`**（小整数 ⇒ 迭代序 = **升序**）
 *      ⇒ C 用**位掩码升序扫**（`1u << k` 由低到高）。
 *   ② `spent` 按**升序**累加、`remaining = max(0.0, remaining - spent)`
 *      ⇒ 次序不同就是 1 ulp 的差别。
 *   ③ `caps`/`ratios` 都是 `double`；`ratio == 0` ⇒ 上限 `inf`（吃多少都不死）。
 *   ④ 最后 `hp = hp_before - share*ratio`（**不夹 0**：与 Python 一致；
 *      上限已经把份额卡在 hp/ratio ⇒ 正常情况下不会为负）。
 */
static void engine_spread(long damage, const Unit *units, int cnt, double *hp) {
    double ratios[MAX_UNITS], caps[MAX_UNITS], allocated[MAX_UNITS];
    unsigned int active = 0;
    for (int k = 0; k < cnt; ++k) {
        double r = (double)units[k].soak / 100.0;      /* cover/100 */
        double cap;
        ratios[k] = r;
        cap = (r == 0.0) ? INFINITY : units[k].hp / r;
        caps[k] = cap;
        allocated[k] = 0.0;
        hp[k] = units[k].hp;
        if (cap > 0.0) active |= (1u << k);             /* `limit > 0` */
    }
    double remaining = (damage > 0) ? (double)damage : 0.0;   /* max(0.0, amount) */
    while (active && remaining > 1e-12) {
        int nact = 0;
        for (int k = 0; k < cnt; ++k) if (active & (1u << k)) ++nact;
        double share = remaining / (double)nact;
        unsigned int capped = 0;
        for (int k = 0; k < cnt; ++k)
            if ((active & (1u << k)) && (caps[k] - allocated[k] <= share + 1e-12))
                capped |= (1u << k);
        if (!capped) {                                   /* 没人到顶 ⇒ 分完收工 */
            for (int k = 0; k < cnt; ++k) if (active & (1u << k)) allocated[k] += share;
            break;
        }
        double spent = 0.0;
        for (int k = 0; k < cnt; ++k) {                 /* ★ 升序 = Python set 的迭代序 */
            double rest, amt;
            if (!(capped & (1u << k))) continue;
            rest = caps[k] - allocated[k];
            if (rest < 0.0) rest = 0.0;                 /* max(0.0, …) */
            amt = (share < rest) ? share : rest;        /* min(share, …) */
            allocated[k] += amt;
            spent += amt;
            active &= ~(1u << k);                       /* 到顶 ⇒ 退出这一轮 */
        }
        remaining -= spent;
        if (remaining < 0.0) remaining = 0.0;           /* max(0.0, …) */
    }
    for (int k = 0; k < cnt; ++k) hp[k] = units[k].hp - allocated[k] * ratios[k];
}

/* ------------------------------------------------------------------ 入口 */
static void ctx_free(Ctx *c) {
    if (c->enemy) {
        for (int i = 0; i < c->n; ++i) free(c->enemy[i]);
        free(c->enemy);
    }
    free(c->enemy_n);
    if (c->scratch) {
        for (int d = 0; d < c->max_rounds + 2; ++d) {
            free(c->scratch[d].nxt);
            free(c->scratch[d].lost_all);
            free(c->scratch[d].unitbuf);
        }
        free(c->scratch);
    }
    free(c->memo.slots);
    arena_free(&c->arena);
}

static PyObject *fast_assess(PyObject *self, PyObject *args) {
    int n, max_rounds, max_states;
    PyObject *enemies, *init_units, *kinds, *agg_fn;
    if (!PyArg_ParseTuple(args, "iOOOOii", &n, &enemies, &init_units, &kinds,
                          &agg_fn, &max_rounds, &max_states))
        return NULL;
    if (n < 1 || n > 8) {
        PyErr_SetString(PyExc_ValueError, "_combat_fast: n 必须在 1..8");
        return NULL;
    }
    if (!PyCallable_Check(agg_fn)) {
        PyErr_SetString(PyExc_TypeError, "_combat_fast: agg_fn 必须可调用");
        return NULL;
    }

    Ctx c;
    memset(&c, 0, sizeof(c));
    c.n = n;
    c.nmask = 1 << n;
    c.max_rounds = max_rounds;
    c.max_states = max_states;
    c.agg_fn = agg_fn;
    c.kinds = kinds;
    c.enemy = (int **)xcalloc((size_t)n, sizeof(int *));
    c.enemy_n = (int *)xcalloc((size_t)n, sizeof(int));

    for (int i = 0; i < n; ++i) {
        PyObject *row = PySequence_GetItem(enemies, i);
        if (!row) goto fail;
        Py_ssize_t m = PySequence_Size(row);
        c.enemy[i] = (int *)xcalloc((size_t)(m ? m : 1), sizeof(int));
        c.enemy_n[i] = (int)m;
        for (Py_ssize_t j = 0; j < m; ++j) {
            PyObject *x = PySequence_GetItem(row, j);
            if (!x) { Py_DECREF(row); goto fail; }
            c.enemy[i][j] = (int)PyLong_AsLong(x);
            Py_DECREF(x);
        }
        Py_DECREF(row);
    }

    Side *init = (Side *)xcalloc((size_t)n, sizeof(Side));
    c.maxunits = 1;
    for (int i = 0; i < n; ++i) {
        PyObject *row = PySequence_GetItem(init_units, i);
        if (!row) { free(init); goto fail; }
        Py_ssize_t m = PySequence_Size(row);
        if ((int)m > c.maxunits) c.maxunits = (int)m;
        /* ★ 摊分用的定长缓冲是 `MAX_UNITS` ⇒ 超了**响亮报错**，
         *   绝不让它**越界写**（那是内存损坏，不是"慢一点"）。 */
        if (m > MAX_UNITS) {
            Py_DECREF(row);
            free(init);
            PyErr_Format(PyExc_ValueError,
                         "_combat_fast: 每方军队数超过 %d（这一支不支持）", MAX_UNITS);
            goto fail;
        }
        init[i].n = (int)m;
        init[i].units = (Unit *)xcalloc((size_t)(m ? m : 1), sizeof(Unit));
        for (Py_ssize_t j = 0; j < m; ++j) {
            PyObject *t = PySequence_GetItem(row, j);
            if (!t) { Py_DECREF(row); free(init); goto fail; }
            init[i].units[j].kind = (int)PyLong_AsLong(PyTuple_GetItem(t, 0));
            init[i].units[j].hp = PyFloat_AsDouble(PyTuple_GetItem(t, 1));
            if (PyErr_Occurred()) { Py_DECREF(t); Py_DECREF(row); free(init); goto fail; }
            init[i].units[j].retreat = PyObject_IsTrue(PyTuple_GetItem(t, 2));
            init[i].units[j].soak = (int)PyLong_AsLong(PyTuple_GetItem(t, 3));
            Py_DECREF(t);
        }
        Py_DECREF(row);
    }

    c.scratch = (Scratch *)xcalloc((size_t)(max_rounds + 2), sizeof(Scratch));
    for (int d = 0; d < max_rounds + 2; ++d) {
        c.scratch[d].nxt = (Side *)xcalloc((size_t)n, sizeof(Side));
        c.scratch[d].lost_all = (double *)xcalloc((size_t)n, sizeof(double));
        c.scratch[d].unitbuf =
            (Unit *)xcalloc((size_t)n * (size_t)c.maxunits, sizeof(Unit));
    }
    c.trunc = new_res(&c); /* 全零，且**不进 memo**（Python 同） */

    Res *root = rec(&c, init, 0);
    if (!root) { free(init); goto fail; }

    PyObject *dlist = PyList_New(root->dn);
    PyObject *rlist = PyList_New(root->rn);
    PyObject *ehp = PyList_New(n);
    PyObject *out = PyTuple_New(5);
    if (!dlist || !rlist || !ehp || !out) {
        Py_XDECREF(dlist); Py_XDECREF(rlist); Py_XDECREF(ehp); Py_XDECREF(out);
        free(init);
        goto fail;
    }
    for (int k = 0; k < root->dn; ++k) {
        int idx = root->dorder[k];
        PyObject *t = Py_BuildValue("(id)", idx, root->dist[idx]);
        if (!t) { Py_DECREF(dlist); Py_DECREF(rlist); Py_DECREF(ehp); Py_DECREF(out);
                  free(init); goto fail; }
        PyList_SET_ITEM(dlist, k, t);
    }
    for (int k = 0; k < root->rn; ++k) {
        int idx = root->rorder[k];
        PyObject *t = Py_BuildValue("(id)", idx, root->rh[idx]);
        if (!t) { Py_DECREF(dlist); Py_DECREF(rlist); Py_DECREF(ehp); Py_DECREF(out);
                  free(init); goto fail; }
        PyList_SET_ITEM(rlist, k, t);
    }
    for (int i = 0; i < n; ++i)
        PyList_SET_ITEM(ehp, i, PyFloat_FromDouble(root->ehp[i]));

    PyTuple_SET_ITEM(out, 0, dlist);
    PyTuple_SET_ITEM(out, 1, rlist);
    PyTuple_SET_ITEM(out, 2, PyFloat_FromDouble(root->er));
    PyTuple_SET_ITEM(out, 3, ehp);
    PyTuple_SET_ITEM(out, 4, PyLong_FromLongLong(c.n_exp));

    free(init);
    ctx_free(&c);
    return out;

fail:
    ctx_free(&c);
    return NULL;
}

static PyMethodDef Methods[] = {
    {"assess", fast_assess, METH_VARARGS,
     "assess(n, enemies, init_units, kinds, agg_fn, max_rounds, max_states) -> "
     "(dist_list, rh_list, er, ehp, n_exp)。dist_list/rh_list 是**保序**的"
     "(下标, 值) 列表，用来复刻 Python 侧的浮点求和顺序（逐位相同）。"},
    {"agg_damage", fast_agg_damage, METH_VARARGS,
     "agg_damage(sig, enemies, soaks, powers, faces) -> {伤害向量: 概率质量}。"
     "`_agg_damage` 里 6^方数 枚举的 C 版，**逐位相同**"
     "（`tests/test_agg_damage_fast.py` 钉死），见该函数头部的四条条件。"},
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT, "_combat_fast",
    "战斗 DP 的可选加速扩展（见 rl/combat_probs.py 顶部的回落逻辑）", -1,
    Methods, NULL, NULL, NULL, NULL};

PyMODINIT_FUNC PyInit__combat_fast(void) { return PyModule_Create(&moduledef); }
