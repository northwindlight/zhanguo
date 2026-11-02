/* v13 经济求解器的可选 C 快路径。
 *
 * 只复刻 `ruleai/v13/economy.py` 与 `ruleai/v13/market.py` 里的**纯算术**：
 * 运营物料目标、稳态物料平衡、现金储备、市场情景估值。引擎调用（规划／报价／
 * 成交）仍全部由 Python 走正式 API；本文件不读世界、不改世界、不碰随机数。
 *
 * 编译：python -m ruleai.v13.build
 *
 * ★ 逐位一致的两条硬约束（缺一条就会静默改变教师决策）：
 *   1. 浮点求和顺序必须与 Python 一致（Counter 插入序、`net.items()` 序）；
 *   2. **int / float 类型必须原样保留**。CPython 3.12 起 `sum()` 对纯浮点走
 *      Neumaier 补偿求和，对 int 走精确整数累加；`Counter` 也保持 int。
 *      把 int 一律写成 double 会让估值差 1 ulp，进而可能改变选中的项目。
 *      ⇒ `Ordered` 每个槽位记 `is_int`，`ordered_dict` 按类型返回 PyLong/PyFloat；
 *      ⇒ `market_value` 复刻 sum() 的双路径（整数精确 / 浮点 Neumaier）。
 *
 * 编译选项必须是 `-O2 -ffp-contract=off`，不得 `-ffast-math`（FMA 收缩会改末位）。
 * `.so` 不提交；扩展缺失或输入异常时 Python 侧自动回退，行为不变。
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

#define GOLD "native:gold"
#define SUPPLY "native:supply"
#define EQUIPMENT "native:equipment"

/* ------------------------------------------------------------------ 小工具 */
static int is_key(PyObject *key, const char *name) {
    return PyUnicode_Check(key) && PyUnicode_CompareWithASCIIString(key, name) == 0;
}

/* Python 的字符串相等：先比指针，再比内容。 */
static int same_key(PyObject *a, PyObject *b) {
    if (a == b) return 1;
    if (!PyUnicode_Check(a) || !PyUnicode_Check(b)) return 0;
    return PyObject_RichCompareBool(a, b, Py_EQ);
}

static int number_of(PyObject *value, double *out, int *is_int) {
    if (PyFloat_Check(value)) { *out = PyFloat_AS_DOUBLE(value); *is_int = 0; return 0; }
    if (PyLong_Check(value)) {
        *out = PyLong_AsDouble(value);
        *is_int = 1;
        return PyErr_Occurred() ? -1 : 0;
    }
    PyErr_SetString(PyExc_TypeError, "_econ_fast: 需要数值");
    return -1;
}

static int dict_number(PyObject *map, PyObject *key, double *out, int *found, int *is_int) {
    PyObject *value;
    if (!PyDict_Check(map)) {
        PyErr_SetString(PyExc_TypeError, "_econ_fast: 需要 dict");
        return -1;
    }
    value = PyDict_GetItemWithError(map, key);
    if (!value) {
        *found = 0;
        *out = 0.0;
        *is_int = 1;   /* Python 侧 get(..., 0) 的默认值都是 int */
        return PyErr_Occurred() ? -1 : 0;
    }
    *found = 1;
    return number_of(value, out, is_int);
}

static int dict_number_c(PyObject *map, const char *name, double *out, int *found, int *is_int) {
    PyObject *key = PyUnicode_FromString(name);
    int rc;
    if (!key) return -1;
    rc = dict_number(map, key, out, found, is_int);
    Py_DECREF(key);
    return rc;
}

/* --------------------------------------------------------------- 求和复刻 */
/* CPython 3.12+ 的 `sum()` 复刻：先把 int 精确累加；一旦遇到 float 就切到
 * Neumaier 补偿求和（每个后续项都做补偿，包括切换后的第二项起）。C 侧所有
 * 对应 Python `sum(...)` 的地方都必须走这里，否则末位差 1 ulp 就可能改掉
 * 「哪些厂要补燃料」「够不够现金」这类判断。 */
typedef struct {
    int acc_int;
    double i_res, f_res, comp;
} Sum;

static void sum_init(Sum *s) {
    s->acc_int = 1;
    s->i_res = s->f_res = s->comp = 0.0;
}

