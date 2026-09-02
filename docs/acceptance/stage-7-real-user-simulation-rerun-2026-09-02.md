# Stage 7 真实用户模拟复测 — 2026-09-02

## 结论

**未完全通过，不建议按“Stage 7 所有用户目标均可发布”验收。**

本次在全新隔离状态中重新执行此前受阻的管理、编译、真实 Provider、正则搜索、typed Artifact、
持久化查询和状态恢复路径。原四项直接阻塞问题均已解除，但发现一个新的 P1 缺陷：一旦执行真实
Workflow，`state doctor` 会把合法的节点有效请求预算误判为 AgentDefinition 完整性损坏，继而使
`state backup` fail closed。结果计数：**PASS 13、FAIL 2、BLOCKED 0、NOT RUN 3、
INCONCLUSIVE 0**。

## 测试基础

- Revision：`5b2d61880f67b5b417cc51355417e127018b0a97`
- 初始 Git 状态：clean；评测结束时除本报告外无项目文件变化
- 平台：Darwin 25.6.0 arm64
- 日期与时区：2026-09-02，Asia/Shanghai
- 入口：公开 `morrow agent`、`morrow workflow`、`morrow session`、`morrow task`、
  `morrow state` CLI
- 隔离状态：`/private/tmp/morrow-stage7-rerun-jzh5MK`
- Workspace：`ws_MWcRamTZvErY0xv1`
- Provider：已授权并实际使用 `opencode-go/deepseek-v4-flash`
- Provider 内容边界：仅发送 README 派生的只读研究任务；未发送凭据或原始 Provider payload
- 自动化旁证：`uv run pytest -q tests/test_stage7_*.py` → `233 passed in 32.58s`

非交互 REPL 第一次启动在沙箱内无法读取 macOS Keychain，但工作空间登记成功；真实 Provider 命令
经用户已授权的外部执行边界运行。该宿主限制未计为产品缺陷，也没有阻塞实际 Live 场景。

## 用户能力清单

| 用户表面 / 能力 | 实现依据 | 可达状态 / 模式 | 场景 | 覆盖 |
|---|---|---|---|---|
| Agent 定义管理 | `README.md` 静态 Workflow；`morrow agent --help` | list/show/create/edit/validate/publish/enable/disable/revoke | S01–S04、S10–S12 | PASS |
| Workflow 定义管理 | `README.md`；`morrow workflow --help` | 四模板、用户源、编译诊断、发布 Head/OCC、启停/撤销 | S01–S04、S10–S12 | PASS |
| 前台 Workflow 运行 | `workflow run/status/runs/node show` | completed/failed、精确 Revision、早期 Run ID、节点查询 | S05–S09、S13 | 部分通过 |
| typed Artifact 交接 | `EvidenceBundle`、`SynthesisReport` 输出合同 | 必需槽、重试、四节点 fan-out/fan-in | S06–S08 | PASS |
| 本地非字面正则搜索 | Explorer 的公开 `grep` 工具 | 成功、非法路径拒绝 | S05、S06 | PASS |
| 状态控制 | disable/enable/revoke/替代版本 | enabled/disabled/revoked/replaced | S04、S11、S12 | PASS |
| 运行恢复管理 | runs/status/resume/abandon | durable failed/completed；terminal no-op | S07、S13、S14 | PASS（未制造 crash-blocked） |
| Doctor 与 Workflow 备份 | README 状态与恢复边界 | healthy/needs_repair、backup fail closed | S15、S16 | FAIL |
| Writer 模板执行 | EIV、Planned Refactor | Host TextResult；native sandbox ImplementationPatch | S17 | NOT RUN |

Stage 8 的自适应图、并发、GUI 和后台任务未在当前实现中提供，因此未列为 Stage 7 漏测。

## 场景结果

