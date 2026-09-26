# Morrow 基础 Harness 技术分析与改进方案

日期：2026-09-24
审计代码：`172750ec4bffbd45a8e2bc4ec100a7944c412a44`
范围：单 Agent 的模型请求、工具执行、上下文、终止、恢复、取证和评测适配。本文是分析与修改建议，未实施运行时代码修改，未启动新 benchmark。

## 1. 核心判断

**当前优先级应是修正执行契约、提高有效执行时间利用率、建立交付验证闭环。现有证据不支持把主要瓶颈归结为“缺少更多 Agent 功能”。**

Morrow 已有单一主循环、持久化工具意图、结构化结果、上下文压缩、失败重试和产物回读。这些基础并不弱；问题在于部分机制的边界不一致：

- 模型看到的是常规 bash，执行端却可能拒绝合法脚本、静默压低 timeout，并清理后台进程。
- Provider 支持瞬态重试，但一个长任务执行过工具之后，后续无效响应仍可能直接结束整个任务。
- 上下文能够缩小到预算内，但失败降级时，已验证事实、失败假设和运行中进程的信息不一定保住。
- 模型输出最终文本即可结束回合；普通任务缺少把“有最终回答”与“目标已验证”连起来的检查。
- 外层 Harbor 知道任务截止时间，内层 AgentLoop 缺少相应的剩余时间和收尾预算。
- 取证仍依赖退出后的日志回收与终态指标，失败最严重的任务恰好最难诊断。

本次确认了两类可离线复现的问题：**合法 shell 被拒绝**、**非连续失败被计为连续失败并中止任务**。另确认了工具超时、进程生命周期和重试边界的现存限制。它们对具体任务分数的贡献，仍需修复后的受控实验测量，不能承诺修复后达到某个分数。

建议顺序：

1. 修 shell 语义、公开真实执行边界、修错误计数。
2. 完成超时取证和请求级恢复。
3. 处理长命令、任务截止时间与收尾。
4. 加入轻量交付验证和压缩保真验证。
5. 在固定版本、相同资源条件下做对照评测。

## 2. 结果复核：哪些结论可以成立

### 2.1 原始结果复算

输入为[最终报告](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/results/final-evaluation-report.md)，并重新读取本机保留的 High trial `result.json`。按任务名选择 `finished_at` 最新且有数值 reward 的记录；归档副本也参与检索，同一时间的副本优先取 jobs 目录。没有挑最高分。

逐项结果、路径、结果文件 SHA256 和有限字段汇总保存在[审计快照](/Users/ruirui/Documents/Project/Agent/developing/docs/research/harness-benchmark-audit-2026-09-24.json)。快照不复制凭据、命令参数或模型文本。

| Harbor 执行状态 | reward 1 | reward 0 | 合计 |
| --- | ---: | ---: | ---: |
| 无 exception_info | 35 | 29 | 64 |
| AgentTimeoutError | 2 | 22 | 24 |
| NonZeroAgentExitCodeError | 0 | 1 | 1 |
| 合计 | 37 | 52 | 89 |

因此：

- **官方 verifier 综合通过率仍是 37/89 = 41.57%。**
- 25/89，即 28.09% 的所选记录带执行异常；不能把这 25 项全部算成未完成，因为其中 2 项 verifier 通过。
- 52 个未通过结果中，23 个带执行异常，29 个不带执行异常。前者占失败结果的 44.23%，说明可靠性与时限治理值得优先处理，但不能推断修复后这 23 项都会通过。
- 29 个无 Harbor 异常的失败中，指标明确记录了 password-recovery、train-fasttext 的 `invalid_response`，以及 video-processing 的 `context_budget`。另 1 项缺少对应指标；其余 25 项记录 `stop`。**“没有 Harbor 异常”不等于“模型完成了有效尝试”。**
- 有 trace 的所选任务为 74/89；有可用 usage 的任务为 61/89。已知用量为 20,088,380 input + 623,016 output，与最终报告一致。未知部分不能按零补齐。
- 74 份可用 trace 中，22 个任务出现合计 50 次 `invalid_command`；另有 5 次 `outside_workspace`。它们是工具接口摩擦的直接证据，但日志没有完整命令，不能把 50 次全部归因于同一个 parser 缺陷。

### 2.2 原报告的解释边界

最终分混合了 pilot、full 和定向重跑，涉及不同代码、安装资产与 timeout multiplier。它可以作为此次 campaign 的综合观测值，**不能作为当前 HEAD 的纯净分数，也不能直接和其他 Agent 的排行榜分数比较**。

早期根因报告中的“39 个失败仅约 13 个属于能力问题”，针对的是旧 64 项基线，不是最终 52 个失败。该归因还受日志完整性与任务选择影响，不应转化为“修好 Harness 就能恢复其余全部分数”。

本次复核还发现：[R3 报告](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/results/tb2-high-r3-repair-rerun-error-report.md)当时称 11 个 timeout 均无逐轮日志，目前本地其中 4 项已有部分 trace：regex-chess、rstan-to-pystan、schemelike-metacircular-eval、write-compressor；其余 7 项仍无 trace。这说明历史报告与当前保留材料存在时间差。部分 trace 也不能替代完整工具轨迹。

模型配置的可用记录为 Volcengine OpenAI-compatible API、glm-5.3-flash、High effort，见[pilot 报告](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/results/tb2-high-pilot-report.md)。本文比较的是 Harness 机制，不用不同模型的排行榜成绩推断代码优劣。

