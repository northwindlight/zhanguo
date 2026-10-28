# 离线存档夹具

`v063-native.json.gz` 与 `v063-experimental.json.gz` 来自提交 `25287c8` 的正常合成双国开局（12×12、种子73，一位真人、一位规则AI）。模块声明与已发布v0.6.3相同。未使用真实存档或模型账户。

用于验证已知旧清单的显式副本升级；包含完整旧依赖清单，不能改成当前清单来让测试通过。native清单SHA256为 `9c67856fdf79f15382d80802ba1f116196105a7eaec384b71ffef6296cf57f07`，experimental为 `6ee31c429fcea96b5da5cb0976da96993c8c0deb3ee383e37ef14e87eb5bb7a1`。哈希输入为JSON排序键、紧凑分隔符、不转义中文后的UTF-8。

`v07-production-*.json.gz`来自生产队列提交`230e97d`（主线文档提交19861ee）合成局，保留未开工建筑与未生成军队的实际预付款，验证新增军令字段时不丢失既有队列。native清单SHA256为`120bd26f256c0fe2be1f62796d306858ee614f85ad309d20a92b162837bf2161`，experimental为`9bd568a9de45c017fe557b1653a1c439f8e75a89e595f5df882521b88e878519`。
