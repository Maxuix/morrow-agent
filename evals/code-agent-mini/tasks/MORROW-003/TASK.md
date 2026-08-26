# 恢复 Session 的完整 Recovery 工作流

真实重启场景中，Session 恢复接口、命令服务和持久化对象可能各自构造不同的 Recovery Service，导致 `/recovery ack`、`/recovery resume` 与后续 AgentRun 不能共享同一份恢复状态。另外，独立 CLI 强制要求调用方提供 command ID，普通用户难以正确使用。

请恢复这条端到端工作流：

- 同一个 Session Application 中的 API、命令服务和持久化层共享同一个 Recovery Service；
- `/recovery` 能发现并显示未闭合执行，支持 acknowledge 后继续处理剩余项目；
- resume 决策解析报告、恢复 Session 健康状态，并准备可继续执行的 AgentRun；
- `recovery resolve` 的 command ID 对普通用户可选，省略时自动生成；显式提供时仍可作为幂等键复用；
- CLI 帮助应说明 resume 本身不会调用 Provider；
- 保持 ConversationLog、ToolCycle 和持久化所有权边界不变。

使用脚本化 Provider 和临时数据目录完成离线验证。
