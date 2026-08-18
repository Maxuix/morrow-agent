# Subplans

Stage 3 Local Tool Execution 已关闭，状态为 `completed / review-remediated`（当前声明平台为
macOS）。Gate P0、最终验收和 2026-08-19 的 Mimo/provider 最终复核均通过，Subplans 29–34
已完成；Linux 仍需真实 runner 后才能声明支持。Stage 4 已进入规划，但当前没有活动子计划：

| 顺序 | 子计划 | 状态 |
|---|---|---|
| 29 | `29-stage3-policy-workspace-foundation.md` | completed |
| 30 | `30-stage3-read-search-tools.md` | completed |
| 31 | `31-stage3-file-mutation-diff.md` | completed |
| 32 | `32-stage3-host-process-execution.md` | completed |
| 33 | `33-stage3-native-sandbox.md` | completed |
| 34 | `34-stage3-git-and-acceptance.md` | completed; implementation review remediated |

已完成的 Natural-Language Configuration Tooling Subplans 25–28 由 commit `3772222`
及后续 Git 历史保存，不再保留在活动目录。更早完成的计划同样以 Git 历史为准。

将过大的主计划拆分为按顺序执行的子计划，并将子计划文件放在此目录。

`PLAN.md` 是活文档，只做当前主计划的高层索引：总体目标、子计划列表、依赖、
完成状态、当前活动子计划。不要在活动 `PLAN.md` 里保留过期正文；旧版本看 Git 历史。
计划和仓库实测冲突时，先改计划再继续实现。

本目录只保留当前活动主计划中未完成或正在执行的子计划。写下一份带新子计划的
主计划之前，从工作树移除已被新阶段取代的旧子计划。

每个子计划应至少说明：

- 目标和范围
- 前置依赖
- 可执行任务
- 完成标准
- 交付结果

`TODO.md` 只写当前活动子计划的可执行任务，不要堆整份主计划。计划完成或下一阶段只有路线入口
但尚未锁定实施范围时，保持无活动任务。Stage 4 的持久化、恢复和 Full Access 实现必须先形成
一个明确子计划并通过对应门禁。

一次只激活和执行一个子计划。除非确有必要，不要创建嵌套子计划。

子计划开始前：

1. 获得用户对实施或对应子计划的明确授权
2. 首次启动 Stage 3 时先通过主计划 Gate P0
3. 在 `PLAN.md` 和本文件中把该子计划标记为 active
4. 将该子计划的任务复制到 `TODO.md`
5. 更新 `TRACKER.md` 的活动任务与下一步

子计划完成后：

1. 验证完成标准
2. 在 `PLAN.md` 中标记完成
3. 把结果记入 `LOG.md`
4. 激活下一个子计划
5. 更新 `TODO.md` 和 `TRACKER.md`
