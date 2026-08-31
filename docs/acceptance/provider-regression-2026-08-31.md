# Provider 修复后真实链路回归

## 结论

本轮已执行的用户目标全部达成，未发现新的产品缺陷。聚合场景计数：
**10 PASS、0 FAIL、0 BLOCKED、4 NOT RUN、0 INCONCLUSIVE**。
这是单一现有模型的定向回归，不是所有能力、异常路径或长期稳定性均正常的声明。

## 测试基线

- 日期：2026-08-31，约 09:21–09:26，Asia/Shanghai。
- 版本：`main@e1ca483`，含此前尚未提交的 21 个文件的死代码清理和文档修正；不是纯提交快照。
- 平台：Darwin arm64，Python 3.13.0；正式 `.venv/bin/morrow` CLI，默认 manual 权限模式。
- 用户明确授权现有 Provider 测试。使用 `openai-compatible` / `opencode-go/deepseek-v4-flash`，没有切换或回退模型。
- 隔离目录：`/private/tmp/morrow-provider-check.ee33gW/`；工作空间为其 `workspace/`，状态为其 `state/`。
- 只复制当前模型的公开配置及凭据引用；凭据始终由现有安全存储解析，不复制密钥，不使用真实用户会话、偏好或项目源码。
- 连接检查也限定为当前模型：隔离配置只注册 `deepseek-v4-flash`，避免 `provider test` 按首个注册模型选择其他模型。
- 工作空间通过正式交互入口登记、填写目标并 `/exit`；所有场景通过 CLI 执行。内部配置复制仅用于隔离准备，不作为用户场景通过证据。
- 按真实用户模拟测试方法生成三条连续旅程，使用返回内容、实际文件、公开状态和备份校验共同判定，不只检查进程是否成功退出。

## 用户能力清单

本轮发现 16 组公开能力，其中 10 组实际操作了代表性入口；组内未运行分支不因此被视为通过。
入口证据以 [CLI 注册与实现](../../src/morrow/interfaces/cli.py)、
[Skill CLI](../../src/morrow/interfaces/skills_cli.py)、[MCP CLI](../../src/morrow/interfaces/mcp_cli.py)、
[Learning/Memory CLI](../../src/morrow/interfaces/learning_cli.py) 和各命令 help 为准。

| 能力组 | 入口/实现证据 | 可达状态或模式与本轮边界 | 场景 | 状态 |
|---|---|---|---|---|
| 工作空间与普通对话 | 主命令、`run` | 首次登记、manual、新回合、流式完成 | P02–P05 | PASS |
| Provider | `provider list/show/test` | 现有凭据可用、连接成功；未增删正常配置 | P01 | PASS |
| Model | `model current` | 当前模型核对；未切换或同步模型 | P01 | PASS |
| Session | `session list/status/archive`、`run --session-id` | 新建、跨进程续接、归档；未 fork | P03、P09 | PASS |
| Task | `task show/accept` | ready_for_acceptance → accepted；未取消或重试 | P06 | PASS |
| AgentRun | `run` 终态、`agent-run show` | 工具轮次、请求计量、成功终态 | P04、P05 | PASS |
| Preferences | `preferences add/list/status/disable` | active → disabled；下一新会话注入对照 | P07、P08 | PASS |
| Learning | `learning reviews/review/status` | pending → completed；空候选合法完成 | P06 | PASS |
| Memory | `memory`、`memory selection` | 未晋升知识；未操作 Memory 生命周期 | N03 | NOT RUN |
| Artifact | `artifact` | 普通 write 不发布 Artifact；无可验收目标 | N02 | NOT RUN |
| Recovery | `recovery` | 未制造 interrupted/corrupt 状态 | N02 | NOT RUN |
| Grant | `grant` | 本轮文件工具不要求 elevated grant | N02 | NOT RUN |
| State | `state doctor/backup/verify-backup` | health ok、完整 bundle 校验；未 restore/cleanup | P10 | PASS |
| Skill | `skill` | 上轮已离线验证；本轮未接入模型使用或脚本执行 | N01 | NOT RUN |
| MCP | `mcp` | 上轮已用 fake stdio；本轮未接入真实外部 MCP | N01 | NOT RUN |
| 权限及其他执行模式 | `--permission-mode` | 仅 manual；未复测主机原生沙箱、审批拒绝或其他平台 | N04 | NOT RUN |

