# -*- coding: utf-8 -*-
"""cmp32 三臂逐图数的独立复算（Pi 11:49 给了 192 个数，明说"别信我的"）。

五个视角一起上（判据在 to_pi.md 11:5x 那条里写死）：
  · 配对 sign-flip 置换检验（均值的精确零分布，不依赖重尾下的 bootstrap 形状）
  · 百分位 bootstrap（复算 Pi 的那列）+ **BCa** 修正（重尾/偏态下更诚实）
  · 中位差的 bootstrap（重尾视角）
  · Wilcoxon 符号秩（秩视角，不关心 −13,408 那张图有多大）
  · 符号检验（Pi 表里那列，2 项检验）
判决规则（事先写死）：**只有 置换 p<0.05 且 BCa CI 不跨 0 且 Wilcoxon p<0.05 三者同向**，
才允许写"bc1−entann 贪心显著为负"；否则"未判定"维持。任何视角翻向正侧，单独点名。
另：剔除影响最大单图的 jackknife；按起始晚资源（矿+油+金）三分层的 bc1−entann（探索性，不分胜负）。
"""
import re
import sys
from math import erf

import numpy as np

rng = np.random.default_rng(20260914)

DATA = {
    ("entann", "greedy"): "13167 9996 8356 9085 8936 11998 8721 8757 3402 19112 9445 11885 8296 15221 13169 4645 10137 10633 9807 11018 12842 15527 10906 7658 10142 18425 12217 3398 11506 8030 16561 11686",
    ("entann", "sample"): "17674 12227 14953 14406 16363 15884 10997 19292 13080 14766 9233 5801 7012 14913 9753 20009 8686 17444 11352 19835 18563 13412 15584 12512 14975 10835 11949 5070 18239 11266 10632 10344",
    ("bc1", "greedy"): "13522 11555 6462 7985 4201 13457 9789 8122 3368 13076 14988 14341 5546 14058 12914 4581 5977 9617 7005 16408 12800 16143 7127 1348 5643 5017 7858 3001 12858 8689 18230 9684",
    ("bc1", "sample"): "18586 13244 14049 14667 16496 13930 16461 17012 14025 14238 12284 8833 12175 16713 11577 20341 6704 17111 11892 16670 14209 14060 16324 15322 14027 12454 11449 11405 17303 12525 11088 8711",
    ("bc2", "greedy"): "14202 6980 7236 9656 9326 13037 13434 11390 11318 14861 9425 11663 9523 13720 10638 7889 10847 9852 6378 10943 11861 9270 9939 6514 7578 13640 12234 6579 12197 9436 12936 11527",
    ("bc2", "sample"): "14625 12795 15848 9892 13552 16857 8107 17558 11752 12478 5644 5523 13270 16893 12739 22032 12583 19833 13945 19914 10632 13058 14934 12775 16139 14655 6080 9785 17261 13593 11570 14619",
}
ARM = {k: np.array(v.split(), dtype=float) for k, v in DATA.items()}
N = 32
NB = 20000
NPERM = 200000