| ID | 角色与真实任务 | 前置 | 用户动作 | 期望 | 实际证据 | 状态 |
|---|---|---|---|---|---|---|
| S01 | 新用户发现可用角色和模板 | 新 Workspace | `agent list`、`workflow list` | 6 个 Agent、4 个模板可见且未发布 | 数量与来源正确，Head 均为空 | PASS |
| S02 | 用户在发布前重复校验 | 未发布 builtin | 两次 validate Explorer；validate Parallel Research | 校验只读；缺失精确 Agent 版本形成结构化诊断 | 两次 Agent 校验相同；Workflow 返回 `agent_version_unresolved` | PASS |
| S03 | 用户发布所有内置能力 | 6 个 Agent 未发布 | 发布 6 Agent；校验并发布 4 Workflow | Head 原子推进；warning 可序列化且命令成功 | 4 个模板均创建 Revision；Writer 模板返回 `optional_removed` warning 且 exit 0 | PASS |
| S04 | 管理员暂停后恢复模板 | Direct 已发布 | disable → 尝试 run → enable | disabled 拒绝新 Run；enable 恢复 | 拒绝信息 `Workflow head is disabled`、exit 2；Head 2→3→可用 | PASS |
| S05 | 研究者发起普通的三视角 README 研究 | Parallel Research 已发布 | 运行较宽泛但明确的只读研究请求 | 四节点完成并输出综合报告 | `wrun_D-Blv_IACP1iBHEn` 首节点耗尽 12 次生成，Run failed，exit 1 | FAIL |
| S06 | 研究者收窄范围后重试 | 新 OPEN Task | 限定 README 章节、每 Explorer 一次 grep 后立即提交 | 新 Run 完成 | `wrun_WUxWA9vDVHMBqC-j` completed/succeeded，exit 0，23 次生成 | PASS |
| S07 | 用户在长运行后找回进度 | 两个持久 Run | 观察早期输出；`workflow runs/status/node show` | Run ID 在模型结果前可见；失败/成功均可查询 | 两次均先输出 Run ID；完整 Run/Node 投影可见 | PASS |
| S08 | 用户要求 typed 多 Agent 汇总 | S06 | 检查节点输出和绑定 | 3×EvidenceBundle → 1×SynthesisReport | 四节点均 completed；四个必需输出绑定正确；最终 Artifact `art_a88e338d550a6049e8b6db8f19ef3c1c` | PASS |
| S09 | 用户用正则查恢复命令 | S05/S06 | Provider 调用非字面 `grep` | 不再出现 rg argv `search_failed` | 15 次 grep succeeded；0 次 `search_failed`；6 次模型非法 target 被安全拒绝 | PASS |
| S10 | 高级用户创建并修改自定义定义 | 隔离 YAML | Agent/Workflow create→validate→publish→edit→publish | source revision 与不可变版本推进 | Agent v1→v2；Workflow rev1→rev2 | PASS |
| S11 | 两个终端产生 stale edit | 已有 source revision 1 | 用 expected 0 编辑，再用 expected 1 | stale 写入拒绝，正确版本成功 | stale 返回 `Extension YAML revision changed`/exit 2；随后成功 | PASS |
| S12 | 管理员撤销并发布替代版本 | 自定义 Head | disable/enable/revoke；edit+publish replacement | 精确版本永久 revoked，新版本成为 Head | revoked=true；Agent v3、Workflow rev3 成为新 Head | PASS |
| S13 | 用户输入非法运行参数 | 已发布模板 | 缺少精确 revision；查询不存在 Run | 稳定信息和 exit 2 | `plain workflow run requires an exact --revision`；`Workflow run is missing` | PASS |
| S14 | 用户对 terminal failed Run 调用恢复命令 | S05 failed Run | resume、abandon | 不重跑已终止 Run，状态保持稳定 | 两条命令均返回原 failed Run，未新增执行 | PASS |
| S15 | 运维在 Workflow 后执行 Doctor | 至少一个真实 Workflow AgentRun | `state doctor --json` | 合法运行不应产生 integrity error | 两个独立 Live 状态均报告 `agent_definition_integrity`、exit 2 | FAIL |
| S16 | 运维备份完整 Workflow 状态 | S15 | `state backup` | 生成可验证 bundle | `backup bundle could not be completed`、exit 2 | NOT RUN |
| S17 | 开发者执行 Writer 模板 | EIV/Planned Refactor 已发布 | 未执行真实写入 | 应在可丢弃项目验证修改、测试与 ReviewReport | 为避免真实仓库变更而省略 | NOT RUN |
| S18 | 运维验证备份 bundle | S16 成功 bundle | `verify-backup` | 校验 bundle | 上游 backup 失败，无 bundle | NOT RUN |

S16 的状态使用 `NOT RUN`：命令虽然执行，但核心“校验成功生成的 bundle”阶段无法开始；阻塞原因是
已确认的产品缺陷，而不是环境授权。

## 三条复杂旅程

### J1：从发现到发布，再暂停和恢复

用户从空状态发现 6 个 Agent 和 4 个模板；先看到未解析精确 Agent 版本的诊断，发布依赖后重新
校验并发布所有模板；随后暂停 Direct、验证新 admission 被拒绝，再启用。Head/OCC 状态贯穿多个
进程，最终结果 PASS。

### J2：真实四节点研究失败、纠正并重跑

第一次普通研究请求在 Explorer 1 用尽 12 次生成预算，Workflow 真实失败且 exit 1；用户通过
`workflow runs/status/node show` 定位后，新建 Task 并收窄读取范围。第二个全新 Run 串行完成三个
Explorer 和 Synthesizer，产出 3 个 EvidenceBundle 与 1 个 SynthesisReport，exit 0。最终用户目标
达成，但首轮预算敏感性保留为可靠性风险。

### J3：撤销、替代、Doctor 与备份

用户创建并发布自定义 Agent/Workflow，经历 stale edit、disable/enable、精确 revoke，再按 README
指导发布新的不可变替代版本。Head 已恢复且不再指向 revoked 版本，但 Doctor 仍把历史 Workflow
AgentRun 判为完整性错误，Backup 随之失败。最终结果 FAIL。

