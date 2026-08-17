# Subplans

当前主计划是 Natural-Language Configuration Tooling，状态为 in progress、review-remediated；
当前激活 Subplan 25：

| 顺序 | 子计划 | 状态 |
|---|---|---|
| 25 | `25-generic-tool-policy-approval.md` | in progress |
| 26 | `26-configuration-service-tool.md` | pending |
| 27 | `27-configuration-single-chain-integration.md` | pending |
| 28 | `28-configuration-tooling-acceptance.md` | pending |

最近完成的 Handoff Removal Refactor Subplans 21–24 已由 commit `cbc3d6d` 保存，不再保留在活动目录。
已完成的 Stage 2 Subplans 17–20 保留在 commit `831c4ea` 的历史中。

将过大的主计划拆分为按顺序执行的子计划，并将子计划文件放在此目录。

`PLAN.md` 是活文档，只做当前主计划的高层索引：总体目标、子计划列表、依赖、完成状态、当前活动子计划。不要在活动 `PLAN.md` 里保留过期正文；旧版本看 Git 历史。计划和仓库实测冲突时，先改计划再继续实现。

本目录只保留当前活动主计划中未完成或正在执行的子计划。写下一份带新子计划的主计划之前，从工作树移除已被新阶段取代的旧子计划。

每个子计划应至少说明：

- 目标和范围
- 前置依赖
- 可执行任务
- 完成标准
- 交付结果

`TODO.md` 只写当前活动子计划的可执行任务，不要堆整份主计划。

一次只激活和执行一个子计划。除非确有必要，不要创建嵌套子计划。

子计划完成后：

1. 验证完成标准
2. 在 `PLAN.md` 中标记完成
3. 把结果记入 `LOG.md`
4. 激活下一个子计划
5. 更新 `TODO.md` 和 `TRACKER.md`
