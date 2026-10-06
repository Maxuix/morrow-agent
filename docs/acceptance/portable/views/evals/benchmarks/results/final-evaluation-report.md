> Portable reading copy. Original bytes and SHA-256 are preserved in [the evidence manifest](../../../../manifest.json). Links alone are adapted for this repository.

# Morrow 最终评测报告

更新时间：2026-09-24（Asia/Shanghai）

## Terminal-Bench 2.0 High 跨 job 综合结果

| 指标 | 结果 |
|---|---:|
| 官方任务数 | 89 |
| 有 verifier 分数的唯一任务 | 89/89 |
| 官方 verifier reward 1 | 37 |
| 官方 verifier reward 0 | 52 |
| **Resolution Rate（reward 平均值）** | **37/89 = 41.57%** |

合并规则：按官方任务名去重，每项取时间最新且 verifier 已完成并返回数值 reward 的结果；同一任务的其他运行不重复计入，也不挑选最高分。该结果合并了 High pilot、full continuation 和 R1–R4 中各 job 的结果。25 个所选结果附带 agent 执行异常（24 个 `AgentTimeoutError`、1 个 `NonZeroAgentExitCodeError`），但其 verifier 均已完成并给出 reward，因此按官方 verifier 输出计分；异常原因仍单独分类，不据此推断为模型能力失败。没有可用 verifier 分数的执行尝试不覆盖该任务的有效分数。

这是按用户要求跨 job 合并的正式综合 verifier 分；它描述 verifier 观测结果，不等同于排除已知项目缺陷或环境故障后的模型能力归因分。

所选 89 个结果中，61 项有可用 Morrow usage：20,088,380 input + 623,016 output = 20,711,396 tokens；28 项 usage 不可用。当前可获得 token 总数和 provider cost 均为 unavailable。账本统计见下文；账本 charged/reserved 不等于实际模型 usage。

## 结论

- Terminal-Bench 2.0 High 的跨 job 综合 Resolution Rate 为 **41.57%（37/89）**；89 个官方任务均有一个可用 verifier 分数。
- R1–R4 及其已授权重跑均已终态，没有活动或排队中的评测。未解决异常保留在错误报告中，不继续重跑。
- Terminal-Bench 2.0 High 的 64 项全量 job 已结束。官方 verifier 对 62 项给出结果：23 项通过、39 项 reward 0，均值 **37.1%**；2 项 QEMU 任务没有 verifier 分数。
- 对原 20 项异常任务进行的 2× timeout 补跑已结束（11 项 trial 当时标记 completed、9 项异常；后续根因审计将前 11 项归为 9 项断连早退与 2 项崩溃）。没有运行中或排队项。补跑是经过筛选的异常子集，14 项有 verifier 分数且全为 0；未单独用于重算 64 项基线。
- 三项在 2× 仍超时的任务缺少 Morrow 逐轮日志，现有证据无法判断是循环还是长时间推理；不把它们定性为任一原因，也不继续重跑。QEMU 两项重复 exit code 1、无 verifier，按要求不再重跑。四项被监控暂停动作污染的结果不计作模型失败。
- SWE-bench Lite 300 项未启动。当前预算剩余低于 300 项各 1M token 的最低 reservation；既有镜像检查也没有验证通过。

## Terminal-Bench 2.0 — High 全量基线

| 指标 | 结果 |
|---|---:|
| Job | `tb2-high-full-continuation` |
| 目标任务 / 已终态 | 64 / 64 |
| 正常完成 / 异常结束 | 44 / 20 |
| 官方 verifier reward 1 | 23/62（37.1%） |
| 官方 verifier reward 0 | 39/62 |
| 无 verifier 分数 | 2 |
| 异常类型 | 18 `AgentTimeoutError`、2 `NonZeroAgentExitCodeError` |
| Morrow 可用 usage | 13,456,797 input + 433,831 output = 13,890,628 tokens（40/64 项） |

两个无分数项为 `qemu-alpine-ssh` 与 `qemu-startup`。与 High pilot 的 12/25（48%）相比，基线低 10.9 个百分点；任务集合不同，分数按各自 verifier 分母解释。

## 原异常项的 2× 补跑

本节记录原始 trial 状态；下方 2026-09-24 根因补充对其中 11 个表面上 `completed + reward 0` 的 trial 作了进一步归因，应以根因补充作为原因判断。