## 缺陷与风险

### F1 — P1：Workflow AgentRun 的有效节点预算导致 Doctor 误报，并阻断 Backup

- 受影响用户：任何执行过 Stage 7 Workflow 后需要 Doctor/备份的用户。
- 最短公开复现：发布一个 `max_agent_generation_requests` 由 Workflow 节点决定的 Agent → 成功运行
  Workflow → `morrow state doctor --workspace-id ...` → `morrow state backup`。
- 期望：合法有效预算通过完整性检查，备份包含 Workflow 记录。
- 实际：Doctor 返回 `agent_definition_integrity`/exit 2；Backup 返回
  `backup bundle could not be completed`/exit 2。
- 重现性：两个独立隔离 Live 状态均重现，包括此前单节点 smoke 状态和本次四节点状态。
- 根因边界：Workflow AgentRun 快照记录有效节点预算 `12`，而
  `application/agent_definitions/integrity.py` 将其与 AgentDefinition 的声明值 `null` 直接比较。合法的
  Workflow override 因此永远不相等。
- 临时绕过：未观察到公开 CLI 绕过；发布替代定义不能清除历史 AgentRun 的误报。

### F2 — P2 风险：内置 Explorer 对普通研究提示可能在提交前耗尽节点预算

- 首次 Run 的 Explorer 1 执行 24 次读搜工具、12 次 Provider 生成，但没有调用
  `submit_node_result`，最终 `budget_exhausted`。
- 收窄提示后一次重试成功，故当前证据不足以认定为确定性运行时缺陷；它仍是默认模板的真实 UX
  可靠性风险。
- 已观察绕过：明确限制文件/章节和工具次数，并要求读取后立即提交。

### F3 — P3：可恢复 typed submission 失败仍缺少字段诊断

成功 Run 中 Explorer 2 与 Synthesizer 各有一次 `submit_node_result:invalid_arguments`，随后自行修正。
这没有阻塞完成，但终态只保留空的细节，用户难以判断第一次结构错误的具体字段。

## 覆盖与缺口

- 发现的 Stage 7 公共能力组：9；至少部分执行：9/9（100%）。
- 计划场景：18；实际发起：16/18；PASS 13、FAIL 2、NOT RUN 3、INCONCLUSIVE 0。
  S16 已发起但无法进入 bundle 验证阶段，因此与 S18 的缺口有重叠。
- 有意义状态转换：unpublished→published、source edited、OCC stale、enabled↔disabled、revoked→
  replacement、OPEN→failed、OPEN→ready_for_acceptance、Run failed/completed 均覆盖。
- 复杂旅程：3 条；2 条最终 PASS，1 条 FAIL。
- Provider-backed：2 个本次 Run；1 failed、1 succeeded；另用独立先前 smoke 状态复核 Doctor 缺陷。
- 未执行组合：Direct 的真实 invoking-session 完成路径；EIV/Planned Refactor 的真实 Writer 路径；
  native auto-sandboxed ImplementationPatch；人为 crash 后的 blocked→resume；成功 backup 的
  verify/restore。省略原因分别为重复 Live 成本、避免真实项目修改、需要专用可丢弃工程或上游缺陷阻断。
- 仅在当前 macOS arm64 运行；Linux 仍未覆盖。

## Provider 证据

- Adapter/Model：`opencode-go/deepseek-v4-flash`
- 本次 Provider 生成请求：35（失败 Run 12；成功 Run 23）
- Run 1：`wrun_D-Blv_IACP1iBHEn`，54.16 秒，failed/budget_exhausted，CLI exit 1
- Run 2：`wrun_WUxWA9vDVHMBqC-j`，145.66 秒，completed/succeeded，CLI exit 0
- 工具汇总：15 grep succeeded、6 grep invalid_target、12 read succeeded、5 find succeeded、
  4 submit succeeded、2 submit invalid_arguments 后恢复
- 成本与 token 数：Provider 未提供可审计聚合值，因此记为 unavailable，不按 0 计算。

## 建议下一步

1. 修复 F1：让完整性验证使用 AgentRun 冻结语义允许的有效节点预算，或在快照中同时保留声明值与
   有效值并分别校验；添加“成功 Workflow 后 doctor + backup + verify-backup”公开链路回归。
2. 为 builtin Explorer 增加面向有限节点预算的提交纪律，或在接近上限时提供结构化提交提醒；用相同
   普通研究提示重复至少 3 次衡量失败率。
3. 让 `submit_node_result` 的 schema/semantic 拒绝返回安全、无值的字段路径和错误码，并在终态中
   保留聚合诊断。
4. 在可丢弃项目和原生沙箱环境中补跑 Direct、EIV、Planned Refactor、crash-resume 以及修复后的
   backup/verify/restore 链路，之后再给出完整 release-ready 结论。
