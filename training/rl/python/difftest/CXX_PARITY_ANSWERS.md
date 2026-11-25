# 回答 CXX_PARITY_REMAINING.md 的两个问题（以 Python 参照实现 `mp.py` 为准，scD/scF 在 Python 里 0 差异）

## 问题 2（scF t12 多一支 67hp 军）——不是死亡，是**主动遣散**

在 Python 参照实现里把 scF 重放到 t11/t12：

```
TURN 11 post 国1: [(id4, (7,13), 37.0), (id5, (7,14), 79.0)]
TURN 12 国1 动作: disband {'ids': [4]} ; recruit ; move {'aid': 5 ...}
TURN 12 post 国1: [(id5,(7,13),100), (id6,(8,13),100)]      # id4（37hp 那支）已不存在
```

`scF.py` 脚本里 n1 在 **t12、t20、t28 各遣散 `mine[0]`（第一支军）**，n0 在 t14、t30 遣散最后一支。
所以那支 37hp 军在 t12 开头被 `disband` 掉，不是被打死/饿死。C++ 多出来的 67hp 说明 **C++ 回放没有执行这条 `disband`**：
- 检查 `tools/ref2txt.py`：`disband` 的参数是 **`ids`（数组）**，不是 `id`（旧脚本用过 `id` 单值，新录的是 `ids`）；
  `aid` → C++ 军队编号的映射要用 `ids` 里的每个编号查。
- 遣散语义：立即从军队表删除；交战中不可遣散（ref 里这支 t11 已脱离交战，`engaged=False`）；不返还任何资源；
  民兵的编制名额当场释放；当回合起不再吃补给。
- 验证方法：在 C++ 回放里 t12 开头打印 n1 的军队表，应只剩 id5(79hp→回血 100)；n1 本回合 recruit 出 id6。

## 问题 1（scD 上贡逐笔取整）

### 先看一个高度可疑的点：单位换算
你写的 `(v*100*pct + carry)/100` 是**乘了 100 又除 100**，结果 = `v*pct`，**等于把产出的 15 倍当贡品**——
这与你报的症状吻合（t4 起 n0 的 food/wood/ore 多出一倍以上、n1 的变 0 或少很多）。
Python 的正确式（整数，`pct`=15，`carry` 单位是 1/100 件）：

```python
tot = amt * pct + carry            # amt: 本笔物理产出（整数）；pct: 15；carry: 0..99
give, carry_new = divmod(tot, 100) # 取整后的贡品件数；余数留作 carry（1/100 件）
# carry_new == 0 时删除该 key
```
即 `give = floor((amt*pct + carry)/100)`。**不要再乘 100。**

### Python 实现原文（`mp.py`）

```python
def _credit_output(self, owner, good, amt):
    v = self.vassals.get(owner)
    if v and amt > 0 and v["overlord"] in self.nations:
        o = v["overlord"]
        key = f"{owner}|{o}|{good}"                    # carry 的键：(附庸,宗主,资源)
        give, rem = divmod(amt * v.get("tribute_percent", 15) + self.tribute_carry.get(key, 0), 100)
        if rem: self.tribute_carry[key] = rem
        else:   self.tribute_carry.pop(key, None)
        if give:
            self._tribute_out[(owner, o, good)] = self._tribute_out.get((owner, o, good), 0) + give
            amt -= give
    self.add_res(owner, good, amt)                     # 附庸自己只拿扣后的
```

### 逐条回答

1. **调用点（共 4 处，各自调用）**，全部在 `resolve_turn` 的生产段：
   - 采集建筑：对每个有建筑的地块、每种采集建筑、每个产出项各调一次：`_credit_output(owner, g, amt*cnt)`（`cnt`=该地块该建筑座数）。
   - 金矿：`_credit_output(owner, "黄金", outputs["黄金"]*cnt*MARKET["黄金"])`（黄金折算率 10）。
   - 工厂（补给厂/装备厂）：投入扣完后，每个产出项 `_credit_output(n, g, amt*batches)`。
   - 市政厅：**每个有市政厅的地块**各调一次 `_credit_output(n, "黄金", hall_gain)`，
     `hall_gain = (gold_base + 该地块其他建筑座数*gold_per_slot) * 市政厅座数`。市政厅只在**电网不足=False**（`not short`）时产金。
   - 能源厂**没有产出**，不调用（只消耗燃料）。
2. **投入/燃料不走 `_credit_output`**：工厂投入、能源厂燃料都是 `add_res(n, f, -need*batches)` 的普通负值，不扣贡、不动 carry。
3. **贡品划给宗主的时点**：整个生产段（采集→电网→工厂→市政厅）全部结束之后、`resolve_diplomacy`（外交命令队列结算）**之前**、补给与战斗之前：
   ```python
   for (s, o, g), give in sorted(self._tribute_out.items()):
       if give and o in self.nations: self.add_res(o, g, give)
   self._tribute_out = {}
   ```
   宗主收到的贡品**不再被抽成、本期不能用于生产**（也不计入宗主的 `prod` 毛产出，生产/GDP 记账按附庸毛产出）。
4. **单位**：见上，产出是整数物理件数，`pct` 是整数百分比，carry 是 1/100 件（0..99 的整数）。存档里 carry 用字符串键 `"附庸|宗主|资源"`。
5. **是的，同回合后续生产链只能吃扣贡后的库存**：`add_res(owner, g, amt - give)` 在产出入账的当下就扣了，后面的工厂读到的是扣后的库存。

### 顺序细节（也会影响逐笔取整的结果）
- Python 的生产顺序：**先**遍历 `tiles`（字典插入顺序）处理**所有国家**的采集/金矿（第 1 阶段），**再**按国家处理 电网 → 工厂 → 市政厅（第 2 阶段）。
  同一 `(附庸,宗主,资源)` 的 carry 是按这个调用顺序依次滚动的，**总量与顺序无关，但每一笔的 give 与顺序有关**；
  逐笔 give 只在"后续工厂能否开工"上有影响，所以工厂的资源阈值要按 Python 这个顺序走。
- 新关系的生效时点：附庸关系由外交命令队列在 `resolve_diplomacy`（生产**之后**）建立，所以**接受称臣那回合不收贡**，下一回合起才收。
- 关系解除（释放/独立战争/宗主灭亡）后 carry **保留不清**（再次同一对关系时继续累计）；`tribute_percent` 取关系里的值（普通附庸 15）。

## 备注
rpi5 上的 C++ 工程我这边连不上（`ssh rpi5` 解析不到主机）。若需要我直接改并复测，请把 `~/zhanguo-cpp`（含 `refs/`、`tools/`）
拷到 rpi3 或给我一个可达的主机别名；否则按上面两点改后跑 `bash run_sc.sh` 即可验证（预期 scD、scF 都应 0 差异）。
