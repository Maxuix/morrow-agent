> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../../manifest.json). Links alone are adapted for this repository.

# Terminal-Bench 2.0 High：错误与超时分析

更新时间：2026-09-23 20:36（Asia/Shanghai）

## 结论

- 原 High 全量 job `tb2-high-full-continuation` 的 64 项均已结束：44 项正常完成，20 项异常结束。官方 verifier 对 62 项有结果：23 项 reward 1、39 项 reward 0；2 项没有 verifier 结果。
- 对原 20 项异常进行的 `tb2-high-errors-rerun-2x` 也已结束：11 项正常完成、9 项异常结束；没有运行中或排队项。11 项正常完成以及 3 项仍超时的任务均为 verifier reward 0；其余 6 项没有 verifier 结果。
- 三项重复超时都恰好耗尽 2× agent 执行时限，但留存结果没有 Morrow 逐轮日志、token 用量或工具调用轨迹。现有证据不能判断它们是长时间 LLM 推理还是循环，因此不把它们定性为任一原因，也不继续重跑。
- 两个 QEMU 子进程重复以 exit code 1 退出，没有 verifier 结果；保存的材料不能定位更具体根因。按要求不再重跑。
- 四项补跑结果受此前监控暂停容器影响，单独标作受干扰的环境结果，不计作模型失败。
- 全量 High 与这 20 项补跑均无运行中或排队项。SWE-bench Lite 未启动：当前剩余预算无法覆盖 300 项各 1M token 的最低 reservation。

## 全量 High 基线

| 指标 | 结果 |
|---|---:|
| Job | `tb2-high-full-continuation` |
| Trials | 64/64 已进入终态 |
| 正常完成 / 异常结束 | 44 / 20 |
| Verifier reward 1 / 0 | 23 / 39（共 62 项有分数） |
| 无 verifier 结果 | 2（`qemu-alpine-ssh`、`qemu-startup`） |
| 异常类型 | 18 `AgentTimeoutError`、2 `NonZeroAgentExitCodeError` |

官方分数只按这 64 项基线统计。困难异常子集的补跑结果不与基线重复合并。

## 20 项异常子集补跑

Job `tb2-high-errors-rerun-2x` 使用 High、并发 2、timeout multiplier 2.0。最终状态如下：

| 结果 | 数量 | 任务 / 说明 |
|---|---:|---|
| 正常完成，verifier reward 0 | 11 | circuit-fibsqrt、compile-compcert、crack-7z-hash、dna-assembly、gcode-to-text、make-mips-interpreter、model-extraction-relu-logits、path-tracing-reverse、path-tracing、regex-chess、rstan-to-pystan |
| 再次 `AgentTimeoutError`，verifier reward 0 | 3 | gpt2-codegolf、large-scale-text-editing、make-doom-for-mips |
| `NonZeroAgentExitCodeError`，无 verifier | 3 | qemu-alpine-ssh、qemu-startup、schemelike-metacircular-eval；三项 child exit code 均为 1。前两项不是暂停监控任务，后者受暂停动作影响 |
| `VerifierTimeoutError`，无 verifier | 1 | pytorch-model-cli；监控曾暂停其容器，不能归因到模型 |
| `AgentSetupTimeoutError`，无 verifier | 2 | torch-pipeline-parallelism、write-compressor；均发生在监控暂停容器期间 |

因此，补跑有 verifier 结果的 14 项均为 reward 0；其余 6 项不计分。此子集由原异常项筛选，不能当作随机样本，也不能替代 64 项基线。

## 超时判断与证据边界

三项重复超时的 `agent_execution` 时长恰好到达 2× 配置限额：

| 任务 | 原 job 执行时长 | 2× 补跑执行时长 | 2×结果 |
|---|---:|---:|---|
| gpt2-codegolf | 900 秒 | 1,800 秒 | `AgentTimeoutError` |
| large-scale-text-editing | 1,200 秒 | 2,400 秒 | `AgentTimeoutError` |
| make-doom-for-mips | 900 秒 | 1,800 秒 | `AgentTimeoutError` |