### 2.3 历史缺陷的当前状态

| 历史问题 | 当前源码状态 | 本次判断 |
| --- | --- | --- |
| D1：短重试、invalid response 中止、压缩失败中止 | 已增加 5 次上限、jitter、累计 120 秒重试等待；摘要重试与上下文降级已存在 | 部分缓解；请求级重试边界与长任务恢复仍不足，见 H5 |
| D2：uv/uvx 污染 verifier | 已改为 /opt/morrow/tools，仅加入 Agent 进程 PATH | 旧修复已落地，不再列为当前未修缺陷 |
| D3：TaskOutcome artifact refs 超限崩溃 | 使用 build_bounded_task_outcome，限制数量与体积并记录遗漏 | 旧修复已落地；相关离线测试本次通过 |
| D4：旧 glibc 无兼容 wheel | 资产脚本增加 manylinux 兼容平台和 Debian 11 无网络安装检查 | 当前代码已处理；本次未重新构建镜像验证；R1 已从安装失败进入执行后超时 |
| D5：timeout 丢日志 | 已增加 finally、shield 和 15 秒尽力回收 | 仍未达到可靠取证；当前最终选择中 15 项无 trace |
| D6：默认工作区为 / | 已读取任务 workdir，否则容器 pwd | 默认目录问题已处理；结构化文件工具与系统任务的路径边界仍有摩擦 |
| D8：后台进程不存活 | 本次定位到 HostProcessAdapter 主进程退出后主动清理同进程组 | 不只是容器 exec 的偶然现象，见 H2 |

源码依据：[重试策略](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/core/runtime_policy.py:17)、[Outcome 组装](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/application/tasks.py:217)、[Harbor adapter](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/harness/morrow_harbor_agent.py:132)、[资产脚本](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/scripts/prepare_assets.sh:36)。

## 3. 当前问题与修改方案

证据等级：A = 本次离线复现；B = 当前源码明确行为；C = benchmark 相关现象或待测假设。优先级 P0 表示应在下一次有成本的评测前处理，P1 表示下一轮基础 Harness 改进重点。

### H1｜P0：shell 预检与工具声明的执行语义不一致

**证据：A + B；关联现象：C。**

[ProcessExecutionService.preflight](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/services/process.py:178)对整个 shell 字符串运行 `shlex.split`，并拒绝任何空 token。它把“命令字符串切词”当成了“shell 程序语法检查”。

本次通过 `/bin/sh -n -c` 检查的两个合法输入，都被当前 preflight 拒绝：

| 输入 | shell 语法检查 | Morrow preflight |
| --- | --- | --- |
| `printf '%s\n' ''` | exit 0 | invalid_command |
| 带 quoted heredoc，正文含 `# don't parse this as shell syntax` | exit 0 | invalid_command |

前者的空参数本来就是合法值；后者的 heredoc 正文不该再被 shell 切词器当成普通 shell 语法。编写 Python、SQL、正则和生成文件时，这类输入很常见。

此外，工具 schema 描述“Bash command”，[执行端](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/adapters/local/process.py:151)调用 `asyncio.create_subprocess_shell` 却没有指定 Bash。在 POSIX 上其默认 shell 是 /bin/sh；若任务镜像使用 dash，Bash 专用语法会失败。该差异目前是源码风险判断，本次未在 Linux 镜像执行复现。

**修改方案：**

1. 将 argv 校验与 shell 脚本校验分开。argv 只要求命令名非空，其余参数允许合法空字符串。
2. shell 输入仅做 NUL、长度等边界校验；复杂脚本的审批分类应保守归为 shell/unknown，不因切词失败拒绝合法脚本，也不能因此绕过权限。
3. 明确执行 `/bin/bash -c` 或已探测的 shell。若环境只有 POSIX sh，工具描述同步说明；启动时固定 shell 路径及版本。
4. 将 quoted heredoc、空参数、嵌套引号、命令替换、Bash 专用语法和 Unicode 纳入契约测试。
5. 将 preflight 失败与已启动进程的非零退出分别呈现，避免模型错误地认为脚本已执行。

**验收：**上述两例不再误拒绝；复杂脚本的执行结果与指定 shell 一致；审批和脱敏行为保持可验证。50 次历史 invalid_command 的具体归因须在补足命令指纹后确认。

### H2｜P0：90 秒静默截断与后台清理限制了长任务

**证据：B；关联现象：C。**

[BashArguments 与 request 转换](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/application/local_tools.py:685)将 timeout 强制限制在 1–90 秒，模型可见 schema 却只有“Timeout in seconds”，没有最大值。请求 300 秒会静默变成 90 秒。运行策略还有 120 秒默认工具上限、300 秒代码上限，形成多套不一致的数值。

[HostProcessAdapter](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/adapters/local/process.py:163)在主进程退出后检查并终止同一进程组中的子进程。因此 `server &`、后台编译等写法可能在 shell 返回时失去进程；stdin 又是 DEVNULL，交互程序也没有终端会话语义。

这对 QEMU、服务器配置、长编译和训练尤其不利。QEMU 的新结果已经进入工具执行，不能继续把其失败归为旧安装问题；但现有 trace 不足以证明 H2 是其唯一根因。

**修改方案：**