static void sum_add(Sum *s, double product, int product_int) {
    if (s->acc_int && product_int) {
        s->i_res += product;          /* 整数精确累加（这里用 double 表示，值域内精确） */
        return;
    }
    if (!s->acc_int && !product_int) {
        double t = s->f_res + product;
        if (fabs(s->f_res) >= fabs(product)) s->comp += (s->f_res - t) + product;
        else s->comp += (product - t) + s->f_res;
        s->f_res = t;
        return;
    }
    /* 类型切换：Python 走「通用加法」一次，不做补偿。 */
    s->f_res = (s->acc_int ? s->i_res : s->f_res) + product;
    s->acc_int = 0;
}

static double sum_value(Sum *s) { return s->acc_int ? s->i_res : s->f_res + s->comp; }

/* --------------------------------------------------------------- 有序计数表 */
/* 复刻 `collections.Counter`：键按**首次插入顺序**保留，后插入的等值键并入
 * 第一个键；每个槽位记住自己的数值是不是 int。 */
typedef struct {
    PyObject *index;
    PyObject **keys;
    double *values;
    unsigned char *is_int;
    Py_ssize_t n, cap;
} Ordered;

static int ordered_init(Ordered *ord) {
    memset(ord, 0, sizeof(*ord));
    ord->index = PyDict_New();
    return ord->index ? 0 : -1;
}

static void ordered_clear(Ordered *ord) {
    Py_ssize_t i;
    for (i = 0; i < ord->n; i++) Py_DECREF(ord->keys[i]);
    free(ord->keys);
    free(ord->values);
    free(ord->is_int);
    Py_CLEAR(ord->index);
    memset(ord, 0, sizeof(*ord));
}

static int ordered_grow(Ordered *ord) {
    Py_ssize_t cap = ord->cap ? ord->cap * 2 : 16;
    PyObject **keys = (PyObject **)realloc(ord->keys, (size_t)cap * sizeof(PyObject *));
    double *values;
    unsigned char *flags;
    if (!keys) { PyErr_NoMemory(); return -1; }
    ord->keys = keys;
    values = (double *)realloc(ord->values, (size_t)cap * sizeof(double));
    if (!values) { PyErr_NoMemory(); return -1; }
    ord->values = values;
    flags = (unsigned char *)realloc(ord->is_int, (size_t)cap * sizeof(unsigned char));
    if (!flags) { PyErr_NoMemory(); return -1; }
    ord->is_int = flags;
    ord->cap = cap;
    return 0;
}

static Py_ssize_t ordered_slot(Ordered *ord, PyObject *key) {
    PyObject *marker = PyDict_GetItemWithError(ord->index, key);
    if (!marker) return -1;
    return PyLong_AsSsize_t(marker);
}

static int ordered_add(Ordered *ord, PyObject *key, double delta, int delta_int) {
    Py_ssize_t slot = ordered_slot(ord, key);
    if (slot < 0) {
        PyObject *marker;
        if (PyErr_Occurred()) return -1;
        if (ord->n == ord->cap && ordered_grow(ord) < 0) return -1;
        slot = ord->n++;
        Py_INCREF(key);
        ord->keys[slot] = key;
        ord->values[slot] = delta;
        ord->is_int[slot] = (unsigned char)delta_int;
        marker = PyLong_FromSsize_t(slot);
        if (!marker) return -1;
        if (PyDict_SetItem(ord->index, key, marker) < 0) { Py_DECREF(marker); return -1; }
        Py_DECREF(marker);
        return 0;
    }
    ord->values[slot] += delta;
    if (!delta_int) ord->is_int[slot] = 0;
    return 0;
}

static PyObject *ordered_dict(Ordered *ord) {
    PyObject *out = PyDict_New();
    Py_ssize_t i;
    if (!out) return NULL;
    for (i = 0; i < ord->n; i++) {
        PyObject *value = ord->is_int[i] ? PyLong_FromDouble(ord->values[i])
                                         : PyFloat_FromDouble(ord->values[i]);
        if (!value) { Py_DECREF(out); return NULL; }
        if (PyDict_SetItem(out, ord->keys[i], value) < 0) {
            Py_DECREF(value); Py_DECREF(out); return NULL;
        }
        Py_DECREF(value);
    }
    return out;
}

