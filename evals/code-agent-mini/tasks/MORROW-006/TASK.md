# 增加安全的分层 Runtime Policy

当前运行预算和 Review 超时默认值分散在代码与旧资源中，用户也缺少一个受控的覆盖入口。需要建立随包默认策略与用户配置覆盖，但绝不能让配置文件放宽安全所有的边界。

请实现分层 Runtime Policy：

- 随包提供只读默认策略，覆盖 AgentRun、Learning Review 和 Preference Review 的可调预算；
- 可选的用户 YAML 只覆盖明确声明的字段，未声明字段继续使用默认值；
- 未知字段、错误类型、非有限数字、越过硬上限或非法字段组合使整个配置拒绝加载，不允许部分应用；
- 权限、审批、循环检测开关、密钥/路径过滤、Schema/Payload/Storage 预算和重试语义不得由 YAML 放宽；
- 普通配置写入必须保留已有 Runtime Policy 覆盖；
- AgentRun 和 Review 在开始时解析并冻结实际策略；
- 保持现有公开停止原因和生命周期兼容。

完成后运行 Runtime Policy 及配置持久化测试。