- 先统一 schema、policy、执行端上限，超限显式拒绝并给出有效值，禁止静默 clamp。
- 对短前台命令保持有界执行；对长命令提供“启动、轮询、停止”这一最小生命周期，返回稳定 execution_id、状态、退出码和输出读取位置。可作为现有 bash 的模式扩展。
- 已跟踪进程归属当前 task/session；用户取消、任务终止和恢复时有确定处理。不要通过取消所有清理来实现后台执行。
- 任务必须交付的服务需要明确“最终验收时保持运行”的生命周期，不能统一在 shell 返回或最终回答后杀掉。
- 先实现非交互长命令与服务；只有复现实例确实要求交互时再接 PTY。不要为了补一个边界引入完整终端平台。
- 启动上下文说明 cwd、shell、cd/env 是否跨调用保留、前台时限与后台规则。

**验收：**使用可控假进程验证长命令不被 90 秒截断、poll 不重复启动、取消只停止所属进程、服务跨工具调用存活；进程集成测试使用握手/事件，不靠固定 sleep。

### H3｜P0：任务截止时间未传入内层决策，模型请求缺少总时限

**证据：B；关联现象：C。**

Harbor 的 timeout multiplier 在[评测驱动](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/run_tb2.py:184)生效；adapter 执行 `morrow run` 时没有把剩余时间交给 AgentLoop。模型和工具可以继续开始新的长操作，直到外层取消。

[Provider stream](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/adapters/models/openai_compatible.py:694)已有连接/首块/块间时限，不能称为“完全没有 timeout”。但持续产生 SDK chunk 的单次请求没有独立的总用时上限；首块和块间默认同为 45 秒，又不能表达不同模型的推理特征。

[R4 记录](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/results/tb2-high-r4-verifier-rerun-error-report.md)中，winning-avg-corewars 一次模型阶段间隔达到 1393.3 秒，后续 2× 尝试在约 1751.7 秒 thinking 后 internal 结束。证据支持长时间停留在模型阶段，不能区分真实推理、服务端等待或具体内部异常。

**修改方案：**

1. 引入可选的 run deadline，由 benchmark adapter 明确传入；普通交互任务继续由其宿主策略决定，不强加 benchmark 时限。
2. 使用单调时钟计算 remaining_seconds，在每次模型请求、重试、工具调用和压缩前检查。
3. 为产物落盘、可见验收、终态指标与日志导出预留收尾时间；临近截止时将“剩余时间及应完成的收尾”作为受控上下文提供给模型。
4. 区分 connect、first semantic token、idle、total attempt、total run 五种限制。每次请求的上限不得超过剩余预算。
5. 记录模型阶段的 activity 时间和类型；用匿名计数判断进展，不保存隐藏推理文本。
6. 先固定 High 做 deadline 对照。动态降低 reasoning effort 必须单独设实验组，不能混入原 High 基线。

**验收：**假时钟覆盖“不断有 chunk 但总时限到达”“重试跨截止”“只剩收尾预算”；在外层 kill 前输出部分用量、明确终止原因和已产生的产物。增加时限的收益应由对照实验判断。

**实施记录（2026-09-26）：**新增可选 `morrow run --run-timeout-seconds`，Harbor adapter 按 trial 配置中的 task timeout、覆盖值、上限和 multiplier 计算，并扣除已耗时间及日志回收预算后传入。AgentLoop 用单调时钟预留终态时间，在模型请求、压缩、重试和工具调用边界检查；单次模型请求另有 600 秒总上限，持续收到 chunk 也不能越过上限。临近截止时向本次模型请求提供有界收尾提示，匿名记录模型活动类型、块数和阶段耗时，截止后以 `run_timeout` 终态保存已有事实。OpenAI-compatible adapter 将连接、首个语义内容和块间空闲分别限时；空 chunk 不重置首个语义内容的时限。离线假时钟和 adapter 用例已覆盖持续 chunk、重试、工具后截止及参数传递；**尚未执行 High 对照评测**，无可声明的分数提升。

### H4｜P1：最终回答与交付验收之间缺少闭环

**证据：B；行为案例：C。**

[AgentLoop 最终文本分支](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/runtime/agent.py:1892)接受有效的无工具最终文本后结束回合。Morrow 已有 ValidationFact 和 outcome 证据，问题是普通任务没有在结束前利用它们检查最新交付状态。

旧根因材料中的 configure-git-webserver 在验证后拆掉可用状态；cancel-async-tasks、filter-js-from-html 出现验证结论未得到有效处理；nginx-request-logging 因精确输出格式未满足而失败。这些涉及模型判断，也暴露了 Harness 可以改进的交付检查环节，不能简单都归入不可优化的“纯能力问题”。

[Direct Coding 协议](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/application/prompt.py:29)还统一要求交付前清理临时产物。若任务目标是运行中的服务或生成环境，这句通用指导可能产生歧义；不能据此断言它就是历史自毁行为的原因。

**修改方案：**