/* sign = +1 / -1，与 Python 的 update/subtract 一致地保留 int 类型。 */
static int ordered_add_map(Ordered *ord, PyObject *map, int sign) {
    PyObject *key, *value;
    Py_ssize_t pos = 0;
    while (PyDict_Next(map, &pos, &key, &value)) {
        double amount;
        int is_int;
        if (number_of(value, &amount, &is_int) < 0) return -1;
        if (ordered_add(ord, key, sign > 0 ? amount : -amount, is_int) < 0) return -1;
    }
    return 0;
}

/* ----------------------------------------------------------------- 快照读取 */
typedef struct {
    int complete;
    PyObject *kind;
    PyObject *site;
} Building;

static int read_buildings(PyObject *buildings, Building **out, Py_ssize_t *n) {
    Py_ssize_t i, size = PySequence_Size(buildings);
    Building *rows;
    if (size < 0) return -1;
    rows = (Building *)calloc((size_t)(size ? size : 1), sizeof(Building));
    if (!rows) { PyErr_NoMemory(); return -1; }
    for (i = 0; i < size; i++) {
        PyObject *item = PySequence_GetItem(buildings, i);
        PyObject *kind, *site, *remaining;
        int pending;
        if (!item) { free(rows); return -1; }
        if (!PyDict_Check(item)) {
            Py_DECREF(item); free(rows);
            PyErr_SetString(PyExc_TypeError, "_econ_fast: building 必须是 dict");
            return -1;
        }
        kind = PyDict_GetItemString(item, "kind");
        site = PyDict_GetItemString(item, "site");
        remaining = PyDict_GetItemString(item, "remaining");
        if (!kind || !site) {
            Py_DECREF(item); free(rows);
            PyErr_SetString(PyExc_KeyError, "_econ_fast: building 缺 kind/site");
            return -1;
        }
        Py_INCREF(kind); Py_INCREF(site);
        rows[i].kind = kind;
        rows[i].site = site;
        pending = remaining ? PyObject_IsTrue(remaining) : 0;
        Py_DECREF(item);
        if (pending < 0) {
            Py_ssize_t j;
            for (j = 0; j < i; j++) { Py_DECREF(rows[j].kind); Py_DECREF(rows[j].site); }
            Py_DECREF(kind); Py_DECREF(site);
            free(rows);
            return -1;
        }
        rows[i].complete = !pending;
    }
    *out = rows;
    *n = size;
    return 0;
}

static void free_buildings(Building *rows, Py_ssize_t n) {
    Py_ssize_t i;
    for (i = 0; i < n; i++) { Py_DECREF(rows[i].kind); Py_DECREF(rows[i].site); }
    free(rows);
}

static PyObject *row_of(PyObject *catalog, PyObject *kind) {
    PyObject *row = PyDict_GetItemWithError(catalog, kind);
    if (!row) {
        if (!PyErr_Occurred())
            PyErr_SetString(PyExc_KeyError, "_econ_fast: 未知建筑种类");
        return NULL;
    }
    return row;
}

