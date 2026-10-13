# 地图色盘实验

`python experiments/map_render/palette_search.py` 搜索国土色与四色兵牌之间的色差，仅使用标准库。

当前游戏地图由 [UI Mod](../../mods/ui/README.md) 渲染；`mods/ui/maps.py` 消费标准玩家观察，不读取 World。依赖旧 render / mp_ai 的渲染实验已退役，源码和旧结果见 [历史提交](https://github.com/northwindlight/zhanguo/tree/72c595c/experiments/map_render)。
