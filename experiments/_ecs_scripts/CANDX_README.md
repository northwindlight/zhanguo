# candx 交付包（候选互相可见 · 2 号模型头号改动）

**状态**：两版 diff **已入库未落地**（Pi 20:17 审查通过，`2626084` + `16fe67a`）。
**落地时机**：周末新机（≥32 GB）→ 重走 BC→锚 PPO。落地前硬要求 §3.1（K≈600 补测）**已于 2026-09-14 夜完成，见 §4.5**。

## 1. 是什么
读出段（`rl/transformer.py` 的 `h = ln_c(att + qq)` 之后）加一步**候选间自注意力**：

```
att2 = <层>(h, h, h, key_padding_mask=~cand_mask)      # padding 候选不许当 K/V
h    = ln_cand(att2 + h)                               # 残差；score 与 exec_head 继续吃同一份 [h,q0]
```

- **tied 版**（`transformer_candx.py` / `candx.diff`）：`att2` 复用已训的 `self.cross` 权重；新增参数只有 `ln_cand`（**+384，+0.018%**）。
- **indep 版**（`transformer_candx2.py` / `candx2.diff`，Pi 审查后指定方向）：独立新层 `self.cross2`；**+148,608（+7.040%）**，2,110,851 → 2,259,459。
- 动机（两条独立证据，勿重推）：v2 尺三臂 `score` 组 ‖μ‖² 全 ≤0（从 BC 起点就学不动）；`score` 只用出 0.57 而 q0 线性可读到 0.92。独立打分在结构上表达不了"这个比那个好，因为那个此刻点不动"。
- 成本口径：**叠加不是替换**（O(K·T·d) + O(K²·d)）。

## 2. 已写死的裁决判据（Pi 20:17，落地方执行）
> 落地后各用 `probe_grad_noise_v2` 测一炉 **12 局**（同池偏移、同协议），比 **score 组 ‖μ‖² 的 z**：
> - indep 的 z 显著 > tied ⇒ 表达力优势成立 ⇒ **落 indep**
> - 两者都仍 ≤0 或分不开 ⇒ "绑权重压表达力"不成立 ⇒ **按参数优先落 tied**
> - **只报原始数（z、‖μ‖²、B_ep 或"仍分辨不出"），判读归 Pi。**

## 3. 为什么"绑权重 vs 独立层"在 init 处判不了（本轮最重要的方法论条）
① 的扰动效应量 **∝ 新层权重训练过与否**，不 ∝ 表达关系的能力：tied 走已训 cross（信号强），indep 走随机初值（信号弱）
⇒ init 处比的是"谁的初值大"。两版结构上都全连通（159/159、274/274 变）。
**⇒ "绑权重压表达力"是设计性命题，只能训练后判（§2 判据即为此设）。**

## 4. 验收原始数（五判据，全过；三模型并排）
- **① 决定性**（扰动候选 j 的 amount_idx，看 i≠j）：
  old **0/159、0/274（最大 0.000e+00）**；tied 浅局 159/159（最大 1.97e−2/中位 6.73e−3）、深局 274/274（2.66e−2/5.37e−3）；
  indep 浅局 159/159（1.27e−2/2.98e−3）、深局 274/274（1.00e−3/2.99e−4）。indep 协议含 cross2 随机初值固定 `manual_seed(12345)`。
- **② 参数量**：old 2,110,851；tied 2,111,235（+384）；indep 2,259,459（+148,608）。
- **③ 接口**：`policy_logits/act/forward_batch` 未动；`forward` 签名两版与 old 字符串级一致。
- **④ 冒烟**：批内 padding 115 个（K_max=275 双步批）两版都守 −1e9、valid/value 全有限无 nan。
  （过程记录：初版④单观测批 padding=0 是**空检**，自查后改双步批造 padding——保留此记录防后人再交空检。）
- **⑤ 墙钟（单核，中位/p90 ms）**：
  K=160：old 43.0/45.9，tied 45.2/50.7，indep 45.0/49.3；
  K=351：old 46.1/47.3，tied 53.7/54.6，indep 53.8/56.3。

## 4.5 落地前硬要求 K≈600 —— **已补测（`time_candx_k600.py`，真·500T 局）**
bc1@100 在 seed 900000 的 500 回合自走局：8912 步到终局，**全程最大 K=804**（>600，不用合成垫高）。
最大时点前向墙钟（20 次）：**old 64.7/65.1，tied 87.0/88.7，indep 86.7/88.1 ms ⇒ candx 在 K≈800 是 +34%，两版等价**。
⇒ 边界条件 §3.1 关闭；**注意 +34% 只是单观测前向，rollout 侧要按"叠加"预算（T500e8 每块成本 ≈ ×2.3 步数 × ×1.34 前向）**。

## 5. 文件清单（md5 = `md5sum` 原文，2026-09-14 20:3x，勿手抄）
```
transformer_candx.py    69701e206158858810fd89af2583aa20   绑定版全文
candx.diff              e9bdec1f1f6dd11a731764246050d0c5   绑定版 diff（30 行）
transformer_candx2.py   d648b894a1a4e1698116a59955312ec6   独立层版全文
candx2.diff             16b32328ce9536c07ed9c5a16b8b861b   独立层版 diff（32 行）
test_cand_visibility.py f6751d45483753826f6969afcb8077d6   ①-⑤ 三模型并排自检（可重跑）
time_candx_k600.py      8f9850463f766678c4c6c2edb8c02a32   K=804 计时脚本
（日志）candx_acceptance.log 9368b1f5e22a7836347d81748e215d91 · candx_acceptance_v2.log fa1cffdf54bdc82173ceedc9b6618220 · candx_k600.log 7c722356ddde8bceec08615f71ef9442
```
重跑：`OMP_NUM_THREADS=1 PYTHONPATH=$HOME/zhanguo ~/.venv/bin/python experiments/_ecs_scripts/test_cand_visibility.py`

## 6. 落地时唯一已知的硬坑
**candx 在 init 处不是恒等**（`att2≠0`，即使 ln_cand 默认初始化）⇒ 老权重装进去 logits 立刻变
⇒ **落地 = 换模型重生**：必须 **重走 BC → 锚 PPO**，不存在"续训无缝"。500 视界落地前先读 §4.5。