时长证明 Harbor 到达了 agent 执行期限，但不能单独证明期限内发生了什么。异常 trials 没有保存 `agent/morrow-run.jsonl` 或 `morrow-terminal-metrics.json`，所以无法检查模型响应间隔、重复工具动作、token 增量或循环。仓库中的 Harbor adapter 仅在 `environment.exec` 返回后才把 `/tmp/morrow-run.jsonl` 复制到 trial；发生 agent timeout 时没有这份轨迹。脱敏扫描也没有发现 API 鉴权、429 限流或网络错误标记。故三项仍属**原因未定**，没有证据将其归类为循环或长思考。

原 18 项 timeout 中，11 项在 2× job 正常结束，但 verifier 都为 0；这说明延长时限帮助它们走到 verifier，不证明它们只是思考较慢。按当前授权，不对三项重复 timeout 再加倍重跑。

## 非零退出和受监控影响项

- `qemu-alpine-ssh`、`qemu-startup`：原 job 与 2× 补跑都报 `NonZeroAgentExitCodeError`，child exit code 1；耗时约 12 秒，无 verifier 结果。留存的 trial 没有 agent 输出日志，安全扫描没有鉴权、限流或网络特征，根因仍未定位；不继续重跑。
- `pytorch-model-cli`、`schemelike-metacircular-eval`、`torch-pipeline-parallelism`、`write-compressor`：此前异常阈值监控通过 Docker pause 冻结了这些任务的容器，后续分别出现 verifier timeout、exit 1、agent setup timeout、agent setup timeout。这些结果受到监控动作污染，不能归因于模型或项目；根据“报错项不继续重跑”的要求，保留为未评分项。
- 除明确记录的容器暂停影响外，没有证据将上述异常归咎于 Volcengine 鉴权、API 限流或网络连接。

## 预算与 Morrow usage

- 硬上限：300,000,000 tokens；reservation：1,000,000 tokens/task。
- 当前 ledger charged/reserved：146,444,263；剩余：153,555,737。
- 2× 补跑的 20 项仍按 20,000,000 保守占额。十项正常完成记录的 usage 标记为 unavailable，另十项未取得可用 usage；故本 job 没有可报告的精确 token 总数。
- 原 64 项 full job 的可用 Morrow usage 为 13,456,797 input + 433,831 output = 13,890,628 tokens（40/64 项）；High pilot 报告记录 3,501,220 tokens（16/25 项）。两组可用 usage 合计 17,391,848 tokens，**不含**本次补跑未知用量。预算 ledger 中的 charged/reserved 是受 reservation 保护的额度，不等于实际模型 usage。

## 文件与后续边界

- 逐项脱敏结果：`exit-reasons.jsonl`。
- 状态快照：`progress-monitor.jsonl`。
- 汇总报告：`final-evaluation-report.md`。
- 按 1M/task 完成 SWE-bench Lite 300 项至少需 300,000,000 tokens；当前剩余 153,555,737，短缺 146,444,263。之前镜像检查也未能验证 GHCR 镜像可达性。未提高 300M 上限、未降低 reservation、未启动 SWE-bench Lite。

## 2026-09-24 补充（逐项根因分析后修正）

- "11 项正常完成但 verifier 均为 0" 的框架需修正：逐项日志显示其中 9 项在 46 秒–58 分钟死于 `provider_network` 断连（3 次重试约 14 秒后整个 run 中止），2 项（gcode-to-text、path-tracing）死于 TaskOutcome artifact-refs 未处理 `ValidationError` 崩溃。交付物均未写出，**不能作为模型能力或"2× 时限足够"的证据**。补跑全 20 项 reward 0 的主因是覆盖该 job 的沙箱出口故障（代理 503 / 镜像 SSL EOF）。
- QEMU 两项 exit code 1 的根因已查明：wheelhouse 仅含 manylinux_2_34 的 cryptography，而任务镜像为 Debian 11（glibc 2.31），pip 无候选导致安装失败；非模型问题。
- 完整逐项归类、项目侧缺陷清单（D1–D8）与修复/重跑计划见 [tb2-high-failure-rootcause-plan.md](tb2-high-failure-rootcause-plan.md)。