/* ------------------------------------------------------------------ 物料平衡 */
static PyObject *py_net_recipe(PyObject *self, PyObject *args) {
    PyObject *catalog, *buildings, *military;
    Ordered net;
    Building *rows = NULL;
    Py_ssize_t n = 0, i;
    PyObject *result = NULL;
    if (!PyArg_ParseTuple(args, "OOO", &catalog, &buildings, &military)) return NULL;
    if (read_buildings(buildings, &rows, &n) < 0) return NULL;
    if (ordered_init(&net) < 0) { free_buildings(rows, n); return NULL; }
    for (i = 0; i < n; i++) {
        PyObject *row = row_of(catalog, rows[i].kind);
        PyObject *inputs, *outputs, *effects, *gold_base;
        if (!row) goto done;
        inputs = PyDict_GetItemString(row, "inputs");
        outputs = PyDict_GetItemString(row, "outputs");
        effects = PyDict_GetItemString(row, "effects");
        if (!inputs || !outputs || !effects) {
            PyErr_SetString(PyExc_KeyError, "_econ_fast: catalog 行缺 inputs/outputs/effects");
            goto done;
        }
        if (ordered_add_map(&net, inputs, -1) < 0) goto done;
        if (ordered_add_map(&net, outputs, +1) < 0) goto done;
        gold_base = PyDict_GetItemString(effects, "gold_base");
        if (gold_base) {
            double others = 0.0, base, per = 0.0;
            int base_int, per_int, found = 0, rc;
            Py_ssize_t j;
            PyObject *gold_key;
            for (j = 0; j < n; j++) {
                int same_site = same_key(rows[j].site, rows[i].site);
                if (same_site < 0) goto done;
                if (!same_site) continue;
                same_site = same_key(rows[j].kind, rows[i].kind);
                if (same_site < 0) goto done;
                if (!same_site) others += 1.0;
            }
            if (number_of(gold_base, &base, &base_int) < 0) goto done;
            if (dict_number_c(effects, "gold_per_slot", &per, &found, &per_int) < 0) goto done;
            if (!found) per = 0.0;
            gold_key = PyUnicode_FromString(GOLD);
            if (!gold_key) goto done;
            rc = ordered_add(&net, gold_key, base + others * per, base_int && per_int);
            Py_DECREF(gold_key);
            if (rc < 0) goto done;
        }
    }
    {
        int truth = PyObject_IsTrue(military);
        if (truth < 0) goto done;
        if (truth) {
            double need = 0.0, equipment = 0.0;
            int found = 0, need_int, equipment_int;
            PyObject *key;
            if (dict_number_c(military, "need", &need, &found, &need_int) < 0) goto done;
            if (dict_number_c(military, "equipment_need", &equipment, &found, &equipment_int) < 0)
                goto done;
            key = PyUnicode_FromString(SUPPLY);
            if (!key) goto done;
            if (ordered_add(&net, key, -need, need_int) < 0) { Py_DECREF(key); goto done; }
            Py_DECREF(key);
            key = PyUnicode_FromString(EQUIPMENT);
            if (!key) goto done;
            if (ordered_add(&net, key, -equipment, equipment_int) < 0) { Py_DECREF(key); goto done; }
            Py_DECREF(key);
        }
    }
    result = ordered_dict(&net);
done:
    ordered_clear(&net);
    free_buildings(rows, n);
    return result;
}