- 从用户任务提取轻量验收清单：输出路径、格式/单位、运行状态、公开测试、性能条件。不要读取或注入隐藏 verifier。
- 在首次最终回答前做一次有限的完成检查：有哪些目标已有工具证据、最后一次修改后是否验证、是否还有失败验收或未结束的关键执行。
- 有明确缺口时反馈具体缺口，允许有限次数修正；禁止无限“再检查一次”循环。没有可运行测试的任务可以保留 unverified，而不是强制伪造测试。
- 将验证结果绑定产物版本或工作区变更摘要；“测试通过后又修改”应使旧证明过期。
- 将清理原则改为只清理由本任务产生且确认不属于交付物的临时项；运行环境、必要服务和任务要求的文件须保留。
- 终态分别记录 execution_finished、validation_passed/failed/not_run、goal_verified/unverified。它们不覆盖官方 verifier reward。

**验收：**构造“路径写错”“单位错误”“测试失败却拟结束”“通过后又改坏”“服务被清理”五类公开小任务；检查能指出真实缺口，并且不对纯解释任务强制跑测试。

**实施记录（2026-09-26）：**普通任务在拟提交最终文本时先检查本次有序工具事实。公开任务中出现输出路径、格式、单位、服务、测试或性能要求，或最新验证已有明确缺口时，至多追加两次有界交付复核；复核提示只含用户任务、拟交付文本和公开工具事实，候选回答不写入 ConversationLog。Workflow 节点沿用自身提交与验收流程，纯解释任务不增加模型请求。`ValidationFact` 在后续文件变更后失效，终态分别记录 `execution_finished`、`validation_outcome` 和保守的 `goal_verification=unverified`；公开测试通过仍不等于目标已由官方 verifier 确认。Direct Coding v4 只清理非交付临时产物，并保留冻结 v3 协议的恢复读取。离线用例覆盖路径与单位复核、失败验证、通过后变更和旧协议恢复；**尚未执行 Harbor 对照评测**，不能声明实际得分提升。验证后出现不透明命令时保守地使旧证明过期；现有事实无法精确判断命令是否改动文件，后续应通过工作区变更摘要缩小误判。

### H5｜P0：重试范围过粗，长任务执行工具后仍容易早退

**证据：B，现有回归测试直接固定该行为。**

[_can_retry_provider_failure](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/runtime/agent.py:232)拒绝“已有工具意图 + invalid_response”的重试；调用方传入的是 `state.tool_calls > 0`，代表整个本次 run 已有工具调用，而不是这次失败请求有没有提交副作用。

[test_nonretryable_invalid_response_and_post_tool_defect_do_not_repeat_work](/Users/ruirui/Documents/Project/Agent/developing/tests/test_long_task_reliability.py:228)明确验证：工具成功一次，下一次响应虽标记 retryable，仍停止为 invalid_response。因此旧 D1 的这一分支没有被完全解决。

另外，[provider_retry.py](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/runtime/provider_retry.py:38)的 120 秒是**全 run 累计 sleep 预算**，不是 120 秒真实恢复窗口。成功请求会重置 consecutive retry 次数，却不重置累计等待。默认 5 次无 Retry-After 的等抖动退避，总 sleep 约 31–62 秒；不能写成每次网络故障都可恢复两分钟。

**修改方案：**

1. 以“本次模型请求的提交点”为重试边界：当前请求尚未提交 assistant/tool intent 时，可在已有 durable history 上重新请求；过去的工具结果保留。
2. 已提交或完成状态未知的工具继续遵循不可自动重放原则。重试模型请求不等于重跑整个 AgentRun，也不等于重放已完成工具。
3. 将单请求/单故障恢复窗口与全任务累计预算分开，并受 H3 的剩余时间约束。永久鉴权、明确非法参数等错误继续快速失败。
4. 重试次数与累计等待在恢复时有一致持久化口径，避免重启重置预算或反复使用旧工具意图。
5. 对“暂时无法恢复”的任务提供精确可继续状态，避免将业务产物已存在的长任务仅呈现成一个通用错误。

**验收：**先执行一次计数型工具，再注入可重试坏流，再正常响应；工具累计执行必须仍为一次，任务可继续。另测未知副作用不重放、永久错误不重试、多次短故障、摘要与主请求共享总预算。

### H6｜P1：压缩保证了结构安全，语义保真仍缺少验证

**证据：B；对分数的影响待实验。**

当前实现已保留用户锚点、完整 tool-call/result 配对、结构化摘要和文件清单；不应再建议“从零增加压缩”。

不足主要有两点：

- [bench_setup](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/harness/bench_setup.py:52)只填 reasoning 能力；默认 OpenAI-compatible adapter 没有精确 context_window_tokens，因而通常使用 262,144 字符的保守 fallback。它是请求边界，不是模型真实 token 窗口；可能过早压缩，也可能估算不准。
- [degrade_model_input](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/application/context.py:500)在摘要失败时按完整旧 turn/cycle 丢弃投影。它保持日志与配对正确，却不保证丢弃部分中的已验证结论、错误单位、失败方案和运行中进程仍在模型输入中。`context_degraded` 当前主要是事件，不能等价于模型已获知丢失了哪些关键信息。

**修改方案：**

1. 从明确配置或验证过的模型元数据设置 context/output limits；报告区分真实 usage 与估算。不要用累计 input tokens 推断单次上下文大小。
2. 在现有 ContextBuilder 中派生一个小型任务状态投影，保留目标约束、关键路径、当前方案、失败尝试摘要、最近有效验证和活动进程标识。它不能成为第二聊天历史写入者。
3. 压缩降级时向模型提供明确的缺失说明和可回读的 artifact/记录引用，避免静默丢失后继续假定记忆完整。
4. 在任务状态信息与摘要中，模型判断应标记为判断；只把工具结果和已保存事实当作证据。
5. 先建立压缩前后继续执行的语义测试，再考虑 tokenizer 或更复杂压缩器。现有按工具类型截断、artifact 回读机制应保留并测试，不必先整体替换。

