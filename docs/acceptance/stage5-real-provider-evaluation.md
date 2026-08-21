# Stage 5 真实 Provider 复合测试报告

日期：2026-08-21  
范围：Stage 5 Learning、Memory 注入、持久化 Session/TaskRun/Tool，以及真实 Provider coding 工作流  
Provider：OpenCode Go `opencode-go`  
模型：`mimo-v2.5`、`deepseek-v4-flash`

## 结论

真实 Provider 的 coding、多工具调用、任务接受、会话恢复、工具执行持久化和配置 promotion
主链路可以运行，Operational Store 最终 `health=ok`，没有发现数据损坏或越界写入。

但“自然语言偏好 → 自动学习 → 下一轮按偏好调整”的质量目前不能判定为通过：

- 3 次自然语言持久偏好测试均被真实 DeepSeek Review 判为零候选，学习准确率为 `0/3`。
- 用候选字段语义明确引导后，2 次 set/overwrite 测试生成并接受了候选，准确率为 `2/2`。
- 2 次 remove 测试均未生成候选，删除链路没有获得真实 Provider 的成功证据。
- Mimo v2.5 的 Learning Review 在默认 15 秒 Review deadline 内连续超时；同一 Review 切换
  DeepSeek 后可以完成，但仍返回零候选。
- 已接受的中文偏好能持久化并注入新任务；覆盖为 English 后，新 Session 能使用 English，
  但恢复已有 Session 的一次任务仍返回中文，说明已有会话的偏好/历史上下文刷新存在滞后。

因此，本轮结论是：Stage 5 的真实 coding、工具安全和持久化部分通过；真实偏好学习质量应保持
hold，不能宣称“用户正常表达即可稳定学习”。

## 测试边界与环境

本轮由用户明确授权真实 Provider 和网络调用。为避免污染真实用户状态，所有运行均使用临时隔离目录：

- 状态根：`/tmp/morrow-stage5-live.JEQm82/state`
- 工作区：`/tmp/morrow-stage5-live.JEQm82/workspace`
- 工作区先初始化 Git，并提交 `test: seed live coding fixture`。
- Provider 凭据仅通过已有 macOS Keychain 引用读取；报告、YAML、事件、日志和模型上下文中未记录
  密钥或完整凭据。
- 真实生产状态目录和工作区未修改。临时状态中只保留本轮测试数据和测试报告所需的非敏感摘要。

连接性验证结果：

| 检查 | 结果 |
|---|---|
| `provider show opencode-go` | credential 可用；模型包含 `mimo-v2.5`、`deepseek-v4-flash` |
| `provider test opencode-go`（Mimo active） | 连接成功 |
| `provider test opencode-go`（DeepSeek active） | 连接成功 |
| 默认真实 coding Provider | Mimo v2.5 |
| Learning Review 对照 Provider | DeepSeek v4 Flash |

`provider test` 成功只证明短探针可以连接，不能代表长上下文、结构化 Review 或多轮工具任务的
可靠性；本轮结果也验证了这一差异。

## 真实任务记录