/* --------------------------------------------------------------- 运营物料目标 */
static PyObject *py_operating_targets(PyObject *self, PyObject *args) {
    PyObject *catalog, *buildings, *military, *prices, *buy, *sell;
    Py_ssize_t periods;
    Ordered target;
    Building *rows = NULL;
    Py_ssize_t n = 0, i;
    PyObject *result = NULL;
    if (!PyArg_ParseTuple(args, "OOOOn", &catalog, &buildings, &military, &prices, &periods))
        return NULL;
    buy = PyDict_GetItemString(prices, "buy");
    sell = PyDict_GetItemString(prices, "sell");
    if (!buy || !sell) {
        PyErr_SetString(PyExc_KeyError, "_econ_fast: prices 需要 buy/sell");
        return NULL;
    }
    if (read_buildings(buildings, &rows, &n) < 0) return NULL;
    if (ordered_init(&target) < 0) { free_buildings(rows, n); return NULL; }
    {
        double need = 0.0, equipment = 0.0;
        int found = 0, need_int = 1, equipment_int = 1, truth;
        PyObject *key;
        truth = PyObject_IsTrue(military);
        if (truth < 0) goto done;
        if (truth) {
            if (dict_number_c(military, "need", &need, &found, &need_int) < 0) goto done;
            if (dict_number_c(military, "equipment_need", &equipment, &found, &equipment_int) < 0)
                goto done;
        }
        key = PyUnicode_FromString(SUPPLY);
        if (!key) goto done;
        if (ordered_add(&target, key, need * (double)periods, need_int) < 0) { Py_DECREF(key); goto done; }
        Py_DECREF(key);
        key = PyUnicode_FromString(EQUIPMENT);
        if (!key) goto done;
        if (ordered_add(&target, key, equipment * (double)periods, equipment_int) < 0) {
            Py_DECREF(key); goto done;
        }
        Py_DECREF(key);
    }
    for (i = 0; i < n; i++) {
        PyObject *row = row_of(catalog, rows[i].kind);
        PyObject *inputs, *outputs, *key, *value;
        Py_ssize_t pos;
        Sum output, input;
        double energy_output = 0.0, out_value, in_value;
        int consumed = 0, found = 0, energy_int = 1;
        if (!row) goto done;
        inputs = PyDict_GetItemString(row, "inputs");
        outputs = PyDict_GetItemString(row, "outputs");
        if (!inputs || !outputs) {
            PyErr_SetString(PyExc_KeyError, "_econ_fast: catalog 行缺 inputs/outputs");
            goto done;
        }
        sum_init(&output);
        sum_init(&input);
        pos = 0;
        while (PyDict_Next(outputs, &pos, &key, &value)) {
            double amount, price = 0.0;
            int amount_int, have = 0, price_int = 1;
            if (number_of(value, &amount, &amount_int) < 0) goto done;
            if (is_key(key, GOLD)) {
                /* Python 里金额项是字面量 1（int），产品保持 int。 */
                sum_add(&output, amount, amount_int);
            } else {
                if (dict_number(sell, key, &price, &have, &price_int) < 0) goto done;
                sum_add(&output, amount * (have ? price : 0.0), amount_int && price_int);
            }
        }
        pos = 0;
        while (PyDict_Next(inputs, &pos, &key, &value)) {
            double amount, price = 0.0;
            int amount_int, have = 0, price_int = 1;
            if (number_of(value, &amount, &amount_int) < 0) goto done;
            if (dict_number(buy, key, &price, &have, &price_int) < 0) goto done;
            if (!have) price = INFINITY;  /* Python 取 math.inf，产品必然是 float */
            sum_add(&input, amount * price, have && amount_int && price_int);
        }
        out_value = sum_value(&output);
        in_value = sum_value(&input);
        if (dict_number_c(row, "energy_output", &energy_output, &found, &energy_int) < 0) goto done;
        if (!found) energy_output = 0.0;
        pos = 0;
        while (PyDict_Next(outputs, &pos, &key, NULL)) {
            Py_ssize_t slot = ordered_slot(&target, key);
            if (slot < 0 && PyErr_Occurred()) goto done;
            if (slot >= 0 && target.values[slot] > 0.0) { consumed = 1; break; }
        }
        if (energy_output != 0.0 || PyDict_Size(inputs) == 0 || out_value >= in_value || consumed) {
            pos = 0;
            while (PyDict_Next(inputs, &pos, &key, &value)) {
                double amount;
                int amount_int;
                if (number_of(value, &amount, &amount_int) < 0) goto done;
                /* amount * periods：periods 是 int，只有 amount 是 int 才是 int。 */
                if (ordered_add(&target, key, amount * (double)periods, amount_int) < 0) goto done;
            }
        }
    }
    result = ordered_dict(&target);
done:
    ordered_clear(&target);
    free_buildings(rows, n);
    return result;
}

