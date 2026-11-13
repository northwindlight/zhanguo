# 股市后端

使用 [PAMS: Platform for Artificial Market Simulations](https://github.com/masanorihirano/pams)，固定 PyPI 版本 `0.3.0`。
作者：Masanori Hirano、Ryosuke Takata、Kiyoshi Izumi。项目许可证为 EPL-1.0，见其[原始 LICENSE](https://github.com/masanorihirano/pams/blob/main/LICENSE)。
通过可选依赖导入原包，没有复制或修改第三方源文件。`backend.py` 对接其 Market/Order/Cancel 和匹配日志；
PAMS 的默认统一清算价格语义在本 mod 中保留，券商资金、借券与资源收费由本项目独立实现。

[官方说明](https://pams.hirano.dev/en/latest/user_guide/about.html)；
[论文](https://arxiv.org/abs/2309.10729)：PAMS: Platform for Artificial Market Simulations。
