# TB2 High 失败根因分析与下一步计划

日期：2026-09-24（Asia/Shanghai）

本文对 `tb2-high-full-continuation`（64 项基线）与 `tb2-high-errors-rerun-2x`（20 项补跑）的全部失败 trial 做逐项根因归类，并给出"修项目 vs 重跑"的判定。证据来源：各 trial 的 `agent/morrow-run.jsonl`、`agent/morrow-terminal-metrics.json`、`verifier/test-stdout.txt`、`exception.txt` 及仓库源码核对。

## 总体判定

**先修项目，再有选择地重跑。** 39 项 verifier 0 中，只有约 13 项可归因于模型能力；其余 26 项失败（及 2 项无分数）由 6 个项目侧缺陷和一波网络中断造成。不重跑纯能力失败项（烧预算换方差）；修复后优先重跑"从未被有效测量"的项。

## 对既有报告的两处修正

1. 补跑报告称"11 项正常完成但 verifier 均为 0"。逐项日志显示这 11 项**并非完整尝试后答错**：9 项在 46 秒–58 分钟处死于 `provider_network`（3 次重试约 14 秒后整个 run 中止），2 项（gcode-to-text、path-tracing）死于 TaskOutcome artifact-refs 崩溃。交付物均未写出。它们不能作为模型能力证据，也不能作为"2× 时限足够"的证据。
2. 补跑 job 全 20 项 reward 0 的主因是一波覆盖整个 job 的沙箱出口故障（代理 198.18.9.229 返回 503、对腾讯 PyPI 镜像 SSL EOF），不是任务本身难度。

## 基线 39 项 reward 0 的归类

### A. 正常完成但失败（22 项）

| 类别 | 数量 | 任务 |
|---|---:|---|
| capability:wrong-solution | 11 | build-pov-ray、cancel-async-tasks、configure-git-webserver、dna-insert、filter-js-from-html、install-windows-3.11、mteb-retrieve、protein-assembly、query-optimize、raman-fitting、torch-tensor-parallelism |
| capability:misunderstood（实为 D6 工作区陷阱） | 1 | extract-elf（相对路径写到了 `/` 而非 `/app`） |
| harness:env-issue | 6 | fix-ocaml-gc、mailman、multi-source-data-merger、reshard-c4-data、sam-cell-seg、winning-avg-corewars |
| harness:agent-bug | 2 | password-recovery（单次 invalid_response 即中止）、video-processing（compaction 失败即中止，109 轮） |
| verifier:strict | 2 | nginx-request-logging（`status=200` vs 正则 `\s(\d{3})\s`，7/8 通过）、polyglot-c-py（instruction 自带 gcc 示例残留的 `cmain`） |

注：query-optimize（输出正确、慢黄金解 25% 未达 1.05× 门槛）与 raman-fitting（nm/cm⁻¹ 单位）属"差一点"的能力失败；configure-git-webserver 是验证通过后主动拆掉了可用状态（"for cleanliness"）。

### B. 超时 17 项

- 11 项补跑"正常完成"：实为 9 项 provider 断连早夭 + 2 项 artifact-refs 崩溃（见上）。
- 3 项重复超时（gpt2-codegolf、large-scale-text-editing、make-doom-for-mips）：仍无逐轮日志，原因未定（见 D5）。
- 3 项受监控暂停污染（schemelike-metacircular-eval、torch-pipeline-parallelism、write-compressor）+ 1 项 pytorch-model-cli（暂停 + 断连 + verifier 超时）：从未被有效测量。
- 1 项 circuit-fibsqrt 基线超时但 verifier reward 1（被杀前已完成关键工作），已在 23 项通过数内。

### C. 无分数 2 项（QEMU）

根因已完全查明（见 D4），非模型问题。

## 项目侧缺陷清单（按分数影响排序）

| # | 缺陷 | 证据 | 影响 |
|---|---|---|---|
| D1 | 瞬态故障直接杀死整个 run：provider 网络错误仅重试 3 次（2s/4s/8s，`src/morrow/core/runtime_policy.py:27-29`）即 `stop_code=provider_network` 中止；单次 `invalid_response` 不重试（password-recovery，第 16 轮）；compaction 摘要调用失败包装为 `ContextBudgetError` 中止（`src/morrow/runtime/agent.py:743`，video-processing 109 轮作废） | 10+ trials 的 `error` 事件、补跑成对 trial 同一秒死亡 | ≥12 trials，最大分数杠杆 |
| D2 | `uvx-wrapper` 被 symlink 到 `/usr/local/bin/uvx`、`/root/.local/bin/{uv,uvx}`（`harness/morrow_harbor_agent.py:149-163`），污染 verifier 工具链；参数解析错误：`uv venv`→`exec venv`、`uvx --index URL`→把 URL 当命令执行、选中无 pip 的 python3.13 | mailman、multi-source-data-merger、sam-cell-seg、winning-avg-corewars 的 verifier 在 pytest 启动前崩溃（无 ctrf.json） | ≥4 trials 的 verifier 从未评分 |
| D3 | TaskOutcome 组装时 artifact refs 超过 `TASK_OUTCOME_ARTIFACT_MAX_REFS=64`（`src/morrow/core/domain.py:61,333`；未截断的合并在 `src/morrow/application/tasks.py:199-207`）→ 未处理 `ValidationError`，headless run 崩溃 exit 2，metrics 丢失，原始 traceback 写入 JSONL | build-pov-ray、gcode-to-text、path-tracing 日志尾部 | 3 trials（长 run 必现） |
| D4 | wheelhouse 仅含 manylinux_2_34 的 cryptography 50.0.1（由 `mcp` 的 `pyjwt[crypto]` 传递引入）；QEMU 任务镜像为 Debian 11（glibc 2.31），pip 无候选 → 安装失败 exit 1，约 12 秒 | 两 trial `exception.txt`；镜像实测 `ldd 2.31` | 2 trials 无分数 |
| D5 | Harbor adapter 仅在 `environment.exec` 返回后复制 `/tmp/morrow-run.jsonl`（`harness/morrow_harbor_agent.py:223-228`）；Harbor 用 `asyncio.wait_for` 取消 run 后日志永远丢失，超时 trial 无任何逐轮证据 | 3 项重复超时 trial 无 `morrow-run.jsonl` | 阻塞所有超时诊断 |
| D6 | adapter 默认 `--workspace-dir /`，TB2 任务 WORKDIR 为 `/app`；相对路径写入落到 `/` | extract-elf：自验 698 条目通过，verifier 找不到 `/app/extract.js` | ≥1 trial，潜在更多 |
| D7 | fix-ocaml-gc 的 morrow 进程在 960.0s 被 SIGTERM（MORROW_EXIT=143），任务时限为 3600s，来源未查明；其 verifier 自身 `git clone` 也失败 | trial 日志、verifier stdout | 1 trial，待查 |
| D8 | 后台进程在 bash 工具调用之间不存活（容器 exec 模型），多个 agent 自行发现并用 setsid 绕过；bench 提示词未说明 | 两个任务的 agent 自述 | 效率/行为噪声 |

