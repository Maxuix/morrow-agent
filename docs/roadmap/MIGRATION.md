# 2026-08-18 路线迁移说明

本次修订保留已完成的 Stage 1–2，重写 Stage 3，并把旧 Stage 4–7 拆分为新的 Stage 4–10。

## 文件迁移

| 旧路线文件 | 新路线文件 |
|---|---|
| `stage-4-sessions-context-and-memory.md` | `stage-4-task-session-and-persistence.md` + `stage-5-reviewable-learning-and-memory.md` |
| `stage-5-skills-and-extensions.md` | `stage-6-skills-and-extensions.md` |
| `stage-6-automation-and-complex-tasks.md` | `stage-7-workflow-runtime.md` + `stage-8-adaptive-orchestration-and-gui.md` + `stage-9-background-automation.md` |
| `stage-7-experience-and-channels.md` | `stage-8-adaptive-orchestration-and-gui.md` + `stage-10-productization-and-1.0.md` |

旧文件名在本包中保留为兼容入口，只解释迁移，不再承担路线权威。

## 为什么拆分

1. 先持久化任务事实，再决定哪些内容值得学习。
2. 先建立可测试、可回滚的 Skill 生命周期，再允许自动生成 Draft。
3. 先运行静态、经编译的 Workflow，再加入自动编排与 GUI 编辑。
4. 先证明前台 Workflow 的正确性，再让它后台和定时运行。
5. GUI 先作为统一 Core 的控制面，安装、升级、迁移与发布在最后收口。

## 应用到仓库

1. 覆盖 `docs/ROADMAP.md`。
2. 整体覆盖 `docs/roadmap/`，保留本包中的兼容入口文件。
3. 检查旧文档、README 或研究文档中的链接；兼容入口会继续指向新路线。
4. 不要把未来架构模型直接写入当前 `docs/ARCHITECTURE.md`；实际代码发生变化后再更新当前架构基线。
5. 仅在用户显式激活下一个 Stage 3 实现切片时，才新建或更新 `.agent/PLAN.md`；本次路线迁移不重开
   已完成的配置工具计划，也不把临时任务写进路线文件。
