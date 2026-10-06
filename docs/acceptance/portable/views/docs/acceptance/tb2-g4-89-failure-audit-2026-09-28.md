> Portable reading copy of the pre-closeout staged report. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../manifest.json). Only navigation links are adapted.

# Morrow Terminal-Bench 2.0 v2：G4 全量结果与 28 项异常退出审计

日期：2026-09-28。审计对象：`tb2-full-89-3c641082b680`，Harbor job ID
`a0cf5945-9274-4f65-a117-6ff653917624`。本报告只读分析已完成的 G4 结果；没有改动官方任务、
verifier、冻结配置、原始结果或预算账本，也没有选择性重跑任务。报告中的模型能力以本批运行指纹和
[火山方舟 Agent Plan 官方接入示例](https://docs.volcengine.com/docs/ark/agent-plan-personal-zcode?lang=zh)
为依据；不记录凭据、模型原文、工具参数或原始错误响应。

## 结论与结果边界

| 项目 | G4 结果 |
|---|---:|
| 冻结任务、已知官方 verifier reward | 89 / 89 |
| 官方 reward = 1 | 33 / 89（37.08%） |
| reward = 0、Agent 正常结束 | 28 / 89 |
| reward = 0、Agent 异常退出 | 28 / 89 |
| 异常退出分类 | `run_timeout` 14、`provider_timeout` 8、`context_budget` 5、`invalid_response` 1 |
| 报告有效性检查 | `fixed_version_full`，`validity_errors=[]`，无 G4 verifier 失败 |
| 难度分层 | easy 2/4、medium 27/55、hard 4/30 |

**37.08% 是该冻结配置下可复核的官方分数。**它不能被解释为“已充分利用 256K 上下文时的
Morrow 能力”：运行指纹中的 `context_window_tokens=256000`、`max_output_tokens=239616` 经当前
RunPolicy 计算，只剩 **16,384 token 输入预算**；全批没有一次成功的模型摘要压缩。28 项异常中，
13 项有直接的上下文边界或压缩失败证据，8 项触及 Morrow 自定的单次模型请求 600 秒上限，
6 项触及官方任务期限而未观察到压缩请求失败，1 项是未细分的响应终态错误。这里的“直接证据”
并不等于反事实证明：修复后能多解出多少题，必须由新的完整 89 题运行决定。

当前没有 G4 进程。**修复和重新验证前暂停新的正式 benchmark 批次**；保留 G4 作为这次配置
的历史结果，不用单题重跑替换其中任何 reward。G3 曾有的 verifier 下载问题已有独立修复记录；
G4 的 28 项不是 verifier 下载失败。

## 审计依据与可复现方法

- 汇总：[full-89-metrics.json](../../../raw/evals/benchmarks/results/full-89-metrics.json)；运行目录：
  [原 job 来源清单（完整逐题流仍仅本地保留）](../../../tb2-g4-job-index.json)；
  冻结指纹：[manifest](../../../raw/evals/benchmarks/runs/manifests/3c641082b680436d8d61905d62746685.json)。
- 每题的 `result.json` 给出官方 reward、Harbor exception 和 task agent timeout；
  `agent/morrow-terminal-metrics.json` 与 `agent/morrow-run.jsonl` 给出 Morrow stop code、运行策略和终态；
  `agent/morrow-diagnostics.jsonl` 给出模型请求状态、压缩请求和流式活动。下表的 `trial` 是运行目录下
  的子目录名，可直接定位全部原始证据。审计只提取这些文件的结构化状态、计数和时间，不读取或
  复述模型内容、工具输出和凭据。
- 对照运行时源码提交 `797e8c6a56eacb33d56fe4e493d596279bb3eeec`，与 G4 manifest 的
  `source.commit` 一致。关键实现见 `src/morrow/runtime/policy.py`、
  `src/morrow/application/context.py`、`src/morrow/runtime/agent.py`、
  `src/morrow/adapters/models/openai_compatible.py`、`evals/benchmarks/run_tb2.py`、
  `evals/benchmarks/harness/morrow_harbor_agent.py` 和 `evals/benchmarks/collect_metrics.py`。
- `rstan-to-pystan` 的 `morrow-run.jsonl` 末尾另有 15 行非 JSON 文本；本次按收集器做法跳过
  非 JSON 行，并以独立的 terminal metrics 与 diagnostics 交叉核对其停止类别。该日志问题不改变
  该题的官方 reward，但应改进证据隔离。

## 共同原因：配置与实现如何放大异常

### 1. 256K 名义窗口只提供 16K 输入额度

本批 89 个 trial 指纹均记录 `context_window_tokens=256000`、
`max_output_tokens=239616`、`reasoning_effort=high`。
`RunPolicyResolver.resolve()` 使用 `reserve_tokens=max(预设 reserve, max_output_tokens)`；
`ContextBuilder` 的输入阈值为 `context_window_tokens-reserve_tokens`，即
`256000-239616=16384`。同时 `keep_recent_tokens=20000`，比可用输入预算还大。
Benchmark preflight 只检查输出预留小于总窗口，没有检查扣除预留后能否容纳最近历史、系统消息和工具
schema。这是**项目及评测前检的确定性设计缺陷**，不是 provider 实际只支持 16K 上下文。

火山方舟的 Agent Plan 官方示例给 `glm-5.3-flash` 标注 `context=1024000`、`output=131072`。
本批主动采用 256K 总窗口可以作为保守上限，但 239,616 的单次最大输出配置超过该示例的
131,072；应在新运行前核实实际套餐/部署能力。主 Agent 流式请求当前不发送这个 `max_output_tokens`
字段，然而策略仍将其全部预留，因此输入额度被压缩。不要通过修改 G4 指纹或原报告追改该事实。

### 2. 摘要压缩全批失败，降级只会丢弃旧历史

24 题发起共 **1,599 次**摘要压缩模型请求：1,593 次 `invalid_response`，6 次 `network`，
成功次数 **0**。1,593 次中 1,589 次在 5 秒内失败，且无可用 usage。Morrow 的
`_complete_compaction_summary()` 把 `reserve_tokens*4/5` 作为非流式请求的 `max_tokens`，本批即
**191,692**。这超过上述官方示例的模型最大输出值；请求参数不被部署接受是高度可信的解释。
但当前脱敏日志只保存归一化后的 `invalid_response`，未保存安全的 HTTP 状态码和失败阶段，故不能
断言 1,593 次均为同一种上游 4xx。适配器也可能把响应结构错误归入同一代码。需要一个不计入
正式分数的最小非流式诊断请求确认具体原因。

压缩失败后，现有 `degrade_model_input()` 会丢弃完整的旧轮次/周期继续运行；全批
`dropped_cycle_count=2589`，`cleared_cycle_count=0`，真正成功的 `compaction_count=0`。
`collect_metrics.py` 却把 `dropped_cycle_count+cleared_cycle_count` 命名为
`context_compactions`，因此汇总中的 **2,589“compactions”并非 2,589 次成功压缩**。
这影响诊断结论，虽不改变官方 reward。重复的确定性无效请求还消耗运行时间；例如
`make-mips-interpreter` 单题有 303 次这类压缩失败。

### 3. 八个 `provider_timeout` 是本地 600 秒硬上限

`MODEL_ATTEMPT_MAX_SECONDS=600.0` 作用于每次模型请求；超过时 Morrow 抛出
`ModelAttemptExceeded` 并映射为 `provider_timeout`。八题最后一次请求均耗时约 600 秒、
`retry_count=0`，最后诊断仍有 `reasoning` 流式活动，累计活动采样 125–256 次、chunk
计数 15,872–32,640。**这不是“网络 600 秒无响应”或官方任务 deadline**；模型一直在思考，
只是没有在本地单次上限内结束。各题官方时限为 900–3600 秒，多数在被截断时仍有剩余时间。
能否在剩余时间内给出答案目前未知。保留交互运行的 600 秒默认值可以合理，但有明确 Harbor
截止时间的 benchmark 应由剩余任务时限约束请求，并在指标中将“活跃流超时”与真正的 provider
连接/空闲超时分开。

### 4. 十四个 `run_timeout` 的期限是官方任务时限

官方 task agent 期限为 900、1200、1800 或 3600 秒，协议冻结乘数 1.0。13 题由
Morrow 内部 deadline 结束，Agent 实际运行约 857/1757/3562 秒；Harbor adapter 从官方剩余
期限里保留 15 秒复制日志，再扣除容器启动等开销。`caffe-cifar-10` 是例外：Morrow 已写出
`run.completed`，37 秒后 Harbor 外层仍以 `AgentTimeoutError` 结束，提示包装器结束或日志复制
路径仍需定向排查。其余题没有证据证明官方任务时限设得过短；压缩失败/历史丢弃是其中八题的
明确额外负担，但不能把这八题的全部耗时归因于压缩：
`llm-inference-batching-scheduler` 和 `train-fasttext` 的已记录工具执行时间分别约占 Agent
时长的 78% 和 74%。另外六题还需从已有记录和本地合成复现确认效率瓶颈。
**不得为了提高分数修改官方 timeout。**

### 5. Retry 存在，但覆盖不了这些确定性终止

正式协议有意固定每题一次、Harbor `max_retries=0`，避免重跑挑选分数。Morrow 内部已有按
`ModelFailure.retryable` 分类的有界重试（默认最多 5 次、单失败窗口最多等待 120 秒、运行全程
最多等待 600 秒），可处理暂时网络/限流/超时，且只在安全的提交边界重试。本批
`protein-assembly` 有 3 次网络重试，`train-fasttext` 有 10 次分布在不同窗口的网络重试，
证明机制实际生效。600 秒活跃请求截止、确定性无效压缩参数及无法压缩的本地上下文不适合
原样盲目重试；当前更缺的是**参数校验、失败阶段可观测性和同类确定性错误的去重**。

## 28 项逐题审计

表中“高”表示直接由停止事件和代码路径确定；“中”表示直接停止原因确定，但任务为何耗尽时间
或上游为何拒绝仍有未观测因素。`trial` 与上面的 G4 运行目录拼接即可读取该题证据。方案编号见
下一节；“任务级诊断”指分析已有记录或本地合成复现，不重跑任何官方单题。

### `context_budget`：5 项

| 任务 | trial 后缀 | 个别证据和原因判断 | 最小修复 | 置信度 |
|---|---|---|---|---|
| `distribution-search` | `__gDgD2AQ` | 371/3600 秒、1 次模型请求；无可安全压缩的完整旧边界，0 次摘要请求/丢弃。16K 输入额度小于 20K 最近历史目标，新周期无法装入。 | F1；若仍复现，记录当前周期估算大小与边界类别。 | 中 |
| `gcode-to-text` | `__Bfmd7oC` | 456.6/900 秒、74 次模型请求；25 次摘要请求均 `invalid_response`，39 个周期被丢弃，最后压缩失败且无安全降级空间。 | F1、F2。 | 高 |
| `path-tracing-reverse` | `__wawMDHD` | 1119.7/1800 秒、136 次模型请求；53 次摘要请求失败、74 周期被丢弃，最后达到压缩/降级极限。 | F1、F2。 | 高 |
| `qemu-alpine-ssh` | `__Q57GPPT` | 175/900 秒、9 次模型请求；无安全压缩边界，0 次摘要请求/丢弃，直接触及狭窄输入预算。 | F1；若仍复现，检查单个工具结果的有界投影。 | 中 |
| `video-processing` | `__Lj9MAgt` | 1143.3/3600 秒、51 次模型请求；17 次摘要请求失败、30 周期被丢弃，最后上下文压缩失败。 | F1、F2。 | 高 |

### `provider_timeout`：8 项

下列任务的最后一次请求均有持续 `reasoning` 流，均到本地 600 秒上限，没有证据显示 600 秒
静默断线。表内前一个时间是整个 Agent 运行时长，后一个是官方任务上限。

| 任务 | trial 后缀 | 个别证据和原因判断 | 最小修复 | 置信度 |
|---|---|---|---|---|
| `circuit-fibsqrt` | `__ej9LHAm` | 1163/3600 秒；第 5 次模型请求持续思考至 600 秒，仍有大段官方时间。 | F3。 | 高 |
| `dna-assembly` | `__oBvThhx` | 1078/1800 秒；第 13 次请求持续思考至 600 秒，之前只丢弃 1 个旧周期。 | F3。 | 高 |
| `feal-linear-cryptanalysis` | `__ETxRaAt` | 606/1800 秒；第 2 次请求即触及 600 秒，上下文压缩无关。 | F3。 | 高 |
| `largest-eigenval` | `__SNiHevK` | 844/900 秒；第 12 次请求达到 600 秒，虽有流活动，但距官方截止很近，延长单次上限未必有收益。 | F3；对 900 秒任务以剩余期限约束。 | 高 |
| `model-extraction-relu-logits` | `__wiBWTvA` | 610/900 秒；第 3 次请求达到 600 秒，官方时间仍剩约 290 秒。 | F3。 | 高 |
| `regex-chess` | `__LQJcFoe` | 606/3600 秒；第 3 次请求达到 600 秒，官方时间仍很充足。 | F3。 | 高 |
| `schemelike-metacircular-eval` | `__GjH3Mvz` | 1177/2400 秒；第 10 次请求达到 600 秒，旧历史仅丢弃 4 周期。 | F3。 | 高 |
| `write-compressor` | `__ENBKC6j` | 609/900 秒；第 2 次请求达到 600 秒，官方时间仍剩约 290 秒。 | F3。 | 高 |

### `run_timeout`：14 项

| 任务 | trial 后缀 | 个别证据和原因判断 | 最小修复 | 置信度 |
|---|---|---|---|---|
| `caffe-cifar-10` | `__j8eLUQJ` | 官方 1200 秒外层 `AgentTimeoutError`；30 次模型请求，工具执行累计约 584 秒。Morrow 的终态记录比 Harbor 截止早 37 秒，提示收尾/进程返回路径另有延迟；不能把它归为 verifier 问题。 | F4 定向检查收尾和日志复制；保留官方 1200 秒。 | 中 |
| `cobol-modernization` | `__9x6Bw7H` | 857/900 秒；17 次请求，工具累计仅 4.3 秒，末次 `reasoning` 持续约 408 秒；没有摘要请求失败，仅丢弃 2 周期。长模型思考耗尽时限，尚无独立项目缺陷证据。 | F1 后从已有记录检查早期可验证进度。 | 中 |
| `gpt2-codegolf` | `__ULZVYEm` | 857/900 秒；227 次请求、84 次无效摘要请求、125 周期丢弃，反复压缩失败明显占用任务循环。 | F1、F2；诊断请求数下降后再评估任务效率。 | 高 |
| `llm-inference-batching-scheduler` | `__9ePYZWv` | 1757/1800 秒；85 次请求、23 次无效摘要、38 周期丢弃；工具执行累计约 1366 秒，占 Agent 时长约 78%，任务步骤耗时也是主要因素。 | F1、F2；从已有记录核对耗时工具是否重复。 | 中 |
| `make-doom-for-mips` | `__psGT3E3` | 857/900 秒；250 次请求、106 次无效摘要、132 周期丢弃，严重历史丢弃。 | F1、F2；先消除压缩失败，再看实现耗时。 | 高 |
| `make-mips-interpreter` | `__xrd3PwA` | 1758/1800 秒；694 次请求、303 次无效摘要、369 周期丢弃，是异常循环最集中的任务。 | F1、F2；加相同确定性请求错误的去重门槛。 | 高 |
| `path-tracing` | `__hXK6DpR` | 1757/1800 秒；348 次请求、147 次无效摘要、196 周期丢弃，另有 1 次已按策略重试的网络失败。 | F1、F2；保留已有网络重试。 | 高 |
| `polyglot-rust-c` | `__NFwE5uN` | 857/900 秒；仅 7 次请求、工具累计 1.2 秒、无摘要失败，末次长时间 `reasoning`；更像模型思考耗时而非工具或压缩故障。 | F3 的活跃思考观测；从已有记录检查关键步骤是否过晚。 | 中 |
| `protein-assembly` | `__mYuQZbn` | 1757/1800 秒；37 次请求、3 次网络失败被重试；工具累计约 1313 秒，占约 75%，末次模型请求到官方截止；无无效摘要请求。 | 保留现有有界重试；从已有记录识别重复的长工具等待，F4 补充阶段耗时。 | 中 |
| `pytorch-model-cli` | `__jgXLCCh` | 857/900 秒；40 次请求，26 旧周期被丢弃但无可发出的摘要请求；工具累计约 690 秒、其中 1 次超时，截止时在工具准备阶段。 | F1；从已有记录诊断耗时工具与验证步骤。 | 中 |
| `qemu-startup` | `__TPUSkAN` | 857/900 秒；74 次请求、20 次无效摘要、27 周期丢弃；末尾工具执行失败后触及期限。 | F1、F2；诊断末次工具失败代码和剩余时间。 | 高 |
| `raman-fitting` | `__hPdFj2D` | 857/900 秒；63 次请求、16 次无效摘要、32 周期丢弃；末次模型流仍活跃。 | F1、F2。 | 高 |
| `rstan-to-pystan` | `__9eMPhf3` | 1757/1800 秒；41 次请求、13 旧周期丢弃、无摘要失败；工具累计约 1370 秒、占约 78%，末次文本流到截止。JSONL 尾部 15 行非 JSON，需避免标准输出混入结构化证据。 | F1 后从已有记录查长工具调用；F4 隔离日志。 | 中 |
| `train-fasttext` | `__KLUxS8b` | 3562/3600 秒；75 次请求、9 次无效摘要与 5 次摘要网络失败、29 周期丢弃；另有 5 次普通请求网络失败，合计 10 次重试。工具累计约 2624 秒，占约 74%；任务计算、网络波动与压缩失败共同消耗时间。 | F1、F2；复用已有有界网络重试，F4 记录各阶段等待。 | 中 |

### `invalid_response`：1 项

| 任务 | trial 后缀 | 个别证据和原因判断 | 最小修复 | 置信度 |
|---|---|---|---|---|
| `password-recovery` | `__tKnzcbw` | 42.9/900 秒；7 次普通模型请求均标记完成，最后一次有工具调用活动；事件在 `tool_preparing` 后以“模型响应未正常结束”收尾，没有最后工具执行记录。故障发生在模型响应完成之后、工具执行之前；日志不足以分清 finish reason、工具调用结构还是对话提交。 | F4 增加脱敏结构化子原因与阶段；只对未提交的可修复结构错误使用现有有界重试。 | 中 |

## 最小修复方案与验证次序

| 编号/优先级 | 改动边界 | 具体方案与验收标准 |
|---|---|---|
| F1 / P0 | 模型能力与 RunPolicy、benchmark preflight | 将“模型可支持最大输出”和“单次调用实际预留输出”分开；先用官方部署上限校验配置，本次 `239616` 不应通过 `glm-5.3-flash` 的 131072 示例上限。给任务调用选择可解释的输出预留，例如 16K–32K 起步，并保证 `context_window-reserve` 明显大于 `keep_recent_tokens` 加系统/工具开销。沿用现有 Pydantic 校验与 RunPolicy，不新建预算子系统。新指纹必须记录实际输入额度、输出上限和选定预留。 |
| F2 / P0 | 现有 ContextBuilder、OpenAI 兼容适配器 | 摘要 JSON 属短输出：独立限制压缩 `max_tokens`，建议先按 schema 预算做 4K–8K 的诊断值，并在首次正式运行前用当前部署做一次不计分的非流式压缩闭环。失败诊断只记录阶段、HTTP 状态类别、错误代码和请求上限，不记录响应正文。先修复参数与摘要解析，再考虑对同一配置的确定性 4xx 去重；不得对 1,593 次同样的请求盲目增加 retry。验收为至少一条真实摘要成功、`compaction_count>0`，并且没有摘要内容/凭据进入日志。 |
| F3 / P1 | AgentLoop 单次模型请求 deadline | 对有 Harbor 截止时间的运行，将单次请求界限与 `deadline.remaining` 关联，保留合理的收尾余量；600 秒继续作为无外层 deadline 的默认保护值或可配置上限。指标分开记录活跃 `reasoning` 流被本地截断、连接超时和空闲超时。保留用户指定的 `high` 思考强度，且不改官方 task timeout。用脚本 Provider 的持续活动流做非 Live 回归，再用独立的新正式批次测最终 reward。 |
| F4 / P1 | runner/collector 可观测性 | 为终态添加安全的 `cause_phase`/`cause_code`；记录模型最后活动类别、内部与官方剩余时限，区分 `summary_success_count`、`dropped_cycle_count` 与 `cleared_cycle_count`。对 `caffe-cifar-10` 的终态后 37 秒延迟和 `rstan-to-pystan` 的非 JSON 尾行做定向日志/进程检查。`password-recovery` 的结构错误只在确认未提交工具意图时允许现有有界重试。沿用 Harbor `result.json` 和现有 diagnostics，不引入第二套 runner 或日志系统。 |
| F5 / P0 | 预算账本与结果表述 | `TokenBudget` 是按任务接纳的软账本，不是逐请求硬上限。45 个 G4 task 的 terminal usage 为 `unavailable`，但各有 partial metrics；`_finalize_from_job_logs()` 只在 `not usage` 时读 partial，因而账本给它们每题记 1M，未用已知部分用量。应在 terminal usage 不完整时合并 partial metrics，再经现有锁与 `finalize()` 原子核账；保留原账本快照作审计，不静默覆盖。收集器把 2,589 次“丢弃”从 `context_compactions` 改名或拆列。 |

修复顺序是 F1→F2→F5→F4 的脱敏验证→F3。先用本地假 SDK/脚本 Provider 覆盖容量前检、
摘要成功/确定性拒绝、持续流 600 秒边界的逻辑与账本合并；在协议允许的独立非计分诊断里验证
当前部署的摘要请求。只有容量、压缩和结果采集门槛通过后，创建**新的完整 89 题、一次/题**
正式批次。不得改旧 G4 的 reward 或只重跑异常 28 题拼接分数。

## 预算与解释限制

G4 账本 89 项均已 finalized，当前记录的 G4 charge 为 **54,407,550 token**；这是软接纳账本的
记账值，不是实际总消耗。收集器按 terminal usage 或 partial request usage 得到的**已知下界**为
**59,717,034 token**，另有 **1,647 个用量未知请求**；其中 45 个 terminal usage 不可用任务的
partial 已知部分合计 **50,309,484 token**，但账本按每题 1M 共记 **45,000,000 token**。
因此账本 charge 比已知下界至少低 **5,309,484 token**，不能当作完整实际用量；
`cost_coverage=0/89`，也不能凭现有数据给出精确费用。总预算已由用户提高至
500M，但它不改变上述账本精度和软上限性质。

本审计没有读取原始上游错误正文，也没有发起付费 Live 复现，因此摘要请求到底是 4xx 参数错误、
结构解析错误或两者叠加，仍需 F2 的脱敏诊断闭环确认。六个没有摘要请求失败的 `run_timeout`
仅能确定在官方任务期限内未完成，不能无证据地归咎于 Morrow、模型质量或官方任务设计。
