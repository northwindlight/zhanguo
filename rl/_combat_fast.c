/* `rl/combat_probs.py` 里战斗 DP 的 **可选加速扩展**（CPython C API）。
 *
 * 编译（可选，编不出来就回落纯 Python，仓库仍能用）：
 *     bash rl/build_combat_fast.sh
 *
 * ------------------------------------------------------------------ 为什么是它
 * 实测（`rl/dp_internals_probe.py`）：战斗 DP 占采集回路墙钟 **58.8%**，
 * 而它几乎全是 CPython 的解释器开销 —— 一次转移 **4.5 µs**、约 20 个解释器级
 * 操作（225 ns/操作），cProfile 里**一个数值计算函数都没有**。
 * 换语言能拿到 1~2 个数量级：原型实测 **DP ×12**（对原始 Python ×20）
 * ⇒ 整体推算 **2.27×**，而 Amdahl 上限是 2.43×（DP 占 58.8%）⇒ 吃掉 93.5%。
 *
 * --------------------------------------------------------- 三条不可动摇的约束
 * ① **伤害公式只有一份。** `agg`（签名 → {伤害向量: 概率}，那套 6^方数 掷骰枚举）
 *    **回调进 Python** 拿，不在这里重写。它是照抄引擎的（`World._resolve_battles`），
 *    重写一遍就等于第二次重写 —— 仓库里 v10 的教训。
 * ② **逐位相同。** 这是**观测特征**的来源，漂一点点会让训练静默学错。
 *    所以这里**复刻 Python 的浮点求和顺序**：
 *      · `agg` 按 Python dict 的**插入序**迭代（`PyDict_Next` 就是插入序）；
 *      · `dist` / `rh` 各自记**首次触达序**（`dorder` / `rorder`），
 *        因为 `sum(dist.values())`、`for s, p in dist.items()` 都按这个序走。
 *    ★ 顺带的好处：稀疏有序**正好比稠密数组快** —— 实测稠密数组在 Python 里
 *      慢 1.4~1.5×（见 PLAN §12.20 第六节），C 这边同一个道理。
 * ③ **可选。** `combat_probs` 里 `import` 失败就整条回落纯 Python，行为不变。
 *
 * --------------------------------------------------------- 一处必须小心的坑
 * 递归时**父层的临时缓冲会被子层覆盖**。Python 那版每个结果都新建 `nxt` 列表，
 * 这里为了不每次 malloc 而复用缓冲 ⇒ 必须**按递归深度分层**
 * （`scratch[depth]`）。深度上限就是 `max_rounds`。
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
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
typedef struct {
    int kind, hp, retreat, soak;
} Unit;
typedef struct {
    Unit *units;
    int n;
} Side;

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
    int *lost_all;  /* n 个 */
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
            v = st[i].units[u].hp;
            out[k++] = (char)(v & 0xFF);
            out[k++] = (char)((v >> 8) & 0xFF);
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

static Res *rec(Ctx *c, const Side *st, int depth) {
    char key[8 * 8 * 7 + 8];
    int klen = encode_state(st, c->n, key);
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

    unsigned char flat[9];
    for (int i = 0; i < c->n; ++i) {
        int f = 1;
        for (int u = 0; u < st[i].n; ++u)
            if (st[i].units[u].soak != 100) {
                f = 0;
                break;
            }
        flat[i] = (unsigned char)f;
    }

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
            long per = d / cnt, rem = d % cnt;
            int lost = 0, kn = 0;
            Unit *keep = sc->unitbuf + (size_t)i * (size_t)c->maxunits;
            for (int k = 0; k < cnt; ++k) {
                const Unit *u = &units->units[k];
                long sh = per + (k < rem ? 1 : 0);
                long nh = u->hp - (flat[i] ? sh : sh * u->soak / 100);
                lost += (int)(u->hp - nh); /* ★ 含阵亡者"消失的血" */
                if (nh > 0) {
                    keep[kn].kind = u->kind;
                    keep[kn].hp = (int)nh;
                    keep[kn].retreat = u->retreat;
                    keep[kn].soak = u->soak;
                    ++kn;
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
        init[i].n = (int)m;
        init[i].units = (Unit *)xcalloc((size_t)(m ? m : 1), sizeof(Unit));
        for (Py_ssize_t j = 0; j < m; ++j) {
            PyObject *t = PySequence_GetItem(row, j);
            if (!t) { Py_DECREF(row); free(init); goto fail; }
            init[i].units[j].kind = (int)PyLong_AsLong(PyTuple_GetItem(t, 0));
            init[i].units[j].hp = (int)PyLong_AsLong(PyTuple_GetItem(t, 1));
            init[i].units[j].retreat = PyObject_IsTrue(PyTuple_GetItem(t, 2));
            init[i].units[j].soak = (int)PyLong_AsLong(PyTuple_GetItem(t, 3));
            Py_DECREF(t);
        }
        Py_DECREF(row);
    }

    c.scratch = (Scratch *)xcalloc((size_t)(max_rounds + 2), sizeof(Scratch));
    for (int d = 0; d < max_rounds + 2; ++d) {
        c.scratch[d].nxt = (Side *)xcalloc((size_t)n, sizeof(Side));
        c.scratch[d].lost_all = (int *)xcalloc((size_t)n, sizeof(int));
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
    {NULL, NULL, 0, NULL}};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT, "_combat_fast",
    "战斗 DP 的可选加速扩展（见 rl/combat_probs.py 顶部的回落逻辑）", -1,
    Methods, NULL, NULL, NULL, NULL};

PyMODINIT_FUNC PyInit__combat_fast(void) { return PyModule_Create(&moduledef); }