| 场景 | TaskRun / Review | 实际操作与结果 |
|---|---|---|
| coding：修复金额舍入 | `task_DKiONkoI2qrn5bC5` | Mimo 读取目录、源码、测试、Git 状态，最小修改 `discounts.py`，将金额量化改为 `ROUND_HALF_UP`；相关测试通过，未改 tests/README。共持久化 14 次工具执行。 |
| coding：修复税率校验 | `task_3u5OiyMq2FQCgVnx` / `lrv_Cn4avvBjpB4ZR7R0` | 使用目录读取、源码/测试读取、搜索、Git、测试、补丁和 diff；仅修改源码，4 个测试通过。首次 Mimo Review 两次超时；切换 DeepSeek 重试后 Review 完成，但候选数为 0。 |
| 自然语言偏好：README 任务 | `task_OGCwnWCW6bVFYz5e` / `lrv_fsKESI-YISfc3qgD` | 用户明确要求“默认中文、代码说明简洁”，真实模型用中文完成 README 摘要；Review 完成但候选数为 0。 |
| 自然语言偏好：简单任务 | `task_ZVF4ediWqJ1gkcmh` / `lrv_inR0N2eDDaJ3umuw` | 用户只要求长期默认中文并回答 `2+2`；Review 完成但候选数为 0。 |
| 结构化 set：中文 | `task_NKY7cjbqB0UuhwNf` / `lrv_laXQj-IndLr2lLAK` | 用户明确给出 candidate 字段语义；Review 生成 1 个 `communication.language` 候选，正常接受后创建 workspace Preference，revision=1。 |
| 下一轮注入 | 同一已恢复 Session | 新任务没有重复要求中文，模型仍用中文回答；AgentRun snapshot 的 `preferences.language=中文`，证明偏好已进入下一轮模型状态。 |
| 结构化 overwrite：English | `task_xAdDAOiFMVvAIToi` / `lrv_HMv8k4bIwVWJtLNi` | 真实模型生成 1 个与旧候选冲突的 English 候选；用 `--conflict-resolution replace` 正常接受，旧 activation 标为 superseded，workspace revision=2，active language=English。 |
| 覆盖后的新 Session | `task_oAakuemrb15sbw8V` / `lrv_d8x_wIcQUsPHtRsR` | 新建 Session 后询问当前默认语言，真实模型回答 English；新 Session 注入通过。 |
| 覆盖后的旧 Session | 同一 `ses_g27ev6z4G2PERNDh` | 同一已存在 Session 中询问当前默认语言，真实模型仍回答中文；这是刷新滞后的复现证据。 |
| remove：自然语言 | `task_HrY_k83I2maB-gPz` / `lrv_5PdWw5MEuvHPBLRh` | 用户要求生成 remove candidate；Review 完成但候选数为 0。尝试直接配置工具时审批正确拒绝，未发生写入。 |
| remove：精确候选描述 | `task_3kvGCu4fJ5GUQmI5` / `lrv_ykRjtDfLxtifltXH` | 用户提供完整 remove candidate 描述且禁止工具调用；Review 仍为 0 候选。 |

Coding 任务中模型曾出现一次被拒绝的命令审批和两次失败的命令尝试，但运行时均将结果持久化
为受控状态，未越出工作区；最终测试结果仍正确。删除偏好任务中一次 `update_configuration`
审批也被拒绝，证明审批边界有效。

## 偏好学习准确率

本轮把“用户明确表达了一个应产生 candidate 的 durable preference”作为正例，把精确的
`candidate_type/path/value/operation` 作为命中条件；不把模型自然语言回复本身当作学习命中。

| 指标 | 分子/分母 | 结果 |
|---|---:|---:|
| 自然语言 set/overwrite 正例 | 0/3 | 0% |
| 字段语义明确的 set/overwrite 正例 | 2/2 | 100% |
| 所有 user-facing set/overwrite 正例 | 2/5 | 40% |
| remove 正例 | 0/2 | 0% |
| 非偏好后续任务的误报 | 0/至少 3 次 | 未观察到误报 |

“结构化 2/2”不能替代自然用户质量指标，因为它把候选 schema 和语义键直接告诉了模型；它
只能说明真实 Provider 和 promotion 管线在得到足够结构提示时能够工作。

## 持久化与 Memory 证据

最终执行 `state doctor --json` 的结果为 `health=ok`、`issues=[]`。关键计数如下：

| 持久化对象 | 数量 |
|---|---:|
| Session | 5 |
| conversation records | 93 |
| TaskRun | 13 |
| task transitions | 25 |
| Tool executions | 29 |
| AgentRun | 14 |
| Learning Review | 11 |
| Learning evidence | 22 |
| Learning Candidate | 2 |
| Candidate decisions | 3 |
| configuration activations | 3 |
| application events | 57 |
| memory selections | 14 |

