"""Read-only terminal dashboard for the full-turn PPO checkpoint mirror."""
import argparse
import curses
from datetime import datetime
import json
import math
from pathlib import Path
from statistics import mean, median
import time
import unicodedata
from zoneinfo import ZoneInfo


ZONE = ZoneInfo("Asia/Shanghai")
BATCH = 4  # Production updates after four games; numbering includes the pre-migration games.
PAGES = ("总览", "四局与席位", "PPO 参数", "近期批次")


def load(path):
    return json.loads(path.read_text())


def stamp(t):
    return datetime.fromtimestamp(t, ZONE).strftime("%m-%d %H:%M:%S")


def duration(seconds):
    minutes, seconds = divmod(max(0, round(seconds)), 60)
    return f"{minutes}分{seconds:02d}秒"


def number(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def stats(games):
    seats = [s for g in games for s in g["seats"]]
    turns = sum(g["seat_turns"] for g in games)
    return dict(gdp=mean(g["final_nominal_gdp"] for g in games),
                avg=mean(g["nominal_gdp"] for g in games), land=mean(s["land"] for s in seats),
                expand=sum(s["land"] > s["initial_land"] for s in seats), seats=len(seats),
                black=100 * sum(s["blackouts"] for s in seats) / turns,
                short=100 * sum(s["shortage"] for s in seats) / turns,
                recruits=sum(s["recruits"] for s in seats), attacks=sum(s["attacks"] for s in seats),
                starved=sum(s["starved"] for s in seats), turns=turns)


def snapshot(root):
    current = load(root / "verified.json")
    n = current["games"]
    work = root / "work" if (root / "work").is_dir() else root
    path = work / f"games-{n:04d}.json"
    # rsync -a retains producer completion time. verified.json is published after transfer.
    saved = path.stat().st_mtime
    previous = []
    for end in range(n - BATCH, max(n - 8 * BATCH, 0), -BATCH):
        p = work / f"games-{end:04d}.json"
        if not p.is_file():
            break
        row = load(p)
        if row["initial_sha256"] != current["initial_sha256"] or row["games"] != end:
            break
        previous.append(row)
    live_path = root / "live.json"
    live = load(live_path) if live_path.exists() else {}
    return [current, *previous], saved, live


def header(rows, saved, live, now):
    row = rows[0]
    n, goal = row["games"], live.get("goal", 2000)
    size = min(BATCH, max(0, goal - n))
    batch = math.ceil(n / BATCH)
    first = n - len(row["metrics"]) + 1
    top = [f"战国 PPO   {stamp(now)} 北京时间", f"已存第 {batch} 批   第 {first}–{n} 局   累计 {n}/{goal} 局"]
    if not size:
        return top + ["训练已达到目标"]
    target = n + size
    label = f"第 {batch + 1} 批 · {n + 1}–{target} 局"
    recent = live.get("recent", {})
    coverage = recent.get("ppo_coverage", {})
    in_update = coverage.get("games") == target
    if live.get("state") == "complete":
        return top + ["训练已完成"]
    if live.get("state") == "stopped":
        return top + [label + "   训练已停止，暂无预计时间"]
    if live and now - live.get("time", 0) > 120:
        return top + [label + "   训练状态同步延迟，预计时间待刷新"]
    if recent.get("ppo_batch_complete", {}).get("games", 0) >= target:
        return top + [label + "   训练机已存盘，等待本地校验"]
    active_limit = coverage.get("thought_limit", row["thought_limit"]) if in_update else row["thought_limit"]
    thought_disabled = active_limit == 0
    if in_update:
        phase = "PPO 更新"
    else:
        phase = "采样中" if live.get("state") == "running" else "按历史耗时估计"
    if thought_disabled:
        phase += " · 思考关闭"
    hist = [r for r in rows if (r["thought_limit"] == 0) == thought_disabled][:5]
    if not hist:
        return top + [f"{label}   {phase}", "首批零思考完成后开始估算耗时"]
    if in_update:
        seconds = coverage["sample_seconds"] + size * median(r["update_seconds"] / len(r["metrics"]) for r in hist)
    else:
        seconds = size * median((r["sample_seconds"] + r["update_seconds"]) / len(r["metrics"]) for r in hist)
    eta = saved + seconds
    remaining = eta - now
    countdown = "剩 " + duration(remaining) if remaining >= 0 else "已过预计 " + duration(-remaining)
    return top + [f"{label}   {phase}", f"预计完成 {stamp(eta)}   {countdown}   参考近 {len(hist)} 批"]


def overview(row):
    games = row["metrics"]
    a = stats(games)
    s, u = row["sample_seconds"], row["update_seconds"]
    game = sum(g["game_decisions"] for g in games)
    thoughts = sum(g["thoughts"] for g in games)
    stops = sum(g["forced_stops"] for g in games)
    thought = f"思考上限  {row['thought_limit']}    触顶 {stops}/{a['turns']}" if row["thought_limit"] else "思考关闭"
    accepted = sum(epoch["accepted"] for epoch in row["ppo"])
    game_rows = []
    for i, g in enumerate(games, row["games"] - len(games) + 1):
        v = stats([g])
        game_rows.append(f"  {i:>4} 局      {g['final_nominal_gdp']:7.2f}    {g['land']:7.2f}    {v['black']:5.1f}%    {v['short']:4.1f}%")
    return [
        f"期末名义 GDP  {a['gdp']:.2f}     近16局  {number(row['rolling_final_gdp'])}     全程均值  {a['avg']:.2f}",
        f"期末土地  {a['land']:.2f} 块/国     扩张  {a['expand']}/{a['seats']} 席",
        f"断电  {a['black']:.1f}%     军队断供  {a['short']:.1f}%    （按席位回合）",
        f"征兵  {a['recruits']}     进攻  {a['attacks']}     饿亡  {a['starved']}",
        "", "各局表现       期末GDP    土地/国     断电      断供",
        *game_rows,
        "", f"采样  {duration(s)}    PPO 更新  {duration(u)}    {len(games)*3600/(s+u):.1f} 局/小时",
        f"采样  {(game+thoughts)/s:.0f} 决策/秒    游戏 {game:,}    思考 {thoughts:,}",
        f"{thought}    地皮奖励权重 {row['land_reward_weight']:.2f}",
        f"PPO 接受  {accepted}/{len(row['ppo'])} 轮",
    ]


def seats_view(row):
    lines = []
    games = row["metrics"]
    for i, g in enumerate(games, row["games"] - len(games) + 1):
        lines.append(f"第 {i} 局   种子 {g['seed']}   {g['turns']} 回合   期末 GDP {g['final_nominal_gdp']:.2f}")
        lines.append(" 席   名义GDP   固定价GDP    土地   断电   断供   征兵   进攻   饿亡   无效")
        for s in g["seats"]:
            lines.append(f" {s['seat']+1:>2}   {s['nominal_gdp']:7.2f}   {s['fixed_gdp']:7.2f}    {s['land']:4}   {s['blackouts']:4}   {s['shortage']:4}   {s['recruits']:4}   {s['attacks']:4}   {s['starved']:4}   {s['invalid']:4}")
        lines.append("")
    return lines


def optimization(row):
    c = row["contract"]
    reward = c["land_reward_schedule"]
    thought = f"思考 {c['thought_budget'][0]}–{row['thought_limit']} 步" if row["thought_limit"] else "思考关闭"
    lines = [f"{c['size']}×{c['size']} 地图   {c['seats']} 席   {c['turns']} 回合   {c['precision'].upper()}",
             f"全回合更新   γ {c['gamma']:g}   λ {c['gae_lambda']:g}   回报缩放 {c['reward_scale']}",
             f"{thought}   每窗口最多 {c['window_games']} 个游戏动作",
             f"地皮奖励权重 {row['land_reward_weight']:.2f}   {reward['window_games']} 局 GDP 门槛 {reward['threshold']:g}",
             f"退坡触发局数 {row['land_reward']['triggered_at'] or '未触发'}    退坡时长 {reward['fade_games']} 局"]
    for i, e in enumerate(row["ppo"], 1):
        lr = e["accepted_lr"]
        lines.extend(["", f"第 {i} 轮   {'接受' if e['accepted'] else '拒绝'}   学习率 {f'{lr:.2g}' if lr is not None else '—'}   回退 {e['backtracks']} 次",
                      f"KL    游戏 {e['game_kl']:.6f}    思考 {e['thought_kl']:.6f}",
                      f"损失  策略 {e['actor']:.5f}    价值 {e['critic']:.5f}",
                      f"梯度范数  策略 {e['actor_norm']:.4f}    价值 {e['critic_norm']:.4f}",
                      f"覆盖  {e['trained_turns']} 席位回合   {e['trained_game_actions']:,} 游戏   {e['trained_thoughts']:,} 思考"])
        if i == 1:
            lines.append(f"回放误差  logp {e['replay_logp_error']:.2g}   V {e['replay_value_error']:.2g}   逐步重算 {e['reference_windows']} 窗口")
    return lines


def history(rows):
    lines = ["批号    截至局    期末GDP     16局GDP    土地/国    断电     断供"]
    for r in rows:
        a = stats(r["metrics"])
        lines.append(f"{math.ceil(r['games']/BATCH):4}    {r['games']:6}    {a['gdp']:7.2f}    {number(r['rolling_final_gdp']):>8}    {a['land']:7.2f}    {a['black']:4.1f}%    {a['short']:4.1f}%")
    return lines


def fit(text, width):
    used = 0
    for i, ch in enumerate(text):
        used += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used > width:
            return text[:i]
    return text


def dashboard(screen, root):
    curses.curs_set(0)
    screen.timeout(5000)
    page = offset = 0
    while True:
        height, width = screen.getmaxyx()
        try:
            rows, saved, live = snapshot(root)
            top = header(rows, saved, live, time.time())
            body = (overview, seats_view, optimization, history)[page](rows if page == 3 else rows[0])
            sync = stamp(live["time"]) if live.get("time") else stamp(saved)
            tabs = "   ".join(f"[{i}] {s}" if i-1 == page else f"{i} {s}" for i, s in enumerate(PAGES, 1))
            top += [tabs]
        except (OSError, ValueError, KeyError) as error:
            top, body, sync = ["战国 PPO   等待训练报告"], [str(error)], "—"
        room = max(0, height - len(top) - 1)
        offset = min(offset, max(0, len(body) - room))
        screen.erase()
        for i, line in enumerate((top + body[offset:offset + room])[:max(0, height-1)]):
            screen.addstr(i, 0, fit(line, width-1), curses.A_BOLD if i < len(top) else curses.A_NORMAL)
        scroll = "  ↑↓滚动" if len(body) > room else ""
        foot = f"同步 {sync}   5秒刷新   1–4 切页{scroll}   Ctrl-b d 分离"
        screen.addstr(height-1, 0, fit(foot, width-1), curses.A_REVERSE)
        screen.refresh()
        key = screen.getch()
        if ord("1") <= key <= ord("4"):
            page, offset = key - ord("1"), 0
        elif key in (curses.KEY_DOWN, ord("j")):
            offset += 1
        elif key in (curses.KEY_UP, ord("k")):
            offset = max(0, offset-1)
        elif key == curses.KEY_NPAGE:
            offset += max(1, room)
        elif key == curses.KEY_PPAGE:
            offset = max(0, offset-max(1, room))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mirror", type=Path, help="directory containing verified.json")
    args = parser.parse_args()
    curses.wrapper(dashboard, args.mirror)
