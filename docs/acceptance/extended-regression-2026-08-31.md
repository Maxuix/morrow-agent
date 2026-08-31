# 扩展、学习与恢复链路补测

## 结论

补测发现两项可重复的问题：**Skill 交互审批停滞**、**含可执行 Skill 文件的完整备份失败**。
不能延续上一轮核心链路样本的结论，声称所有已实现能力都正常。

聚合场景：**12 PASS、3 FAIL、0 BLOCKED、1 NOT RUN、0 INCONCLUSIVE**。
3 个失败场景中，Learning Review 首次失败已通过正式 retry 恢复；其余两个问题仍未修复。

## 测试基线

- 日期：2026-08-31，主要真实请求约 09:36–09:47，额度限制后于约 10:37 继续本地验证（Asia/Shanghai）。
- 10:52 更新：用户明确批准具体重试后，S09 真实对照完成并通过。
- `main@e1ca483`，保留此前 21 个文件的未提交清理改动；本轮没有修改产品代码或执行 Git 提交/推送。
- Darwin arm64、Python 3.13.0；正式 `.venv/bin/morrow` CLI。
- 用户授权继续未覆盖测试，承接现有 Provider 授权；模型保持 `opencode-go/deepseek-v4-flash`，adapter 为 `openai-compatible`。
- 沿用隔离目录 `/private/tmp/morrow-provider-check.ee33gW/`，workspace ID 为 `ws_EHAbosrXHbGi0ok9`。
- 新增独立最小备份复现目录 `/private/tmp/morrow-backup-regression.OxWCxO/`；其中不配置 Provider 或凭据。
- Skill 是已审阅的合成测试包，只通过脚本生成 `outputs/report.txt`；两个 MCP 都是本地 stdio 测试服务，不代表第三方 MCP 集成通过。
- 所有用户结论来自正式 CLI、真实模型回答、文件与持久状态；内部只读统计和有界备份诊断仅解释用量与失败原因，不替代端到端证据。
- 按真实用户模拟测试方法，组合扩展、项目知识、审批中断与恢复旅程；未绕过审批或直接修改数据库来制造成功状态。

## 用户能力清单

与[上一轮清单](provider-regression-2026-08-31.md)合并阅读。本轮侧重其未覆盖分支，不重复声称全部命令通过。
实现依据为 [主 CLI](../../src/morrow/interfaces/cli.py)、
[Skill CLI](../../src/morrow/interfaces/skills_cli.py)、[MCP CLI](../../src/morrow/interfaces/mcp_cli.py)、
[Learning/Memory CLI](../../src/morrow/interfaces/learning_cli.py) 及实际 help。

| 能力/入口 | 本轮状态与模式 | 场景 | 覆盖状态 |
|---|---|---|---|
| 工作空间、Provider、Model、普通 run | 复用已登记隔离 workspace 与现有模型，未切换模型或修改正常配置 | S01–S11 | PASS |
| Skill install/enable/usage/disable/remove、显式 `@skill:` | 选择、注入、无审批拒绝和引用保护通过；正式交互审批停滞 | S01、S02、S13 | FAIL |
| MCP add/refresh/enable/inspect/disable、模型调用 | 本地 stdio 加法、错误结果及资源制品 | S03、S04 | PASS |
| Artifact list/show/pin/release | available；standard → pinned → standard | S04 | PASS |
| Task accept；Learning review/retry/show/accept | 首次 Review failed，retry completed；候选 proposed → accepted | S05、S06 | PASS |
| Memory list/show、选择和 disable | 新会话选择并使用知识；取消停用、确认停用及停用后真实对照均通过 | S07–S09 | PASS |
| Session resume/status/fork | 审批进程意外退出后的恢复；闭合位置 fork | S10、S12 | PASS |
| Recovery show/resolve | never_started；拒绝直接 resume；显式 abort 后 resolved | S10 | PASS |
| AgentRun 计量/请求记录 | 完成、网络失败重试、审批取消和恢复终态 | S01–S11 | PASS |
| 原生沙箱与权限模式 | manual 拒绝审批；真实 auto-sandboxed 命令不写真实工作区 | S01、S11 | PASS |
| State doctor/backup/verify-backup | Doctor 健康；可执行 Skill 备份失败；纯文本 Skill 对照成功 | S14、S15 | FAIL |
| Grant、其余权限/平台与生命周期分支 | 未创建 unconfined host grant；其他模式及更广错误矩阵未跑完 | N01 | NOT RUN |

