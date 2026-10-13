"""添加一种资源。

资源用**类**注册：继承 `Resource`，给出命名空间 ID 与展示名键，
再从 `content_definitions()` 返回 `{ID: 类}`。资源域会枚举内容表里
所有 `Resource` 子类，自动为它建立账户、库存、只读接口与存档，
无需改动 `mods/native/resources/`。

注册只让这种资源存在；要能在市场上买卖，还要把它列进某份市场声明
（见同目录 market.toml）。
"""
from engine.resource import Resource
from engine.runtime import GameMod


class Tea(Resource):
    TYPE_ID = 'example:tea'
    # 展示名由本地化文件提供，内容定义里只存键。
    NAME_KEY = 'resource.example:tea.name'
    # TRADEABLE 为 False 时市场拒绝上市该资源，账户与存档不受影响。


class TeaMod(GameMod):
    def mixins(self):
        return ()

    def content_definitions(self):
        return {Tea.TYPE_ID: Tea}