/* ------------------------------------------------------------------ 现金储备 */
static PyObject *py_reserve_cash(PyObject *self, PyObject *args) {
    PyObject *catalog, *buildings, *military, *buy, *profile;
    Ordered needs;
    Building *rows = NULL;
    Py_ssize_t n = 0, i;
    PyObject *result = NULL;
    if (!PyArg_ParseTuple(args, "OOOOO", &catalog, &buildings, &military, &buy, &profile))
        return NULL;
    if (read_buildings(buildings, &rows, &n) < 0) return NULL;
    if (ordered_init(&needs) < 0) { free_buildings(rows, n); return NULL; }
    {
        double need = 0.0, equipment = 0.0;
        int found = 0, need_int = 1, equipment_int = 1, truth;
        PyObject *key;
        truth = PyObject_IsTrue(military);
        if (truth < 0) goto done;
        if (truth) {
            if (dict_number_c(military, "need", &need, &found, &need_int) < 0) goto done;
            if (dict_number_c(military, "equipment_need", &equipment, &found, &equipment_int) < 0)
                goto done;
        }
        key = PyUnicode_FromString(SUPPLY);
        if (!key) goto done;
        if (ordered_add(&needs, key, need, need_int) < 0) { Py_DECREF(key); goto done; }
        Py_DECREF(key);
        key = PyUnicode_FromString(EQUIPMENT);
        if (!key) goto done;
        if (ordered_add(&needs, key, equipment, equipment_int) < 0) { Py_DECREF(key); goto done; }
        Py_DECREF(key);
    }
    for (i = 0; i < n; i++) {
        PyObject *row = row_of(catalog, rows[i].kind);
        PyObject *inputs;
        if (!row) goto done;
        inputs = PyDict_GetItemString(row, "inputs");
        if (!inputs) {
            PyErr_SetString(PyExc_KeyError, "_econ_fast: catalog 行缺 inputs");
            goto done;
        }
        if (ordered_add_map(&needs, inputs, +1) < 0) goto done;
    }
    {
        PyObject *periods = PyObject_GetAttrString(profile, "cash_periods");
        double cash_periods;
        Sum total;
        Py_ssize_t slot;
        int periods_int = 1;
        if (!periods) goto done;
        if (number_of(periods, &cash_periods, &periods_int) < 0) { Py_DECREF(periods); goto done; }
        Py_DECREF(periods);
        sum_init(&total);
        for (slot = 0; slot < needs.n; slot++) {
            double price = 0.0;
            int have = 0, price_int = 1;
            if (dict_number(buy, needs.keys[slot], &price, &have, &price_int) < 0) goto done;
            /* Counter 里的值是 int 时，`v*buy.get(k,0)` 也是 int（缺价按 0）。 */
            sum_add(&total, needs.values[slot] * (have ? price : 0.0),
                    needs.is_int[slot] && price_int);
        }
        {
            double weight = sum_value(&total);
            result = PyLong_FromDouble(ceil(20 + cash_periods * weight));
            (void)periods_int;
        }
    }
done:
    ordered_clear(&needs);
    free_buildings(rows, n);
    return result;
}

/* ------------------------------------------------------------------ 市场情景 */
/* 复刻 `MarketModel.path` + `value`。`net_items` 是保持 dict 插入顺序的
 * (键, 数量) 列表；数量与价格的 int 类型也一并保留，好让下面的求值和
 * CPython 的 `sum()` 走同一条路径。 */