## 场景结果

共同清理策略：只在一次性数据中操作；保留故障样本，不删除有历史引用的 Skill。
真实请求会消耗配额；每种失败只作必要复现或一次正式重试。

| ID | 用户任务及前置条件 | 正式操作 | 预期 | 实际证据 | 状态 |
|---|---|---|---|---|---|
| S01 | 用户用已启用 Skill 生成报告，但当前无交互审批 | `run '@skill:acceptance-report ...'`，要求被拒绝后停止 | 脚本不运行，不伪造报告 | run_skill_script denied=1；模型说明 approval_rejected；Artifact 列表为空 | PASS |
| S02 | 用户在交互终端批准同一报告脚本 | 两次正式 REPL，显式选择 Skill，在工具审批处输入 `y`/`yes` | 显示执行预览，批准后生成报告制品 | 两次均显示“未提供额外预览”，确认后不继续；一次 Ctrl+C 退出，一次用于受控进程中断 | FAIL |
| S03 | 用户通过 MCP 计算，再查看预期失败 | add/refresh/enable 后让模型调用 add(17,25) 与 fail 一次 | 得到 42；能看见错误且不重复调用 fail | 两个 MCP 调用实际发生；42 与 is_error=true/boom 均回到模型；fail 未重试 | PASS |
| S04 | 用户获取 MCP 报告并调整保留策略 | 调用本地 report；artifact list/show/pin/release | 资源落入 ArtifactStore，可查看并保留/释放 | `art_HkUcOfhpqK-QJdHX`；17 字节 `MCP-ARTIFACT-831\n`；row_version 2→3→4，内容摘要不变 | PASS |
| S05 | 用户记录长期项目约定并验收后首次学习 | 模型写 project-conventions.md；task accept；learning review | 首次审查正常完成 | 约定文档写入成功；Review 返回 invalid_output、exit 2，候选为空 | FAIL |
| S06 | 用户重试失败审查并确认采纳 | learning retry/show/accept | 审查恢复，候选经确认晋升为知识 | 同一 Review attempt=2、completed；产生 project_knowledge 候选；确认后 outcome=activated | PASS |
| S07 | 用户在新会话查询已采纳约定 | 新 run 询问报表日期时区，不读取文件或调用工具；preferences status | 从已保存知识回答 UTC | 模型明确按 saved project knowledge 回答 UTC；工具 0；memory_selection_item_count=1、revision=1 | PASS |
| S08 | 用户取消停用后再确认停用知识 | memory disable 先答 n，再答 y | 取消无写入；确认后停用 | 取消 exit 2；随后 row_version 由 2 变 3、status=disabled、memory_revision=2 | PASS |
| S09 | 用户停用知识后重复同一问题 | 用户明确批准重试；新 run，不读文件、不调用工具 | 不选择停用知识，并与启用时对照 | 新会话知识选择数为 0、memory revision=2；模型明确回答没有保存的约定；工具 0、exit 0 | PASS |
| S10 | 用户从审批进程意外退出恢复 | 定位并终止唯一隔离测试进程；session resume；recovery show/resolve | 不自动重放未知副作用；可安全放弃 | awaiting_approval 被分类 never_started；直接 resume 被拒绝；abort 后 report resolved、session health=ok | PASS |
| S11 | 用户只在原生沙箱生成临时文件 | 真实 run --permission-mode auto-sandboxed，用 Python 写并读 sandbox-only.txt，不推广 | 仅沙箱有文件，真实项目不变 | 两次 bash 成功；模型回读 SANDBOX-831；真实 workspace 中文件不存在；补跑一项此前跳过的主机测试通过 | PASS |
| S12 | 用户从闭合回合分叉会话 | session fork 原标记会话，cut-position=3 | 子会话保留切点和父会话来源 | `ses_I7QIWdlcxSzJWl1R`，health=ok、position=3，parent 与 cut record 均正确；未另测分叉后模型回答 | PASS |
| S13 | 用户清理已使用的 Skill | disable，再 remove 指定版本 --yes | 不销毁历史引用的版本 | 拒绝 `Skill version is referenced and cannot be removed`，exit 2；包仍保留 | PASS |
| S14 | 用户备份带脚本的已安装 Skill | 两个不同名称完整备份；全新 script-state 最小复现 | 备份可验证且保留包语义 | 连续失败；有界诊断为 skill_package_invalid；全新状态只安装该包也失败 | FAIL |
| S15 | 用户备份纯文本 Skill，作为邻近对照 | 新 plain-state 安装文本夹具；backup/verify-backup | 正常发布且全部验证通过 | 所有校验项 True、issues=[]、credentials_excluded=True | PASS |
| N01 | 用户使用其余扩展与异常组合 | 未继续扩大测试矩阵 | 完整覆盖所有公开能力 | 未跑完 Grant、full-access-manual、auto-safe、知识 dispute/delete、故障发生于真实副作用中途、限流/断网注入、多模型/跨平台与长时稳定性 | NOT RUN |

