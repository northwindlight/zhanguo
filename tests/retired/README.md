# 旧单体测试档案

这些测试依赖已移除的 `World(size=..., nations=...)`、中文动作别名、旧提示词拼装器或退休规则 AI，因此不再作为当前测试入口。保留源码用于追溯；目录不设 `__init__.py`，标准 unittest discovery 不递归收集。

当前领域行为由根目录领域对象测试与 `test_world_domains.py` 检查；统一 API、客户端、CLI、存读档由 `test_player_api.py`、`test_player_clients.py` 检查。旧八国测试由新 `test_scenario_session.py` 替代。

旧基准与历史规则要在相应历史提交运行。当前不维护 v11plus 以前的规则 AI，也不维护旁支 v12。
