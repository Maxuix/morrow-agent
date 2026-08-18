# 兼容入口：旧 Stage 6 已拆分

> 状态：已被 2026-08-18 修订路线取代，不再作为路线权威维护。

旧版“自动化与复杂任务”把 Multi-Agent 与后台调度放在同一阶段，现拆分为：

1. [Stage 7：Agent Definition 与静态 Workflow Runtime](stage-7-workflow-runtime.md)
2. [Stage 8：自适应编排与 GUI 控制面](stage-8-adaptive-orchestration-and-gui.md)
3. [Stage 9：后台任务与可靠自动化](stage-9-background-automation.md)

拆分原因：先证明前台、静态、可观察的 Workflow 正确，再允许自动生成和无人值守运行。