## 复杂旅程

1. **受治理扩展到产物**：安装/启用 Skill → 模型显式选择 → 无审批拒绝 → 交互确认复现停滞；另一条独立 MCP 资源路径完成 Artifact 接收、查看、固定和释放。未用 MCP 的成功掩盖 Skill 脚本路径失败。
2. **学习失败、恢复与知识生效**：用户声明 UTC 长期约定 → 模型写文档 → 验收 → 首次 Review 失败 → 正式 retry 成功 → 确认晋升 → 新会话仅据知识回答 → 取消停用/确认停用 → 单独批准重试后，真实回答及知识选择数均确认停用生效。
3. **中断与历史保护**：审批停滞 → 终止已定位的测试进程 → 正式恢复识别 never_started → 拒绝直接重放 → 显式 abort → Session 健康 → 删除有引用 Skill 被拒绝 → 完整备份暴露可执行位丢失问题。

这些旅程仍包含真实失败步骤，不把“测试执行完”写成“旅程全部成功”。

## 缺陷与观察

### F01 — P1：Skill 交互审批无法推进，且缺少预览

- 影响：使用正式交互入口的用户无法完成本轮 Skill 脚本授权；预览也没有显示包、脚本或输出摘要。
- 最短复现：安装并启用本轮 acceptance-report 包 → 主命令进入 REPL → 输入 `@skill:acceptance-report` 并请求执行脚本 → 在正式审批提示输入 y/yes。
- 预期：受限执行预览可见，确认后执行并返回产物。实际：两次均只有“未提供额外预览”、session_write 和审批编号，输入确认后持续等待。
- 第一次 Session `ses_Yntaeek7T9ebZ9jy`，Ctrl+C 后 exit 130；第二次 Session `ses_yBMAQPfckP_fM5Zu`，在等待审批时终止测试进程。
- 第二次恢复报告 `rrp_ZySeudanTFUQGIQ2` 证实执行仍为 awaiting_approval、never_started，没有进入脚本执行。
- 初步边界：TerminalApprovalPort 与运行中输入的交接；PreparedIntent 在预检异常时会留下空预览。两者的具体根因仍需针对性诊断，不据测试结果断言是同一个原因。
- 已观察到的退出路径：Ctrl+C；或重启后明确 abort。没有证明脚本成功执行的绕行方案，未绕过审批。

### F02 — P1：完整备份丢失 Skill 可执行位，导致自身验证失败

- 影响：安装了含可执行文件的 Skill 后，即使未启用或执行，该状态的完整备份也可能无法完成。Doctor 仍可显示 health=ok。
- 最短复现：在全新 state root 安装本轮带可执行 scripts/report.sh 的包，然后 `state backup --name executable-skill`，exit 2。
- 在原隔离状态两次失败、独立全新状态再次复现；纯文本 Skill 对照备份及 verify-backup 通过。
- 有界诊断：`skill_package_invalid`，不是凭据或模型连接错误。
- 代码依据：[Skill backup](../../src/morrow/application/skills/backup.py) 的 `_write_bytes` 对每个复制文件执行 chmod 0600；[canonical tree](../../src/morrow/adapters/skills/tree.py) 的摘要包含 exec_mode。因此脚本源包的可执行属性与备份树不一致，envelope 校验失败。
- 失败备份没有发布完整 bundle；旧的成功备份仍在。本轮未通过删除历史引用或更改包权限规避验证。