**验收：**同一脚本任务在不压缩、正常压缩、摘要失败降级三种路径下都能记住输出路径、反例和仍存活的进程；最终公开验收相同。优先避免“进程活着但方案失忆”。

**实施记录（2026-09-26）：**Benchmark bootstrap 现接受显式 `context_window_tokens` 与
`max_output_tokens`，未知值仍保持字符预算，不以累计 usage 猜测窗口；混合了估算增量的上下文计数
标为估算，真实 Provider usage 仍独立记录。ContextBuilder 在摘要失败而省略旧轮次/工具周期时，
向模型加入有界的缺失范围、工具结果事实、关键路径、失败原因与 Artifact/进程引用，并要求重新
核验进程状态；摘要提示明确区分模型判断与工具/保存记录证据。离线测试覆盖同一脚本任务的完整、
正常摘要、降级三条上下文路径，以及凭据过滤和降级事件。未运行真实 Provider 或公开 benchmark，
因此本节关于最终分数和公开验收的影响仍待实验。

### H7｜P1：防循环计数把间隔失败当成连续失败

**证据：A + B。**

[AgentLoop failure_streaks](/Users/ruirui/Documents/Project/Agent/developing/src/morrow/runtime/agent.py:2090)以 tool name、error code、validation_path 为键累加，成功调用后不清零，也不包含参数或结果指纹。字段路径相同的第三次失败即可触发 loop_detected。

本次用 ScriptedModelProvider 复现：

`bad-one → ok → bad-two → ok → bad-three`

结果为三次失败、两次成功后 `finish_reason=error, stop_code=loop_detected`；最后预置的正常回答没有执行。这与错误消息中的“连续、完全相同”不符。

另一方面，返回成功但实际上没有进展的重复读取/命令，也不会被这个机制识别。现有 timeout trace 工具调用多，不能据此证明发生了这种循环。

**修改方案：**

- 将连续失败与累计失败分开；成功或相关有效状态变化后重置连续计数。
- 识别“同一失败”时包含归一化参数指纹和相关结果/状态指纹；避免把不同搜索目标或不同编辑尝试混为一类。
- 对成功但无进展的重复行为，先做有界窗口提示，引导换方法；仅凭重复命令不硬停，合法 poll 和迭代优化可能本来就重复。
- 保留已有明确结构错误的硬边界；真正停止时输出可解释证据，而不是只说“检测到循环”。

**验收：**上述间隔失败序列可继续；真正连续重复的参数错误仍被阻止；不同 grep、正常进程 poll、具有产物变化的迭代不误杀。

**实施记录（2026-09-26）：**AgentLoop 现按相邻工具结果计数；成功、参数变化或错误结果变化会打断失败连续计数，参数与结果先归一化再仅保存摘要指纹。连续三次相同的字段校验失败仍以 `loop_detected` 停止，错误说明包含工具、次数、错误码和位置；连续三次相同的成功调用只在工具回执中提示换方法，不硬停。离线回归覆盖间隔失败、变化的参数/错误结果、真实重复失败、重复 poll 和变化的产物；尚未重跑公开 benchmark，因此分数影响待测。

### H8｜P0：超时与内部错误的证据不足，阻碍定位和恢复

**证据：B + 实际数据。**

adapter 当前只在 exec 结束/取消后回收一个 /tmp JSONL 文件，且 [_populate_context](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/harness/morrow_harbor_agent.py:259)仅从 run.completed 生成用量。日志追回是尽力而为；缺少终态时，中途已成功的模型请求用量也未导出成 Harbor 指标。

现有 trace 主要是状态和 text.delta，缺少可比较的工具输入/结果证据。因此 R1/R2 的大量 bash 调用无法判定是否重复；winning-avg 的 internal 也缺少足以定位到具体失败类型的信息。

Morrow 内部已经有 ConversationLog、工具执行记录与 model-request journal，问题是这些证据没有形成可靠、可脱敏导出的 benchmark 诊断链，不能误判为整个项目没有持久化。

**修改方案：**

1. 通过 Harbor 支持的日志路径/挂载或有界增量导出，在运行中保存 trace；finally 回收保留为第二道保障。
2. 从已落盘 request journal 导出部分 usage：已知请求合计、未知请求数、完整性标记。未收到 usage 的失败请求继续标记 unknown，不虚构零用量或费用。
3. 记录请求 ordinal、开始/结束、activity、阶段、工具 execution_id、脱敏命令摘要/指纹、cwd、exit status、artifact 引用、压缩边界。
4. internal 错误导出受控的 error class、phase、correlation id 和原因指纹。不要直接将原始 exception 字符串或 traceback 当作公开诊断。
5. 建立多种取消序列测试：模型流中取消、执行中取消、二次取消、下载失败、容器退出。终态不成功也要能保留此前证据。
6. 导出 Harbor ATIF；它是现有日志的只读投影，不能取代 Session-owned ConversationLog。

**验收：**故障注入测试全部保留最后已提交事件与部分 usage 状态；用脱敏指纹能区分“模型长请求”“长工具”“相同命令重复”“工具快速失败”“内部终结失败”。