环境侧（非项目缺陷但需处理）：补跑期间沙箱代理出口故障（503/SSL EOF）波及 verifier 的 apt/pip/HF 下载（reshard-c4-data、sam-cell-seg、fix-ocaml-gc、path-tracing 等）。D1 修复后，同类瞬态故障对 agent 侧的影响会大幅降低；verifier 侧只能依赖网络稳定窗口。

## 不需要修项目的部分

- 11 项 capability:wrong-solution：模型完成并自行停止，答案质量不足。configure-git-webserver（自毁）、cancel-async-tasks/filter-js（自我验证结论被合理化掉）暴露的是模型行为问题，可作后续提示词/流程改进素材，本轮不动代码。
- 2 项 verifier:strict：非缺陷；nginx 项模型本可自查格式，polyglot 项模型本应清理编译残留。

## 下一步计划

### Phase 1 — 项目修复（全部在本仓库，建议按依赖顺序）

1. **D1 韧性策略**（`runtime_policy.py`、`runtime/agent.py` 等）：headless/bench 场景下 provider 网络错误采用有界指数退避、分钟级总窗口而非约 14 秒放弃；`invalid_response` 可重试；compaction 失败降级为截断而非中止 run。附回归测试。
2. **D3 artifact-refs 截断**：组装 TaskOutcome 时对 refs 去重后截断至上限（保留最近/高优先级），不再让 ValidationError 逃逸到 headless 终态。附回归测试。
3. **D2 uvx-wrapper**：不再向全局 PATH symlink（或最小化为仅 agent exec 环境内可用）；如保留 wrapper，修复 flag 值跳过逻辑并保证选中带 pip 的解释器。
4. **D4 wheelhouse**：`prepare_assets.sh` 增加 `--platform manylinux_2_28_x86_64`（仅二进制）下载通道，重建 wheelhouse；在 `debian:11` 容器内实测 `pip install --no-index` 成功。
5. **D5 adapter 日志抢救**：`run()` 加 try/finally，取消路径上用 shield + 短超时尽力取回 `/tmp/morrow-run.jsonl` 并写 metrics；同时记录 `MORROW_EXIT`。
6. **D6 workspace**：TB2 运行配置将 `--workspace-dir` 设为 `/app`（adapter 默认值或 `run_tb2.py` 传入）。
7. D7（960s SIGTERM）保持观察，D5 落地后重跑即可定位；D8 可在 bench 模式提示词补一句后台进程说明。

验证门槛（AGENTS.md）：`uv run pytest -m 'not live'`、`ruff format --check`、`ruff check`、`compileall`；改动处附回归测试；D4 附容器实测记录。

### Phase 2 — 有选择重跑（修复落地后，按性价比排序）

| 批次 | 任务 | 前置修复 | 目的 | reservation |
|---|---|---|---|---:|
| R1 | qemu-alpine-ssh、qemu-startup | D4 | 从"无分数"变为可评分 | 2M |
| R2 | gpt2-codegolf、large-scale-text-editing、make-doom-for-mips | D5、D1 | 获得逐轮证据，判定循环 vs 长推理 | 3M |
| R3 | 补跑被 outage/崩溃杀死的 11 项 + 受污染 4 项（pytorch-model-cli、schemelike-metacircular-eval、torch-pipeline-parallelism、write-compressor） | D1、D3 | 首次有效测量 | 15M |
| R4 | mailman、multi-source-data-merger、sam-cell-seg、reshard-c4-data、winning-avg-corewars、fix-ocaml-gc | D2、D1 | verifier 首次真正评分 | 6M |
| R5（可选） | 全量 64 项干净重跑作为修复后官方基线 | 全部 | 避免合并偏差，给出可辩护分数 | 64M |

R1–R4 合计约 26M reservation，当前剩余 153.6M 可覆盖；R5 也在预算内。**不重跑**：11 项纯能力失败 + 2 项 verifier:strict（除非执行 R5 全量）。重跑前确认沙箱代理出口稳定（补跑 job 的失败窗口由 outage 造成）。修复后批次不与原 37.1% 基线合并统计；官方分数只认同一 job 的全量结果。

### 预算现状

ledger charged/reserved：146,444,263 / 300,000,000；剩余 153,555,737。SWE-bench Lite 仍需 300M 最低 reservation 且 GHCR 镜像可达性未验证，维持不启动。