| 指标 | 结果 |
|---|---:|
| Job | `tb2-high-errors-rerun-2x` |
| High / 并发 / timeout multiplier | High / 2 / 2.0 |
| 任务终态 | 20/20 |
| Trial 当时标记 completed 且 verifier reward 0 | 11 |
| 重复 `AgentTimeoutError`，verifier reward 0 | 3 |
| 无 verifier 的异常项 | 6 |
| 运行中 / 排队 | 0 / 0 |
| 补跑 usage | unavailable；20M reservation 保留在 ledger |

当时标记 completed 且 verifier reward 0 的 11 项为 circuit-fibsqrt、compile-compcert、crack-7z-hash、dna-assembly、gcode-to-text、make-mips-interpreter、model-extraction-relu-logits、path-tracing-reverse、path-tracing、regex-chess、rstan-to-pystan。后续根因审计将这 11 项归为 9 项断连早退与 2 项崩溃，因此不能把该表面状态解释为 11 项有效能力失败。补跑样本由原异常项筛选，非随机样本；不单独用于重新计算全量 High 分数。

### 错误分析与 timeout 判断

- `gpt2-codegolf`、`large-scale-text-editing`、`make-doom-for-mips` 在 2× 下仍分别到达 1,800、2,400、1,800 秒 agent 执行时限。失败 trials 没有保存 Morrow tool/turn 轨迹或 token usage，不能判断循环或长推理。11 个其他原 timeout 任务当时显示 completed 且 verifier 为 0；后续根因审计发现这些结果由 9 项断连早退与 2 项崩溃构成，不能据此认定是长推理，也不能作为模型能力失败证据。
- `qemu-alpine-ssh`、`qemu-startup` 再次以 child exit code 1 结束，约 12 秒，没有 verifier。保留材料不足以定位 exit 的具体触发点；脱敏扫描未发现鉴权、限流、网络特征。
- `pytorch-model-cli`（verifier timeout）、`schemelike-metacircular-eval`（exit 1）、`torch-pipeline-parallelism` 与 `write-compressor`（agent setup timeout）受到此前监控暂停 Docker 容器的影响，结果不用于模型能力归因，也未继续重跑。
- 逐项证据和日志缺失范围见[错误与超时分析报告](tb2-high-errors-analysis-report.md)。

## Morrow usage 与预算

- 跨 job 综合分所选结果中的已知 usage：20,088,380 input + 623,016 output = **20,711,396 tokens**（61/89 项）；28 项 usage 不可用，故这是部分总量。
- 预算上限：300,000,000 tokens；reservation：1,000,000 tokens/task。
- 当前 ledger charged/reserved：175,290,098 tokens；剩余：124,709,902 tokens。此 ledger 数包含保留额度，不代表实际模型 token 用量。
- Provider cost：unavailable。

## SWE-bench Lite — 未运行

300 个实例按 1M/task 至少需要 300,000,000 tokens；按当前账本剩余额度，短缺 **175,290,098 tokens**。既有镜像检查中，本机预期 image refs 为 0/300 缓存；12 个代表性 GHCR manifest 探测均未成功，涉及本机 HTTPS 证书验证失败及 Docker manifest 鉴权拒绝特征。未关闭 TLS 校验、未增加 300M 上限、未降低 reservation，也未启动 SWE-bench Lite。

## 其他未完成项

- TB2 High 的 64 项基线和 20 项异常补跑都没有运行中或排队 trial。全量基线中 2 个 QEMU 项未评分；2× 补跑中 6 个异常项无 verifier，其中 4 个受到监控暂停影响。所有重复超时与错误均保留在错误分析报告中，不再自动重跑。
- 若要完成 SWE-bench Lite，需先解决 GHCR 镜像访问并使现有预算剩余足以安全预留全量 300 项；本报告未擅自调整预算。
- 逐项脱敏退出审计与进度快照分别见 [exit-reasons.jsonl](../../../../raw/evals/benchmarks/results/exit-reasons.jsonl) 和 [progress-monitor.jsonl](../../../../raw/evals/benchmarks/results/progress-monitor.jsonl)。

## 2026-09-24 补充（逐项根因分析后修正）

- 39 项 reward 0 中仅约 13 项可归因于模型能力；其余由 6 个项目侧缺陷（provider 断连即中止、uvx-wrapper 污染 verifier、TaskOutcome artifact-refs 崩溃、wheelhouse 平台缺口、adapter 超时丢日志、workspace 默认 `/`）和一波沙箱出口故障造成。补跑 11 项"正常完成但 0"实为 9 项断连早夭 + 2 项崩溃，非模型能力证据。QEMU 两项 exit 1 根因已查明（glibc 2.31 vs manylinux_2_34 cryptography）。逐项归类与修复/重跑计划见 [tb2-high-failure-rootcause-plan.md](tb2-high-failure-rootcause-plan.md)。