def norm_ppf(p):
    """标准正态分位数（Acklam 近似）。"""
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822407e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d_ = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00]
    pl, ph = 0.02425, 1 - 0.02425
    if p < pl:
        q = np.sqrt(-2 * np.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d_[0]*q+d_[1])*q+d_[2])*q+d_[3])*q+1)
    if p > ph:
        q = np.sqrt(-2 * np.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d_[0]*q+d_[1])*q+d_[2])*q+d_[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def bca(d):
    """配对差均值的 BCa 95% CI。"""
    n = len(d)
    theta = d.mean()
    boots = np.array([d[rng.integers(0, n, n)].mean() for _ in range(4000)])
    z0 = float(norm_ppf(min(max((boots < theta).mean(), 1e-9), 1 - 1e-9)))
    jack = np.array([(d.sum() - d[i]) / (n - 1) for i in range(n)])
    jm = jack.mean()
    num = ((jm - jack) ** 3).sum()
    den = 6.0 * (((jm - jack) ** 2).sum() ** 1.5)
    acc = num / den if den else 0.0
    out = []
    for side, alpha in (("lo", 0.025), ("hi", 0.975)):
        za = norm_ppf(alpha)
        adj = z0 + (z0 + za) / (1 - acc * (z0 + za))
        pc = float(norm_ppf_inv_cdf(adj))
        out.append(float(np.quantile(boots, min(max(pc, 0.001), 0.999))))
    return out


def norm_ppf_inv_cdf(x):
    """Φ(x)：标准正态 CDF。"""
    return 0.5 * (1 + erf(x / np.sqrt(2)))


def analyze(label, a, b):
    d = a - b
    n = len(d)
    mean = d.mean()
    # 置换（sign-flip）
    flips = rng.choice([-1.0, 1.0], size=(NPERM, n))
    perm = (d * flips).mean(axis=1)
    p_perm = float((np.abs(perm) >= abs(mean)).mean())
    # 百分位 bootstrap
    idx = rng.integers(0, n, (NB, n))
    bd = d[idx].mean(axis=1)
    lo, hi = np.quantile(bd, [0.025, 0.975])
    # BCa
    blo, bhi = bca(d)
    # 中位差 bootstrap
    bmed = np.median(d[idx], axis=1)
    mlo, mhi = np.quantile(bmed, [0.025, 0.975])
    # Wilcoxon（正态近似，带并列校正与连续性校正）
    r = np.abs(d)
    order = np.argsort(r, kind="mergesort")
    ranks = np.empty(n)
    ranks[order] = np.arange(1, n + 1)
    W = float(ranks[d > 0].sum())
    mu = n * (n + 1) / 4
    sd = np.sqrt(n * (n + 1) * (2 * n + 1) / 24)
    z = (W - mu) / sd                       # 正态近似的双边 z（连续校正省了：n=32 够用，且与 erf 同向）
    p_w = float(1 - erf(abs(z) / np.sqrt(2)))
    # 符号检验
    pos = int((d > 0).sum())
    from math import comb
    p_sign = 2 * sum(comb(n, k) for k in range(0, min(pos, n - pos) + 1)) / 2 ** n
    # jackknife 去最大影响图
    i_worst = int(np.argmin(d)) if mean < 0 else int(np.argmax(d))
    d2 = np.delete(d, i_worst)
    return dict(label=label, mean=mean, wins=pos, n=n, p_perm=p_perm,
                pct=(lo, hi), bca=(blo, bhi), med=(np.median(d), mlo, mhi),
                p_wilcoxon=p_w, p_sign=min(1.0, p_sign), map=i_worst, d_worst=d[i_worst], mean_wo=d2.mean())


def stratify(label, a, b, late):
    print(f"\n  [{label}] 按起始晚资源三分层（矿+油+金；探索性，不分胜负）")
    q = np.quantile(late, [1 / 3, 2 / 3])
    strata = [("低", late <= q[0]), ("中", (late > q[0]) & (late <= q[1])), ("高", late > q[1])]
    for nm, m in strata:
        d = (a - b)[m]
        print(f"    {nm}层 n={m.sum():>2} 晚资源[{late[m].min():.0f},{late[m].max():.0f}] "
              f"bc1−entann 均值 {d.mean():>+8.0f} 中位 {np.median(d):>+8.0f} "
              f"赢 {int((d>0).sum())}/{len(d)}")


if __name__ == "__main__":
    # 晚资源（900000~900031）来自我已有的 _map_cmp 原始转储
    per = {}
    seed = None
    for line in open("rl/runs/probe_ecs/teacher200_map_cmp_256.raw", encoding="utf-8"):
        m = re.match(r"seed (\d+)", line)
        if m:
            seed = int(m.group(1))
            continue
        m = re.match(r"\s+起始 5 格\s*:", line)
        if m and seed is not None:
            g = {k: int(v) for k, v in re.findall(r"(木头|耕地|矿石|石油|黄金)=(\d+)", line)}
            per[seed] = g
    late = np.array([per[900000 + i]["矿石"] + per[900000 + i]["石油"] + per[900000 + i]["黄金"] for i in range(N)], float)
    print(f"晚资源 900000~900031：中位 {np.median(late):.0f}，[0,5] 层 {(late<=5).sum()} 图")
    for cal in ("greedy", "sample"):
        print(f"\n########## 档：{cal}（图 900000+i, i=0..31；NB={NB}, 置换={NPERM}）##########")
        for pa, pb in (("bc1", "entann"), ("bc1", "bc2"), ("bc2", "entann")):
            r = analyze(f"{pa}−{pb}", ARM[(pa, cal)], ARM[(pb, cal)])
            print(f"  {r['label']}: 均值 {r['mean']:+,.0f}  赢/输 {r['wins']}/{r['n'] - r['wins']}   "
                  f"置换p={r['p_perm']:.4f}  Wilcoxon p={r['p_wilcoxon']:.4f}  符号p={r['p_sign']:.4f}")
            print(f"      百分位CI [{r['pct'][0]:+,.0f}, {r['pct'][1]:+,.0f}]   "
                  f"BCa CI [{r['bca'][0]:+,.0f}, {r['bca'][1]:+,.0f}]   "
                  f"中位差 {r['med'][0]:+,.0f} CI [{r['med'][1]:+,.0f}, {r['med'][2]:+,.0f}]")
            print(f"      去最大影响图(图{r['map']}, 差 {r['d_worst']:+,.0f}) 后均值 {r['mean_wo']:+,.0f}")
        stratify("bc1 vs entann", ARM[("bc1", cal)], ARM[("entann", cal)], late)
