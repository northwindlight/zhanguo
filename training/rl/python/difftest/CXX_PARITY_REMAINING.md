# C++ 引擎剩余 2 处未对齐（scD / scF）— 问题说明

工程位置：rpi5 `~/zhanguo-cpp`（C++ 引擎 + 回放工具）；参照对局在 `~/zhanguo-cpp/refs/sc*.json`
（从 rpi3 拷来，seed 7005；scA/B/D/E/F size 16，scC size 20）。

## 复现

```bash
cd ~/zhanguo-cpp
bash run_sc.sh          # scA..scF 转换 + 回放 + 比对
bash run_refs.sh        # 7 局规则 AI 参照（7001-7006）
```

单局：

```bash
python3 tools/ref2txt.py refs/scD.json 7005 16 /tmp/scD.txt
./zhanguo replay /tmp/scD.txt /tmp/scD.dump
python3 tools/cmp.py refs/scD.json /tmp/scD.dump
```

## 当前状态

- **0 差异**：7 局规则 AI 参照、scA（借款/和平）、scB（宣战/议和）、scC（保障/防御/联盟/投票/改名）、scE（拆除/城堡）
- **未对齐**：scD（first_bad=t10）、scF（first_bad=t12）

---

## 问题 1：scD t10 起 supply 差 1（上贡逐笔取整）

**现象**（t10）：n0.supply ref 9 / cpp 8；n1.supply ref 7 / cpp 8。附庸 n1 → 宗主 n0 上贡 supply。

**已定位根因**：main 的上贡是**每笔产出先扣**（claude 的 `_credit_output`：
`divmod(amt * pct + carry, 100)`，carry 以 1/100 为整数跨期结转，扣下的贡品在生产结束后划给宗主）；
C++ 是**结算后按本回合总产出一次性转移**（double carry）。逐笔取整与总额取整差 1。

**试过的修法**：把 C++ 的 production_phase 每处产出改成
`give = tribute_take(n, r, v); add_res(n, r, v - give);`，并在 production 后 `tribute_flush()` 划给宗主。
结果 **scD 从 t10 恶化到 t4 起 11 项差**：

```
t4: n0.gold 169/189, n0.food 10/20, n0.wood 6/12, n0.ore 14/21, n0.supply 7/13
    n1.gold 105/84, n1.food 10/0 ...
```

说明我的逐笔实现里**扣贡/划贡的顺序或基数**还有错。已回退到结算后转移（当前 scD=t10）。

**需要确认的点（claude 的 Python 实现或 main 源码）**：

1. `_credit_output` 的调用点：采集、工厂（supply/equip）、市政厅、金矿，是否各自调用一次？
2. 工厂的**投入**（food/ore 消耗）是走 `add_res` 负值（不扣贡），还是也走 `_credit_output`？
3. `_tribute_out` 划给宗主的时点（生产结束后、补给/战斗/relations 之前还是之后）？
4. 逐笔 divmod 的输入单位（物理量→1/100 的换算）与 carry 的表示，确认与我的 `(v*100*pct + carry)/100` 一致。
5. 附庸自己的生产链是否用**扣贡后**的库存（注释说"是，本期后续生产链只能用扣后的余量"）——
   即同回合内后续工厂只能吃扣贡后的 food/ore？

---

## 问题 2：scF t12 起多一支 67hp 军

**现象**（t12）：

- ref  n1：[infantry 7,13 100]、[infantry 8,13 100]
- cpp  n1：[infantry 7,13 67]、[infantry 7,13 100]、[infantry 8,13 100]

cpp 多的 67hp = t11 的 37hp 军回血 30（37+30=67）。

t11：ref n1 = [7,13 37]、[7,14 79]；t12 变成两支 100（7,13 100、8,13 100）——
**t11 的 37hp 那支在 ref 里消失了**，cpp 留在 7,13 回血成 67。

**根因方向**：t12 的供应/战斗/死亡判定差异（ref 的 37hp 军死亡，cpp 未死）。
该局 AI 有 **1369 次 recruit、军队反复战死重生**，ref 的 after 里军队无 id（只有 kind/x/y/hp），
逐支 id 追踪还没做。

**需要确认的点**：

1. main 的军队死亡发生在哪个阶段（supply 断粮？battle 后移除？），以及"军队消失"的其他途径。
2. t12 的 ref 军队两支 100 的来源——`log[t12].actions` 里 n1 的 recruit 回执 `res.data` 的
   id/x/y/hp（能确认新兵是哪支、老军是否回血）。
3. 若有 t11 的 battle detail（骰子/承伤），可定位 37hp 军的死亡回合。

---

## C++ 侧实现位置

- 上贡：`economy.cpp` `tribute_take` / `tribute_flush`（当前未启用）＋ `turn.cpp` 的"3) 附庸上贡"段（结算后转移）
- 外交命令队列：`diplo.cpp` `submit_diplo` / `resolve_diplomacy`；挂点 `turn.cpp`（production 后、supply 前）
- 征召队列：`army.cpp` `recruit`（登记）＋ `turn.cpp` 开头 `spawn_recruited`（下回合出兵）
- 实体：`diplo.cpp` `entity()`（= 附庸合并；联盟 bloc **不是**实体）
- GDP：`turn.cpp` market_settle 后（产出−中间投入−燃料+金币，按收盘价）
- 比对：`tools/ref2txt.py`（动作→文本，含外交/银行/拆除/撤退/联盟编辑）、`tools/cmp.py`（逐回合逐字段）

## 已对齐的队列/语义（供对照参考）

外交队列（提交干跑+预扣 → production 后按优先级重验 → 失败退款、未回应一回合过期）、
征召队列（下回合出兵）、GDP 收盘价、实体=附庸合并、信件按字数计费、赠礼含黄金、
bloc_join 收费、上贡含金币与余额结转。