static PyObject *py_market_value(PyObject *self, PyObject *args) {
    PyObject *model, *net_items, *own_net, *extra;
    Py_ssize_t horizon;
    double crowd;
    PyObject *inventory, *reference, *buy0, *sell0, *base, *elasticity, *external,
             *order_offset, *prices, *flow_obj;
    double flow;
    Py_ssize_t nres, i, t, nnet = 0;
    PyObject **keys = NULL, **net_keys = NULL;
    double *amounts = NULL, *inv = NULL, *ref = NULL, *b0 = NULL, *s0 = NULL,
           *elast = NULL, *ext = NULL, *buy = NULL, *sell = NULL;
    unsigned char *amount_int = NULL, *buy_int = NULL, *sell_int = NULL;
    double total = 0.0, weight = 0.0;
    PyObject *key, *value, *result = NULL, *idx = NULL;
    Py_ssize_t pos = 0;
    if (!PyArg_ParseTuple(args, "OOOOnd", &model, &net_items, &own_net, &extra,
                          &horizon, &crowd))
        return NULL;
    inventory = PyDict_GetItemString(model, "inventory");
    reference = PyDict_GetItemString(model, "reference");
    buy0 = PyDict_GetItemString(model, "buy");
    sell0 = PyDict_GetItemString(model, "sell");
    base = PyDict_GetItemString(model, "base");
    elasticity = PyDict_GetItemString(model, "elasticity");
    external = PyDict_GetItemString(model, "external");
    order_offset = PyDict_GetItemString(model, "order_offset");
    prices = PyDict_GetItemString(model, "prices");
    flow_obj = PyDict_GetItemString(model, "flow");
    if (!inventory || !reference || !buy0 || !sell0 || !base || !elasticity ||
        !external || !order_offset || !prices || !flow_obj) {
        PyErr_SetString(PyExc_KeyError, "_econ_fast: 市场快照字段不全");
        return NULL;
    }
    {
        int is_int;
        if (number_of(flow_obj, &flow, &is_int) < 0) return NULL;
    }
    nres = PyDict_Size(inventory);
    if (nres < 0) return NULL;
    keys = (PyObject **)calloc((size_t)(nres ? nres : 1), sizeof(PyObject *));
    inv = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    ref = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    b0 = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    s0 = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    elast = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    ext = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    buy = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    sell = (double *)calloc((size_t)(nres ? nres : 1), sizeof(double));
    buy_int = (unsigned char *)calloc((size_t)(nres ? nres : 1), sizeof(unsigned char));
    sell_int = (unsigned char *)calloc((size_t)(nres ? nres : 1), sizeof(unsigned char));
    if (!keys || !inv || !ref || !b0 || !s0 || !elast || !ext || !buy || !sell ||
        !buy_int || !sell_int) {
        PyErr_NoMemory();
        goto done;
    }
    i = 0;
    while (PyDict_Next(inventory, &pos, &key, &value)) {
        double amount, offset = 0.0, number;
        int found = 0, is_int = 0;
        if (number_of(value, &amount, &is_int) < 0) goto done;
        if (dict_number(order_offset, key, &offset, &found, &is_int) < 0) goto done;
        amount += offset;
        keys[i] = key;
        inv[i] = amount > 0.0 ? amount : 0.0;
        if (dict_number(reference, key, &number, &found, &is_int) < 0) goto done;
        if (!found) { PyErr_SetString(PyExc_KeyError, "_econ_fast: 缺基准库存"); goto done; }
        ref[i] = number;
        if (dict_number(buy0, key, &number, &found, &is_int) < 0) goto done;
        if (!found) { PyErr_SetString(PyExc_KeyError, "_econ_fast: 缺买价"); goto done; }
        b0[i] = number;
        buy_int[i] = (unsigned char)is_int;
        if (dict_number(sell0, key, &number, &found, &is_int) < 0) goto done;
        if (!found) { PyErr_SetString(PyExc_KeyError, "_econ_fast: 缺卖价"); goto done; }
        s0[i] = number;
        sell_int[i] = (unsigned char)is_int;
        if (dict_number(elasticity, key, &number, &found, &is_int) < 0) goto done;
        if (!found) { PyErr_SetString(PyExc_KeyError, "_econ_fast: 缺弹性"); goto done; }
        elast[i] = number;
        if (dict_number(external, key, &ext[i], &found, &is_int) < 0) goto done;
        i++;
    }
    nres = i;
    {
        Py_ssize_t size = PySequence_Size(net_items);
        if (size < 0) goto done;
        nnet = size;
        net_keys = (PyObject **)calloc((size_t)(nnet ? nnet : 1), sizeof(PyObject *));
        amounts = (double *)calloc((size_t)(nnet ? nnet : 1), sizeof(double));
        amount_int = (unsigned char *)calloc((size_t)(nnet ? nnet : 1), sizeof(unsigned char));
        if (!net_keys || !amounts || !amount_int) { PyErr_NoMemory(); goto done; }
        for (i = 0; i < nnet; i++) {
            PyObject *pair = PySequence_GetItem(net_items, i);
            int is_int;
            if (!pair) goto done;
            if (!PyTuple_Check(pair) || PyTuple_GET_SIZE(pair) != 2) {
                Py_DECREF(pair);
                PyErr_SetString(PyExc_TypeError, "_econ_fast: net_items 需要 (键, 数量)");
                goto done;
            }
            key = PyTuple_GET_ITEM(pair, 0);
            Py_INCREF(key);
            net_keys[i] = key;
            if (number_of(PyTuple_GET_ITEM(pair, 1), &amounts[i], &is_int) < 0) {
                Py_DECREF(pair);
                goto done;
            }
            amount_int[i] = (unsigned char)is_int;
            Py_DECREF(pair);
        }
        idx = PyDict_New();
        if (!idx) goto done;
        for (i = 0; i < nres; i++) {
            PyObject *marker = PyLong_FromSsize_t(i);
            if (!marker) goto done;
            if (PyDict_SetItem(idx, keys[i], marker) < 0) { Py_DECREF(marker); goto done; }
            Py_DECREF(marker);
        }
        for (t = 0; t < horizon; t++) {
            /* .96**t / .97**t：与 CPython 的 float.__pow__ 一样走 libm pow。 */
            double ext_turn = pow(0.96, (double)t);
            double discount = pow(0.97, (double)t);
            double value_sum;
            for (i = 0; i < nres; i++) {
                double own = 0.0, add = 0.0, step, base_price, current, factor;
                int found = 0, is_int = 0;
                if (dict_number(own_net, keys[i], &own, &found, &is_int) < 0) goto done;
                if (dict_number(extra, keys[i], &add, &found, &is_int) < 0) goto done;
                step = ext[i] * ext_turn + own;
                step += crowd * add;
                inv[i] = inv[i] + step > 0.0 ? inv[i] + step : 0.0;
                ref[i] = ref[i] - flow * step > 1.0 ? ref[i] - flow * step : 1.0;
                if (elast[i] > 0.0) {
                    double log_price;
                    if (dict_number(base, keys[i], &base_price, &found, &is_int) < 0) goto done;
                    if (!found) { PyErr_SetString(PyExc_KeyError, "_econ_fast: 缺均衡价"); goto done; }
                    log_price = log(base_price) + elast[i] * (1.0 - inv[i] / ref[i]);
                    if (log_price < -14.0) log_price = -14.0;
                    if (log_price > 21.0) log_price = 21.0;
                    if (dict_number(prices, keys[i], &current, &found, &is_int) < 0) goto done;
                    if (!found) { PyErr_SetString(PyExc_KeyError, "_econ_fast: 缺现价"); goto done; }
                    factor = exp(log_price) / current;
                } else {
                    factor = 1.0;
                }
                buy[i] = b0[i] * factor * (inv[i] >= 1.0 ? 1.0 : 10.0);
                sell[i] = s0[i] * factor;
            }
            {
                Sum sum;
                sum_init(&sum);
                for (i = 0; i < nnet; i++) {
                    double amount = amounts[i], price;
                    int price_int = 1;
                    PyObject *marker;
                    Py_ssize_t at;
                    if (!amount) continue;
                    if (is_key(net_keys[i], GOLD)) {
                        price = 1.0;   /* Python 侧是字面量 1（int） */
                    } else {
                        marker = PyDict_GetItemWithError(idx, net_keys[i]);
                        if (!marker) {
                            if (PyErr_Occurred()) goto done;
                            /* 参考实现：不在买卖价表里的资源按无穷价 ⇒ 整体 -∞ */
                            result = PyFloat_FromDouble(-INFINITY);
                            goto done;
                        }
                        at = PyLong_AsSsize_t(marker);
                        if (at < 0) goto done;
                        if (amount > 0) { price = sell[at]; price_int = sell_int[at]; }
                        else { price = buy[at]; price_int = buy_int[at]; }
                    }
                    sum_add(&sum, amount * price, amount_int[i] && price_int);
                }
                value_sum = sum_value(&sum);
            }
            if (!isfinite(value_sum)) {
                result = PyFloat_FromDouble(-INFINITY);
                goto done;
            }
            total += value_sum * discount;
            weight += discount;
        }
    }
    result = PyFloat_FromDouble(total / weight);
done:
    if (net_keys) {
        for (i = 0; i < nnet; i++) Py_XDECREF(net_keys[i]);
        free(net_keys);
    }
    free(amounts); free(amount_int);
    Py_XDECREF(idx);
    free(keys); free(inv); free(ref); free(b0); free(s0);
    free(elast); free(ext); free(buy); free(sell); free(buy_int); free(sell_int);
    return result;
}

static PyMethodDef methods[] = {
    {"net_recipe", py_net_recipe, METH_VARARGS, "v13 稳态物料平衡"},
    {"operating_targets", py_operating_targets, METH_VARARGS, "v13 运营物料目标"},
    {"reserve_cash", py_reserve_cash, METH_VARARGS, "v13 现金储备"},
    {"market_value", py_market_value, METH_VARARGS, "v13 市场情景折现估值"},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_econ_fast", "v13 经济求解器 C 快路径", -1, methods,
};

/* 编译时写入 `_econ_fast.c` 的 SHA-256；Python 侧据此拒绝过期二进制。 */
#ifndef ECON_FAST_DIGEST
#define ECON_FAST_DIGEST "unverified"
#endif

PyMODINIT_FUNC PyInit__econ_fast(void) {
    PyObject *mod = PyModule_Create(&module);
    if (!mod) return NULL;
    if (PyModule_AddStringConstant(mod, "DIGEST", ECON_FAST_DIGEST) < 0) {
        Py_DECREF(mod);
        return NULL;
    }
    return mod;
}