Stage 7 Workflow、远程 MCP transport 等未交付能力不计入已实现清单。

## 场景与结果

执行前的共同约束：仅一次性工作空间、合成文本和现有模型；不修改正常数据、不自动采纳学习候选。
Provider 调用有实际配额消耗；仅执行下表的必要请求，未循环进行质量采样。

| ID | 用户与真实任务 | 前置条件 | 正式操作 | 预期结果 | 实际证据 | 状态 |
|---|---|---|---|---|---|---|
| P01 | 已配置用户确认模型能用 | 当前模型及兼容凭据存在 | `model current`、`provider show`、隔离 `provider test opencode-go` | 使用指定模型连接成功 | 当前模型一致；凭据可用；“连接成功”，exit 0 | PASS |
| P02 | 新用户登记一次性项目 | 隔离模型配置完成 | 主命令 `--dir`，确认登记、填写目标、`/exit` | 正常进入和保存退出，不要求重新输入已有凭据 | 交互入口进入 REPL 并正常退出，exit 0 | PASS |
| P03 | 用户关掉进程后继续对话 | 已登记 workspace | `run` 记住 MAPLE-831；新进程 `run --session-id` 询问标记 | 恢复相同会话与任务并答对 | 返回 `MAPLE-831`，两次 exit 0、finish_reason=stop；Session/Task ID 相同 | PASS |
| P04 | 用户创建可核对的小文件 | 一次性目录为空 | 新 `run` 要求创建 checklist.txt：draft、MAPLE-831 两行，再回读 | 文件确实存在且内容正确 | `ls → write → read` 三次工具成功；落盘内容正确；exit 0 | PASS |
| P05 | 用户修正上一轮交付 | P04 完成 | 同 Session 新进程要求把第一行改成 approved，保留第二行并核对 | 原文件被正确修改，其他文件不变 | `read → edit → read` 三次成功；文件为 `approved\nMAPLE-831`，18 字节；exit 0 | PASS |
| P06 | 用户验收任务并查看学习结果 | P05 达到 ready_for_acceptance | `task accept`；`learning reviews`；`learning review`；`learning status` | 任务接受，Review 入队并可由真实模型完成 | accepted；Review pending → completed；attempt=1、repair_used=False、无错误、无候选 | PASS |
| P07 | 用户希望以后回答采用固定格式 | workspace 偏好 revision=0 | `preferences add` 要求结尾另起一行 CHECKED-831；新 Session 解释代码审查；`preferences status` | 偏好真实影响新回答 | revision=1、injected_count=1，回答结尾为 CHECKED-831，exit 0 | PASS |
| P08 | 用户停用格式偏好 | P07 完成 | `preferences disable --expected-revision 1`；新 Session 提同一问题；状态查询 | 停用后不再注入 | revision=2、disabled=1、injected_count=0；回答不含标记，exit 0 | PASS |
| P09 | 用户归档已验收会话 | P06 完成 | `session archive` 后 `session status` | 正常归档、无当前任务、历史保留 | lifecycle=archived、health=ok、current_task_run_id=None、conversation_position=18 | PASS |
| P10 | 用户确认测试数据可恢复 | 所有已执行旅程完成 | `state doctor`、`state backup --name provider-regression`、`state verify-backup` | 状态健康且备份完整、不含凭据 | health=ok、schema=22；所有校验项 True，issues=[]、credentials_excluded=True | PASS |
| N01 | 用户让模型使用扩展 | 需独立 Skill/MCP 场景 | 本轮未调用 | 验证实际扩展调用 | 本轮为 Provider 核心链路补测，未将原离线扩展结果冒充真实调用 | NOT RUN |
| N02 | 用户从故障/审批/Artifact 状态恢复 | 需相应目标状态 | 未造故障或副作用目标 | 验证恢复与审批边界 | 没有 interrupted execution、需提升权限或 Artifact 对象 | NOT RUN |
| N03 | 用户采纳学习候选并晋升知识 | 需 Reviewer 产生候选 | 未采纳或晋升 | 候选生命周期与 Memory 生效 | 本次 Review 产生零候选；未伪造候选来声称端到端通过 | NOT RUN |
| N04 | 用户在其他模式/平台长期使用 | 需额外测试矩阵 | 未执行 | 跨平台、断网/限流/长上下文稳定性 | 只覆盖当前 macOS manual 与单一模型样本 | NOT RUN |

