# Subplans

当前主计划是 Stage 3 Local Tool Execution，状态为 `planned / review-remediated`。
Stage 3 尚未开始实施；当前没有激活子计划。用户明确授权实施后，必须先运行主计划
Gate P0，只有当前宿主原生沙箱可行性门禁通过，才可激活 Subplan 29：

| 顺序 | 子计划 | 状态 |
|---|---|---|
| 29 | `29-stage3-policy-workspace-foundation.md` | pending |
| 30 | `30-stage3-read-search-tools.md` | pending |
| 31 | `31-stage3-file-mutation-diff.md` | pending |
| 32 | `32-stage3-host-process-execution.md` | pending |
| 33 | `33-stage3-native-sandbox.md` | pending |
| 34 | `34-stage3-git-and-acceptance.md` | pending |

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

`TODO.md` 只写当前活动子计划的可执行任务，不要堆整份主计划。计划完成但尚未获得
实施授权时，保持无活动任务。

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