Preference 的实际注入证据出现在 AgentRun snapshot：接受中文后 snapshot 的
`preferences.language=中文`；覆盖后新 Session 的 snapshot 为 `English`。这说明当前实现把
Preference 作为配置/用户状态注入，而不是 Project Knowledge 的 Memory Selection 项。

同时，14 个 `memory_selections` 的 `source_memory_revision` 和 `item_count` 均为 0。也就是说：

1. 偏好确实进入了下一轮 AgentRun 的 `preferences`；
2. 本轮没有证据证明偏好被放入 Project Knowledge Memory Selection；
3. 如果产品要求“学习偏好必须以 memory selection 形式注入”，当前行为不满足该要求；如果
   Preference 设计上属于独立配置状态，则应在文档和诊断输出中明确区分两条路径。

## 问题分析

### P1：自然语言偏好被安全地记录，却经常被 Reviewer 丢弃

证据提取层已经把用户原文存成 `user_explicit_persistent`、`explicit`、`positive`，因此问题不在
证据落库，而在真实模型把明确证据归为零 drafts。当前 Reviewer 首轮请求只告知“匹配
CandidateDraftBatch schema”，没有在首轮消息中直接提供完整 schema；完整 schema 只在修复请求中
出现。一个隔离的真实 DeepSeek 对照显示：给同一类简单偏好补上 schema 后可生成 1 个 language
candidate，而产品默认首轮仍容易返回空 drafts。建议把首轮 schema、可接受的最小 JSON 示例和
“明确正向 Preference 不应默认为空”的边界纳入 Reviewer 协议，并保留安全过滤。

### P1：Mimo 的 Review deadline 与真实响应延迟不匹配

Mimo 的连接探针成功，但默认 Learning Review deadline 为 15 秒；同一 Review 连续两次超时。
DeepSeek 重试完成，说明不是凭据或状态存储故障，而是模型/Review 预算组合问题。建议为 Reviewer
使用显式可配置的 bounded timeout、模型级默认值或异步后台 Review，并在超时后向用户展示清晰
的“任务已接受、学习稍后重试”状态。

### P1/P2：已有 Session 的偏好刷新不一致

覆盖 English 后，新 Session 读取 English；恢复的旧 Session 在一次后续任务中仍回答中文。旧会话
可能保留旧的上下文投影或历史偏好，当前系统没有给用户明确的刷新语义。若产品要求配置覆盖
立即作用于同一 Session，应在下一轮重建 user-state projection；若只保证新 Session，应在
覆盖结果和 `/status` 中明确提示。

### P2：真实 Provider 未能生成 remove candidate

自然语言和精确字段描述两种 remove 测试都返回零候选；`learning undo` 可以撤销最近一次
activation，但其语义是回到旧值，不是清空字段。当前不能用真实 Provider 证实“用户说删除偏好
即可生成并接受 remove candidate”。这是学习质量缺口，不应被 `undo` 的回滚成功掩盖。

### P2：Memory Selection 与 Preference 注入的观测语义不清

用户可观察到模型按偏好调整，但 Memory Selection 全部为空。建议提供带类型的诊断信息，明确
“Preference state injection”和“Project Knowledge memory selection”是不同来源，避免把空的
Memory Selection 误判为学习失败，或把配置注入误宣称为 Memory 检索成功。

## 后续建议

1. 先修 Reviewer 首轮输出协议和自然语言 Preference/REMOVE 识别，再用相同真实任务集复测准确率。
2. 为 Mimo 单独确定 Review 超时与重试策略；不要把短 `provider test` 当成长 Review 通过证据。
3. 明确偏好覆盖对旧 Session 的生效边界，并增加“覆盖后同 Session 下一轮”的验收用例。
4. 增加 Learning remove 的真实 Provider 正例；在没有正例前，不宣称偏好删除已通过。
5. 保持本轮隔离状态不进入生产；本报告只记录真实测试证据，不把结构化提示对照结果等同于
   自然用户学习质量。