可复核身份：workspace `ws_EHAbosrXHbGi0ok9`；对话 Session `ses_dn5RSXrIl_aNgw3W`；
文件 Session `ses_9LCS5FFzVanB68cD`；文件 Task `task_BUWHz6prw37Yr6_A`；
Review `lrv_UQjl-7xT6HMpYHUr`；偏好 `pref_MPxM8KK3X1JIW9-m`。
这些标识仅属于一次性测试状态。

## 三条复杂旅程

1. **跨进程连续对话**：登记项目 → 新 Session 记住合成标记 → 退出进程 → 显式续接 → 正确回忆。变化是进程重启；身份与内容连续性均通过。
2. **交付修正与验收**：模型创建文件并回读 → 用户在新进程提出修改 → 冲突安全 edit 与回读 → Task 接受 → Learning Review 真实执行 → 会话归档。持久文件、任务和审查状态衔接通过；零候选不被当作知识晋升成功。
3. **偏好生效与撤销**：写入 workspace 偏好 → 新 Session 真实回答验证注入 → 停用偏好 → 再开新 Session 作对照 → 状态与备份检查。回答变化与 injected_count 从 1 到 0 一致。

## 发现与环境说明

- 无新增已复现产品缺陷。
- 嵌套执行沙箱第一次凭据检查返回 unavailable；在获得主机执行许可后，同一凭据返回可用且真实连接成功。因此归类为测试环境访问限制，不是 Provider 或输入保护修复回归。
- Doctor 的两次初始调用参数不符合其 CLI 合同（不支持 `--dir`，需要 `--workspace-id`），被正确拒绝；改用正式参数后通过。这是测试驱动参数修正，不是产品缺陷，也不计为额外失败场景。
- 本轮未修改产品代码、正常 Provider 配置或现有会话；未提交或推送此前遗留清理改动。

## 覆盖边界

- 14 个聚合场景中执行 10 个，10 个通过；4 个明确 NOT RUN。三条复杂旅程均执行完成。
- 本轮验证新增和重复使用、跨进程续接、文件写入/修正、验收/归档、Review 完成、偏好启停和备份；没有用单元测试替代这些用户入口证据。
- 之前修复后的离线门禁为 1294 passed、2 skipped、2 deselected；本轮没有再次运行整套 pytest，也未把两项独立 live 标记测试声称为已执行。
- Skill/MCP、知识晋升、异常恢复、权限矩阵、原生沙箱、多模型、长上下文及网络故障仍有覆盖缺口。

## Provider 证据与用量

- 模型：`openai-compatible` / `opencode-go/deepseek-v4-flash`，本轮从未切换。
- 应用层共 14 次模型调用：连接检查 1 次；6 个普通 AgentRun 共 12 次请求；Learning Review 1 次且未使用 repair。SDK 内部传输重试没有单独计数。
- 6 个普通 AgentRun 都以 finish_reason=stop 完成，retry_count=0；6 次工具调用全部成功，无失败或被拒绝工具。
- 普通 AgentRun 可见 total_tokens 依次为 4771、4716、19846、21389、4745、4679，合计 **60,146**。该值不包含连接检查与 Learning Review，不能当作全部计费量。
- Provider 成本字段 unavailable；未猜测金额。Review 从 started_at 到 completed_at 约 9 秒；其余未做独立延迟基准。
- 保留了合成输出、公开计量、文件及状态证据，未记录凭据、推理正文或原始 SDK 请求/响应。

## 后续与保留物

- 当前修复可继续使用，无本轮证据支持的新修复事项。若需要扩大信心，优先单独验证学习候选晋升和故障恢复，再覆盖实际 Skill/MCP 和其他权限模式。
- 不为增加次数重复发送相同 Provider 请求；本轮在三条旅程和完整性校验通过后停止。
- 一次性工作空间、合成文件和备份暂时保留以供复核；本轮未做删除。未写入正常项目的文件或正常用户会话。
- 备份位于 `/private/tmp/morrow-provider-check.ee33gW/state/backups/operational/provider-regression.bundle/`，校验通过且排除了凭据；临时目录不是长期归档承诺。