### O01 — Learning Review 首次 invalid_output，正式重试恢复

Review `lrv_h4t8A7MZ9Sj5bgcH` 首次 failed；retry 后 completed，产生候选
`lcn_1jjuCP0djbUJHXbF`，采纳为 `knw_S2nu-h47FYT5VcJK`。
尚不足以判定稳定产品缺陷；保留首次失败，不将其从质量证据中抹去。

### O02 — MCP 错误结果与工具执行计数的语义不同

本地 fail 工具返回 is_error=true，模型可见且解释正确，但执行计数仍为 succeeded。
本轮只证明错误内容被送回模型，不把该 succeeded 计数当作业务操作成功证据。

### O03 — 测试环境限制

知识停用对照曾因执行审批服务限制未能发送，既不是模型故障，也不是 Morrow 的权限拒绝。
用户随后明确“批准且重试”，相同正式执行路径获准启动并完成；没有通过其他路径绕过拒绝。
运行中的 Doctor 曾报告 open_turn/open_model_request；所有进程收尾后重新检查 health=ok，未据运行中快照报告额外故障。

## Provider 与验证证据

- 当前补测普通 AgentRun 请求记录共 **21** 条：17 completed、3 network failure、1 timeout；真实运行中已观察到失败后的自动重试。统计从 2026-08-31 01:30 UTC 之后的隔离状态只读聚合取得。
- Learning Review 另有 **2 次执行尝试**。不将 SDK 内部重试次数推断为已知。
- 普通请求中有值的 total_tokens 合计 **96,983**（completed 92,261，timeout 4,722）；存在 usage unavailable，且不包含 Reviewer 用量，所以不是完整总量。成本 unavailable，不估算金额。
- Knowledge 生效：新 Session `ses_nBdaEpXAwpnMkIJp`，memory selection 为 `msel_6Zk1jGCFXFQTPCVS`，选择 1 项；工具 0，回答明确使用 UTC。
- Knowledge 停用：新 Session `ses_OVoXcxLMjP3xWDck`，memory selection 为 `msel_kyc-1FIJMDDxDSBJ`，选择 0 项、revision=2；模型回答没有保存的报表时区约定。AgentRun `arun_DU_Tg3CaJynClQDd` 最终 finish_reason=stop，工具 0；这一次用户请求经历应用内一次 timeout 重试，共 2 次模型尝试、9,450 tokens。没有额外启动第二个对照 run；收尾 Doctor health=ok。
- 原生沙箱：真实 AgentRun `arun_ZxXc-NPkhElewPpl`；补跑 `test_production_auto_sandbox_registers_only_native_tools_and_keeps_real_workspace_clean`，结果 1 passed in 0.98s。该测试是支持证据，不替代真实 CLI 旅程。
- 本轮没有再次运行完整 pytest，也没有声称全部 live 标记测试通过。此前修复门禁的 1294 passed 是上一轮证据。

## 覆盖与下一步

- 16 个聚合场景：12 PASS、3 FAIL、0 BLOCKED、1 NOT RUN；其中 Review 失败和 retry 成功分别记录。
- 三条复杂旅程均得到可观察结果，但仍包含失败环节；不具备“所有链路通过”的发布结论。
- 优先修复 F02 备份可执行位保留和 F01 交互审批，再重跑成功脚本产物、审批拒绝/取消与完整备份恢复。
- S09 已通过；之后再按价值覆盖知识其余状态、Grant、真实外部集成及平台/故障矩阵。
- 测试 Skill 和两个 MCP 已停用；被历史引用的 Skill 包仍保留。测试知识保持 disabled，对照证据留存；未逻辑删除知识或删除测试产物。
- 已终止的只是本轮精确定位的停滞测试进程，没有终止用户正常服务。正常配置、凭据和项目文件未改动。
- 临时样本及上一轮成功备份继续保留；新增完整扩展备份尚不可用。本次指定重试完成后停止，不继续重复消耗 Provider 配额；两个已复现产品问题仍待修复。
