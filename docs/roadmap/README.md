# Morrow Stage Roadmap 索引

本目录只保存稳定的 Stage 合同，不保存当前实现 TODO。

## 当前路线

1. [Stage 1：方向确定与可运行原型](stage-1-direction-and-prototype.md)
2. [Stage 2：Agent 核心能力](stage-2-agent-core.md)
3. [Stage 3：本地 Code Agent 与安全闭环](stage-3-local-tools-and-safety.md)
4. [Stage 4：Task、Session、Artifact 与持久化](stage-4-task-session-and-persistence.md)
5. [Stage 5：可审查学习与长期记忆](stage-5-reviewable-learning-and-memory.md)
6. [Stage 6：Skills 与扩展生命周期](stage-6-skills-and-extensions.md)
7. [Stage 7：Agent Definition 与静态 Workflow Runtime](stage-7-workflow-runtime.md)
8. [Stage 8：自适应编排与 GUI 控制面](stage-8-adaptive-orchestration-and-gui.md)
9. [Stage 9：后台任务与可靠自动化](stage-9-background-automation.md)
10. [Stage 10：产品化与 Morrow 1.0](stage-10-productization-and-1.0.md)

## 使用规则

- `docs/ROADMAP.md` 只维护总体方向、阶段顺序、状态和入口。
- 每个 Stage 文档维护目标、边界、架构合同、实施切片和完成门禁。
- 当前执行方案写入 `.agent/PLAN.md`；当前活跃任务只写入 `.agent/TODO.md`。
- 不为每个 Stage 再建立更深的长期路线层级；大型实现只在 `.agent/subplans/` 中拆分。
- Stage 1–2 是历史基线；Stage 3 是当前阶段；Stage 4–10 在激活时根据实际代码再次评审。

## 旧文件迁移

旧路线中的 Stage 4–7 被重新拆分：

| 旧文件 | 新归属 |
|---|---|
| `stage-4-sessions-context-and-memory.md` | Stage 4 的运行持久化 + Stage 5 的学习/记忆 |
| `stage-5-skills-and-extensions.md` | Stage 6 |
| `stage-6-automation-and-complex-tasks.md` | Stage 7 的 Workflow + Stage 9 的后台自动化 |
| `stage-7-experience-and-channels.md` | Stage 8 的 GUI + Stage 10 的产品化 |

兼容入口文件只用于解释迁移，不再作为路线权威。
