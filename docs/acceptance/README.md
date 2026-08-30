# Acceptance evidence index

本目录同时保存当前验收基线和已经完成的阶段/子计划执行记录。历史记录用于解释当时的决策与
验证结果，不是当前实现规范，也不能覆盖代码、当前 README、ARCHITECTURE、ROADMAP 或当前阶段文档。

## Current baseline

- [`current-chain-feasibility.md`](current-chain-feasibility.md)：清理旧版兼容后的正式公开链路可行性、能力清单、问题与缺口。
- [`stage5-acceptance.md`](stage5-acceptance.md)：当前通用 Preference、Learning 与 Memory 离线验收入口。
- [`stage6-skills-and-extensions.md`](stage6-skills-and-extensions.md)：当前 Skill、Provider/Model、MCP、Doctor 与完整 Backup 验收入口。
- [`stage7-direct-agent-baseline.md`](stage7-direct-agent-baseline.md)：当前 direct-agent 可靠性基线状态。
- [`s7p-10-stage7-entry-review.md`](s7p-10-stage7-entry-review.md)：Stage 7 进入审查、CONDITIONAL GO 边界与升级条件。
- [`s7p-10-go-upgrade-proof.md`](s7p-10-go-upgrade-proof.md)：当前代码困难任务的定向升级证明及未通过原因。

## Historical evidence

其余 `stage-*`、`s7p-*`、Provider 评测和配置专项报告按生成时状态保留。它们可能记录后来已经
移除的字段、兼容读取器、临时命令、旧 Backup 形状、阶段性测试计数或实验模型结果；引用这些记录时
必须同时核对上面的 current baseline。为避免重新引入维护负担，不应从历史报告恢复已退役合同。