**实施记录（2026-09-26）：**`morrow run --diagnostic-log` 现在把有界、逐条刷盘的 JSONL 诊断写入 Harbor agent 日志挂载目录；旧 `/tmp` 运行日志继续在 `finally` 中限时回收。诊断从已落盘的 request observation 与 ToolExecution 读取请求序号/起止时间、状态、活动、压缩边界、工具 execution ID、参数和结果的会话内 HMAC 指纹、命令类别、相对 cwd、退出码及 Artifact ID，不复制提示词、命令正文、结果正文或异常消息。内部错误记录受控类型、阶段、关联 ID 与失败位置指纹，运行时错误日志也只写这些安全字段。Harbor 在缺少 `run.completed` 时导出已知 token 下界、未知请求数与完整性标记；费用未知时保持 unknown。指标汇总将部分用量单列，避免混入完整终态 token 合计。脱敏诊断还投影为 Harbor ATIF `trajectory.json`，原始 ConversationLog 仍是唯一聊天历史写入者。离线故障注入覆盖模型等待/工具运行时的已提交证据、取消、二次取消、日志下载失败、容器异常退出和恶意诊断字段过滤；**尚未在真实 Harbor 超时和 High 评测中验证挂载目录及分数影响**。

### H9｜P1：当前评测缺少足够的版本指纹和预算实耗约束

**证据：B；影响是结论可信度与实验可持续性。**

adapter version 从 wheel 文件名读取，难以唯一标识源码和资产。当前 vendored Harbor 在 commit 71c77fdd… 上还存在一个本地 Docker 凭据传递补丁；本次未修改它。这类必要补丁也必须进入运行指纹。

[TokenBudget](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/harness/budget.py:64)是 admission reservation + 完成后核算。1M/task 不是请求级硬上限；fix-ocaml-gc 所选结果的已知 input 已超过 2.8M。未知 usage 保留 reservation 是合理的保守账本行为，但固定 reservation 不能证明真实消费始终不越界。JSON 原子替换也不等价于多驱动进程并发的事务锁。

**修改方案：**

- 每次运行冻结 source SHA、dirty patch digest、wheel SHA、依赖锁、Python/glibc/架构、Harbor SHA+补丁、任务 checksum、模型部署标识、effort、prompt/tool schema digest、timeout、并发与权限配置。
- 预检只检查执行环境、安装、工具和产物路径，不把隐藏 verifier 内容作为模型输入。
- 明确区分 reservation、known actual、unknown exposure；有预算硬限制的场景，在模型请求接纳处预留下一请求预算并及时结算。
- 账本保持单 writer，或使用带事务/锁的实现；无需引入新的工作流系统。
- 综合 campaign 分、固定版本全量分、诊断子集分分别保存；重跑结果必须保留替换来源，不用过滤失败后的分数宣传能力提升。

**验收：**任意 trial 可还原具体运行配置；同一任务的新 run 不会误复用旧预算键；多驱动并发不会丢账；用量未知时明确显示覆盖缺口。

## 4. 与主流开源 Agent 的基础机制比较

比较对象选取能直接检查实现的 Terminus 2/Harbor、mini-swe-agent、Pi、Hermes。它们代表不同取舍，没有一个项目同时提供所有最优机制；更复杂也不必然更准确。

| 基础环节 | Morrow 当前 | 对照项目的可取做法 | 适合 Morrow 的改进 |
| --- | --- | --- | --- |
| shell 契约 | 简单接口，但存在预检误拒绝、shell 名称与执行端差异 | Pi 显式选择 shell，并描述输出/timeout 语义 | 对齐声明与执行，先修 H1 |
| 长命令 | 前台最多 90 秒，同组后台可能被清理 | Hermes 提供可跟踪后台进程；Terminus 2 使用 tmux 交互 | 补齐任务内进程生命周期，按需要引入 PTY |
| 简单 Agent 循环 | 持久化和恢复层丰富，但策略边界有错配 | mini-swe-agent 的短控制路径便于隔离 Harness 变量 | 用作相同模型的最小对照组 |
| 完成协议 | 无工具最终文本即可结束 | mini 有显式提交动作；Terminus 2 有再次确认完成的反馈 | 添加一次有证据的完成检查，不照搬大段提示词 |
| 上下文 | 已有结构摘要、文件清单和配对安全 | Pi 提供 usage 锚点、近期尾部保留、摘要和文件操作跟踪 | 保留现有结构，补语义降级与验证 |
| 恢复 | 有重试、持久化 intent，invalid-response 边界过粗 | Pi 对重试与压缩分别治理；mini 对格式错误提供有限反馈 | 请求级重试与结构错误反馈分开 |
| 取证 | 内部 journal 丰富，外部失败导出不足 | mini 每步 finally 保存；Harbor ATIF 统一工具/观察/指标 | 从现有 owner 导出增量轨迹和部分指标 |

