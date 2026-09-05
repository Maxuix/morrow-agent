# 当前主计划子计划：Chat 工作台补全

> 主计划 ID：stage8-chat-workbench；建立日期：2026-09-05。
> 实施状态：0/8 完成，当前无激活实施子计划；下一步为子计划 1。

[主计划](../PLAN.md)持有目标、技术决定和最终验收。每个子计划持有其任务与局部出口。

| 顺序 | 子计划 | 依赖 | 状态 |
|---|---|---|---|
| 1 | [交互合同与能力映射](1-interaction-contracts-and-parity.md) | 主计划 | [ ] 待开始 |
| 2 | [Core Chat 执行与实时流](2-core-chat-runtime-and-stream.md) | 1 | [ ] 待开始 |
| 3 | [中心 Chat 与 Session 入口](3-chat-workspace-and-session-entry.md) | 2 | [ ] 待开始 |
| 4 | [工作区与 Session 完整管理](4-workspace-and-session-management.md) | 3 | [ ] 待开始 |
| 5 | [模型、思考与权限设置](5-model-reasoning-and-permissions.md) | 4 | [ ] 待开始 |
| 6 | [附件与文件上下文](6-attachments-and-file-context.md) | 5 | [ ] 待开始 |
| 7 | [Workflow 融合与 CLI 功能闭环](7-workflow-and-cli-parity.md) | 6 | [ ] 待开始 |
| 8 | [迁移、全场景验收与交付](8-migration-and-product-acceptance.md) | 1–7 | [ ] 待开始 |

## 激活与关闭

- 用户本轮只要求生成完整 PLAN，故没有实施任务标为进行中。
- 后续授权执行本计划后，从下一子计划开始；授权整个计划时可依次推进，不逐片重复确认。
- 同时只激活一个子计划；将它的任务复制到 TODO，当前一项标 [>]，TRACKER 记录下一动作。
- 先验证子计划依赖已提交并集成，再从最新已验证 main 建对应分支。
- 相关验证成功后标 [x]；关闭前完成提交、执行状态更新、ff-only 集成与 topic/worktree 清理。
- TODO 只保留当前激活子计划的任务。LOG 不记录常规读取和搜索。
- 依赖、公共生命周期或 bundled 默认值存在未覆盖授权时，仅处理具体缺口和独立工作。
- 不凭旧测试数字、占位控件或仅有观察状态宣称新功能完成。

## 目录生命周期

- 此目录只能包含本 README 和当前主计划的 1–8 子计划。
- 每个新主计划独立从 1 编号；先归档旧文件，再更换 PLAN。
- 原 Stage 8 的 13 个子计划已完整移动到
  [stage8-adaptive-orchestration-gui](../archive/subplans/stage8-adaptive-orchestration-gui/README.md)，
  保留原编号与内容，原主计划及索引作为归档快照保存。
- 原 Stage 7 及其他历史归档保持不变；归档文件仅供恢复和追溯。
- 本计划完成不自动开启 Stage 9。
