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
| IMP-001 | 自动发现并分层加载项目级指令 | 高 | 部分 | 已实现 workspace 根目录的 availability-first 单文件加载，优先级 `AGENTS.override.md` > `AGENTS.md` > `CLAUDE.md`，过大、异常格式或读取失败告警跳过；目录级/嵌套的分层发现与继承仍未实现（当前明确只在根目录发现，任务文本不触发局部扫描）。 |
| IMP-002 | 长代码任务的自动上下文压缩 | 高 | 是 | Long-horizon v2 运行已实现结构化压缩（`pi_compaction` checkpoint、Pi token 阈值与 256 KiB 保守字符兜底），`compaction_enabled` 默认开启；新配置型 Provider 默认选择 v2，已知 context/output 能力驱动 token 阈值与 reserve，v1 快照保持原有有界语义。 |
| IMP-003 | 执行过程中的用户 Steering | 中 | 是 | 已实现持久化运行时控制队列（v22）：Enter 在 Agent 运行中提交 steering，于循环顶部、完整工具批次后与最终 STOP 提交前三个安全点以 `STEERED` 终态闭合当前 Turn 并持久化新 Turn；Alt+Enter follow-up 在正常 STOP 后按 FIFO 消费；每 Session 上限 32 条、单条 4096 字符，已接纳的工具批次不受影响。 |
| IMP-004 | 完善复杂编码任务所需的受控工具工作流 | 高 | 否 | 现有读搜、文件修改、命令执行和只读 Git 已能支持基础代码任务，但包管理、Git 写操作、代码生成器和复杂构建链等场景仍可能受限；扩展时需要保持审批和沙箱边界。 |
| IMP-005 | 建立与 Pi 对照的复杂代码任务评测集 | 高 | 部分 | 已建立 [10 项轻量评测集](../evals/code-agent-mini/README.md)，包含 6 条仓库历史任务和 4 条固定外部任务；重复 Morrow/Pi 对照 harness（冻结计划、准入与容量守卫、`reduced-single-repetition-v1` 缩减变体）已实现，但正式评估准入暂因 Token 容量上限阻塞，尚未产生重复对照结果。 |
| IMP-006 | 提升显式计划和任务状态的可见性 | 中 | 否 | 当前主要依赖 ReAct 循环逐步决策；可评估增加非权威、可更新的目标、当前步骤、已完成事项和剩余事项投影，但达到 Pi 基线并不依赖内置 Plan Mode。 |

## 相关资料

- [Stage 5 真实 Provider 评估](acceptance/stage5-real-provider-evaluation.md)
- [Pi 使用方式与设计原则](https://pi.dev/docs/latest/usage)
- [Pi 上下文压缩](https://pi.dev/docs/latest/compaction)
- [Pi Session](https://pi.dev/docs/latest/sessions)
- [Pi Security](https://pi.dev/docs/latest/security)
