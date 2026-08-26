# Morrow 可改进项清单

> 用于记录当前项目已发现的缺陷、不足和潜在优化项。
> 本清单不是 Roadmap 或 TODO；记录不代表必须实施，也不代表已经承诺排期。

## 记录规则

- **重要程度**：`高` / `中` / `低`，表示该项对产品能力或使用体验的影响程度。
- **是否已解决**：`否` / `部分` / `是` / `不再处理`。
- 新增改进项时，在表格末尾追加一行并使用递增编号。
- 确定实施的项目应另行写入对应 Stage 文档或 `.agent/` 执行计划。

## 改进项

| 编号 | 改进项 | 重要程度 | 是否已解决 | 说明 |
|---|---|---|---|---|
| IMP-001 | 自动发现并分层加载项目级指令 | 高 | 否 | 当前尚未形成对 `AGENTS.md`、`CLAUDE.md` 或目录级规则文件的通用发现、继承和覆盖机制。 |
| IMP-002 | 长代码任务的自动上下文压缩 | 高 | 否 | 当前按 Turn、ToolCycle 和预算构造上下文，但没有结构化 LLM Compaction；长任务可能丢失早期决策、修改记录和剩余事项，或因达到预算而停止。 |
| IMP-003 | 执行过程中的用户 Steering | 中 | 否 | 缺少在 AgentRun 运行期间追加纠偏指令，并在模型调用或 ToolCycle 闭合后的安全点有序接纳的机制。 |
| IMP-004 | 完善复杂编码任务所需的受控工具工作流 | 高 | 否 | 现有读搜、文件修改、命令执行和只读 Git 已能支持基础代码任务，但包管理、Git 写操作、代码生成器和复杂构建链等场景仍可能受限；扩展时需要保持审批和沙箱边界。 |
| IMP-005 | 建立与 Pi 对照的复杂代码任务评测集 | 高 | 部分 | 已建立 [10 项轻量评测集](../evals/code-agent-mini/README.md)，包含 6 条仓库历史任务和 4 条固定外部任务；尚未使用同模型、同环境重复运行 Morrow 与 Pi 对照。 |
| IMP-006 | 提升显式计划和任务状态的可见性 | 中 | 否 | 当前主要依赖 ReAct 循环逐步决策；可评估增加非权威、可更新的目标、当前步骤、已完成事项和剩余事项投影，但达到 Pi 基线并不依赖内置 Plan Mode。 |

## 相关资料

- [Stage 5 真实 Provider 评估](acceptance/stage5-real-provider-evaluation.md)
- [Pi 使用方式与设计原则](https://pi.dev/docs/latest/usage)
- [Pi 上下文压缩](https://pi.dev/docs/latest/compaction)
- [Pi Session](https://pi.dev/docs/latest/sessions)
- [Pi Security](https://pi.dev/docs/latest/security)
