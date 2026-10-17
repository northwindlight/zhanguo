"""本领域的声明式数值；兼容汇总层不拥有这些值。"""

                     # 军情只给粗略数量（各兵种几支），位置/血量不外泄
PLAN_MAX_TURNS = 10  # 国策每 N 回合必须修订一次（否则 end_turn 被拦）

EXTRA_PROMPT_TURNS = 20  # 开局临时注入上下文（extra_prompt）的有效回合数，之后只留 extra_summary 小结

SUMMARY_MIN_CHARS = 4  # end_turn 回合小结最短字数（太短会被拦下）


VALUES = {n: v for n, v in globals().copy().items() if n.isupper()}