来源分别为 [Pi shell 实现](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/src/core/tools/bash.ts)、[Hermes terminal 实现](https://github.com/NousResearch/hermes-agent/blob/ef70b3661cbfcf57e583008ad91dd04d8ba46070/tools/terminal_tool.py)、[mini Agent 实现](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/agents/default.py)、[mini 工作协议](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/config/default.yaml)、[Terminus 2 实现](https://github.com/harbor-framework/harbor/blob/71c77fdd119df12eb6ab56e5bc0f29bf62fad338/src/harbor/agents/terminus_2/terminus_2.py)、[Pi compaction](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/src/core/compaction/compaction.ts)、[Pi session](https://github.com/earendil-works/pi/blob/b45597504eeaba1f11a9920a1d1048c361ed4b8e/packages/coding-agent/src/core/agent-session.ts)。

几点取舍需要保留：

- mini-swe-agent 使用独立子 shell，说明“没有持久 shell”本身不构成落后；关键是契约明确，以及目标任务能否完成。
- Pi 也有进程取消和输出截断，不能把无限执行、无限输出当作优化目标。
- Terminus 的二次完成确认提供一次反思机会，但并不自动证明产物正确；Morrow 应进一步利用自己已有的验证事实。
- Morrow 的 durable intent、不可重放未知副作用、单一 ConversationLog 是值得保留的基础。整体换框架可能破坏这些已经建立的边界。

## 5. 可复用开源项目与采用方式

截至本次检索，以下官方仓库均未归档，GitHub 元数据的最近 push 位于 2026-09-21 至 09-24。下面给出实际检查的源码版本，不使用浮动 main 作为实施依据。许可证取自官方仓库元数据，落地复制代码时仍应保留对应版本的许可文件和版权声明。

| 项目 | 检查版本 / 许可证 | 用途 | 推荐采用程度 | 成本与边界 |
| --- | --- | --- | --- | --- |
| [Harbor](https://github.com/harbor-framework/harbor) | 71c77fdd… / Apache-2.0 | 继续承载评测，复用 trajectory models、validator、viewer | **最高：直接复用已有依赖的格式与验证器** | 锁定当前版本兼容的 ATIF；导出前脱敏 |
| [Terminus 2](https://github.com/harbor-framework/harbor/tree/71c77fdd119df12eb6ab56e5bc0f29bf62fad338/src/harbor/agents/terminus_2) | 同 Harbor | 终端会话和完成反馈的参考，对照 Agent | **高：比较机制，按需移植小部件** | tmux 依赖与交互语义有成本；不整体搬入其循环 |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | 04d809ce… / MIT | 最小 Harness 对照，格式错误恢复、每步保存的参考 | **高：作为评测对照与测试素材** | Python 适配方便；它本身不替代 Morrow 的持久化和权限 |
| [Pi](https://github.com/earendil-works/pi) | b4559750… / MIT | shell 契约、压缩、重试的行为参考 | **高：移植行为契约与回归用例** | TypeScript 与 Python 边界不同；现有 Pi 风格机制已不少，避免重做 |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | ef70b366… / MIT | 任务内后台进程、输出轮询、取消与清理 | **中高：参考 process registry，提取最小设计** | 完整 registry 较大，耦合后端和会话；更适合实现薄适配，而非复制整模块 |

Harbor 的 [ATIF 文档](https://docs.harborframework.com/core-concepts/agents/atif)提供模型与 validator，可把当前零散的 Morrow 日志变成可比较轨迹；这是最接近“现成就能用”的改进。先做只读导出，未通过格式校验前不要宣称 ATIF capability。

Hermes 的 [process registry](https://github.com/NousResearch/hermes-agent/blob/ef70b3661cbfcf57e583008ad91dd04d8ba46070/tools/process_registry.py)适合参考进程归属和状态管理，但不建议把其通知、网关和其他后端一并引入。

**不建议以替换整个 Agent 框架作为第一步。** 当前最有价值的复用是 Harbor 取证标准、mini 对照基线、Pi 行为契约、Hermes 进程模式。直接换 Provider SDK、加通用 orchestration 框架，无法自动修复 shell 误拒绝、90 秒限制、错误停止和交付缺口。本文没有推荐新增多 Agent、RAG、长期记忆或浏览器功能。

## 6. 建议实施顺序与代码落点

这是一份建议路线，不激活新的 .agent 计划，不改变现有阶段状态。

| 批次 | 改动 | 主要文件/边界 | 出口条件 |
| --- | --- | --- | --- |
| A：恢复基本执行正确性 | H1、H7；H2 的 schema/上限一致性 | services/process.py、application/local_tools.py、adapters/local/process.py、runtime/agent.py | 两个离线复现问题修复；合法脚本和间隔失败不被误杀 |
| B：让失败可定位、可恢复 | H8、H5、H9 的运行指纹 | runtime/provider_retry.py、runtime/agent.py、interfaces/cli.py、Harbor adapter、现有 journal 投影 | 取消仍有轨迹；工具执行后坏流可安全恢复；运行版本可还原 |
| C：长任务与收尾 | H2 的执行句柄、H3 | services/process.py、进程 adapter、RunPolicy/宿主注入、AgentLoop | 长命令不重复启动；截止前能保存与收尾 |
| D：提升交付准确率 | H4、H6 | application/context.py、prompt.py、现有 validation/outcome 投影 | 验证失败能触发有限修正；压缩后关键事实仍可用 |

实现时继续遵守：

- 普通任务仍走 AgentLoop.run_task。
- Session-owned ConversationLog 仍是唯一聊天历史写入者。
- 进程、验证和上下文状态优先复用已有 execution/journal/artifact owner。
- 运行时 enum 在应用层验证，不为本方案添加 value-set CHECK、触发器或猜测性的表。
- 先在一个逻辑改动中建立测试和证据，再叠加下一个机制；不要一次重写所有运行层。

## 7. 如何证明改进有效

### 7.1 先做不消耗模型预算的契约验证

| 验证组 | 必须覆盖 |
| --- | --- |
| shell | 空参数、quoted heredoc、多行脚本、指定 shell、错误退出、权限分类 |
| 进程 | 前台 timeout、长命令句柄、跨调用服务、取消、重复 poll、重启后的未知状态 |
| Provider | 工具后 invalid stream、部分流丢弃、永久错误、不同时间发生的瞬态故障 |
| deadline | 首块慢、持续 chunk、工具跨截止、压缩跨截止、收尾保留 |
| context | 压缩成功/失败、原目标与验证证据保留、tool pairing、不改写原始历史 |
| finish | 测试失败、验证后再修改、路径/单位错误、服务存活要求、无测试任务 |
| trace | SIGTERM/取消/下载失败/二次取消、已知用量、未知用量、错误类型脱敏 |

使用 ScriptedModelProvider、fake SDK chunks、可控进程和假时钟。通过这些测试只能证明机制正确，不能宣称 benchmark 分数提高。

### 7.2 再进行独立授权的受控评测

本次没有启动任何新任务，原 campaign 中“不再自动重跑”的决定继续有效。下面是将来获得评测授权后的实验设计。

1. **预先固定诊断集。** 覆盖 shell、长编译、服务状态、长模型等待、交付验证、压缩，每类同时包含历史通过和失败样本，避免只挑失败项造成选择偏差。
2. **固定实验条件。** 同一模型部署、effort、任务镜像、prompt、并发、资源、网络与时限；只有被测改动不同。涉及 prompt 变化的实验单列。
3. **比较当前 Morrow、逐项修复 Morrow、mini-swe-agent 或 Terminus 2。** 对照项目也必须使用相同兼容模型和资源，不能直接引用其公开榜单。
4. **记录失败也记录退化。** 配对统计 fail→pass、pass→fail、执行异常、验证失败、超时、用量和耗时；有随机波动时按预算预设重复次数。
5. **全量分独立发布。** 诊断集证明机制后，再做固定版本的 89 项完整评测；不同 timeout/effort/代码版本不混成“修复后单版本通过率”。

建议指标：

| 指标 | 定义/作用 |
| --- | --- |
| 官方 resolution rate | 唯一任务的官方 reward 均值；主指标 |
| 正常终止率 | 执行层是否正常结束；与 reward 分开 |
| 错误终止率 | provider/internal/tool contract/context 分类 |
| 轨迹覆盖率 | 有有效事件、工具观察和终止/部分状态的任务比例 |
| usage 覆盖率 | 已知用量的请求与任务比例；unknown 单列 |
| 最后修改后验证率 | 提交前是否有覆盖当前产物的验证 |
| 命令误拒绝率 | 合法 shell 被 preflight 拒绝的比例 |
| 工具有效时间/模型时间/重试等待 | 判断优化应落在执行、请求还是策略 |
| deadline 前有效交付率 | 外层强制终止前是否完成落盘及可见验收 |
| 压缩后行为退化 | 重复已否定方案、遗失路径/单位/进程等 |

不能把 25 个执行异常剔除后计算的分数称为“真实能力分”，也不能把目前未运行的 SWE-bench Lite 当成代码修改能力证据。

## 8. 本次验证与证据限制

实际完成：

- 重新选择 89 项原始结果，复算 reward、异常矩阵、已知 usage 和 trace/tool error 覆盖。
- 读取运行时、Provider、工具/进程、上下文、Outcome、CLI、benchmark adapter 与相关测试。
- 离线复现合法 shell 被误拒绝。
- 使用 ScriptedModelProvider 离线复现间隔失败被 loop_detected 中止。
- 现有回归测试：17 passed；进程/工具/上下文/Provider 测试：125 passed、1 deselected；benchmark adapter/runner unittest：8 passed。合计 **150 passed，1 deselected**。
- 获取并检查官方开源仓库源码，固定对照版本。

实际测试命令：

~~~bash
uv run --no-sync pytest -q tests/test_long_task_reliability.py tests/test_repeated_failure_stop.py tests/test_benchmark_assets.py
uv run --no-sync pytest -q tests/test_process.py tests/test_local_tool_factories.py tests/test_context_runtime.py tests/test_provider.py
PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q
~~~

测试通过说明已有契约成立，也说明现有测试遗漏了本文的两个复现实例。没有修改生产代码，因此本次未运行完整产品回归、GUI 构建或离线资产重建；未进行真实 Provider 调用、benchmark 重跑或外部发布。

主要限制：

- 15 项所选结果无 trace，28 项 usage 不可用，不能完整归因全部 52 个失败。
- 部分历史日志没有工具参数与结果，无法证明某个任务发生了准确的循环。
- 当前 HEAD 的机制不等于每个历史 trial 实际安装的 wheel，缺少指纹的部分只能结合批次报告判断。
- 配置、环境、模型行为会交互影响结果；单凭一次失败不能精确分摊 Harness 与模型责任。

**最值得先交付的改进包是 H1 + H7 的确定性修复、H5 的请求级恢复和 H8 的可靠取证；随后用 H2/H3 保住长任务的执行时间，再用 H4/H6 提升最终产物的正确性。**
