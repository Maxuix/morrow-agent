# Morrow 新版 Harness Benchmark：适配与执行方案

实施进度与未放行门禁见 [Benchmark v2 实施验收记录](../acceptance/benchmark-v2-2026-09-27.md)。
以下本地实查数据保留为实施前基线，不代表当前 wheel 和收集器状态。

日期：2026-09-27。适用代码基线：`6018258dd3276c37c4a8b8b5f7915eb80064a6fd`。
交付性质：**可实施的评测协议、配置规格、任务清单和操作手册**。本轮未修改执行器／收集器代码，未重建资产，未调用真实模型，未启动新评测。下文 P0 适配完成前，不应按旧快速开始直接发布新分数。

## 1. 结论与推荐组合

**Benchmark 需要调整。重点是资产版本、统计正确性、运行一致性和真实环境验收；不需要扩大 Agent 功能。**

采用“离线契约 → 容器契约 → 3 题冒烟 → 12 题诊断 → 独立 89 题完整运行”的 TB2 主线。正式分只来自最后一个固定版本完整 job，冒烟和诊断结果不补进正式分。保留官方任务、指令和 verifier，不按失败任务调整系统提示词或阈值。

| 评测轨道 | 用途 | 本轮决策 |
|---|---|---|
| 固定版本 Terminal-Bench 2.0，89 题 | 与已有材料建立同数据集的版本桥接，衡量当前整体完成率 | **主线，先实施** |
| Terminal-Bench 2.1 | 后续对齐更新的数据集，减少已知任务问题干扰 | 独立 campaign，不能与 2.0 混分 |
| SWE-bench Lite，300 题 | 验证仓库修复能力 | 当前 runner 有直接阻塞，单独适配后再启用 |
| Harness 合成契约测试 | 验证 deadline、保活、诊断、事实时序等机制 | 前置门禁，不作为 benchmark 分数 |

官方已发布 TB2.1，修正了 TB2.0 中 28 个任务。为判断这次项目改动，先固定本地 TB2.0；如同时升级数据集，分数变化就包含任务变化，不能归因于 Harness。[官方 2.1 说明](https://www.tbench.ai/news/terminal-bench-2-1)

## 2. 本地实查：现在直接开跑会遇到什么

| 优先级 | 证据与问题 | 适配要求 |
|---|---|---|
| P0 | 当前 `assets/morrow_agent-0.1.0-py3-none-any.whl` 与最新源码有 **33 个 Python 文件不一致**；相同版本号不能证明代码相同 | 重建 wheel；逐文件核对包内源码；冻结 SHA256、源码 commit 和构建环境 |
| P0 | `collect_metrics.py` 对目录递归读取所有 `result.json`，会把 job 汇总文件当作 task | 只读取 manifest 对应的 trial 结果；跳过 job 汇总和非 task 记录 |
| P0 | 收集器用异常状态覆盖 resolved；`reward=1 + AgentTimeoutError` 被算为未通过 | verifier 分数与 agent 运行状态分列；官方 reward 不因异常被修改 |
| P0 | 收集器用 `cost or 0`、`tokens or 0` 汇总，未知费用可显示成零；普通 reward 0 默认归为 model_failure | 未知值保留 null；已知下界与覆盖率分列；reward 0 默认仅表示验收未通过，原因没有证据则 unknown |
| P0 | `fixed_version_full` 仅靠 89 个名称唯一记录和 run_id 判定，没有验证集合等于冻结的 89 题、指纹内容一致、reward 完整 | 严格比对 manifest、任务集合、关键指纹和每题唯一 trial；不完整不能标为正式全量分 |
| P0 | 当前账本总额 **300M**，驱动默认 **100M**；参数默认值在读取 `.env` 前计算 | 明确命令行传入总额，或修正参数解析顺序；不得删除旧账本来绕过冲突 |
| P0 | TB2 `--dry-run` 会先实例化预算账本；无效任务检查在 dry-run 之后；预算不足时可只启动 full 的部分任务 | dry-run 必须只读且校验任务；正式 full 须先确认全 89 题可接纳，不得静默退化为子集 |
| P0 | 运行指纹能同时记录“新源码 commit”和“旧 wheel SHA”，却不证明两者对应 | 资产构建清单明确绑定源码和 wheel；运行预检核对，不能只记录两个各自合法的摘要 |
| P1 | H2 服务跨 CLI 退出／verifier 阶段存活、H3 超时收尾、H8 挂载回收尚缺真实容器契约证据 | 增加无真实模型的容器测试，先证明链路能工作，再花模型额度 |
| P1 | 当前非密配置是 `glm-5.3-flash`、High，但 context/output 上限未填写 | 从实际部署确认能力并显式填写；不能凭型号名称猜测，也不能直接沿用模板中的其他模型 |

统计器的最小复现已实际执行：一个 reward=1 且带异常的 trial，加一份 job 汇总 JSON，结果得到 **n_tasks=2、resolution_rate=0、未知 cost 总额=0**。正确值应是 1 个真实 trial、verifier 通过率 1、异常计数 1、总费用未知。P0 中的计分修正具有确定性依据。

主要修改位置：[run_tb2.py](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/run_tb2.py)、[collect_metrics.py](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/collect_metrics.py)、[prepare_assets.sh](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/scripts/prepare_assets.sh)、[Harbor adapter](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/harness/morrow_harbor_agent.py)。

## 3. 冻结协议与已交付配置

[protocol.json](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/config/v2/protocol.json) 保存协议、89 个任务目录 checksum、执行和预算规格。**它是实施规格，现有 `run_tb2.py` 不直接读取此 JSON；不能虚构 `--profile` 参数。** 以下手册使用现有 CLI 已支持的参数。

| 配置项 | 决策 |
|---|---|
| Morrow | 从验收通过的 `6018258d` 演进；适配完成后冻结实际新 commit，并重建资产 |
| TB2 数据集 | `2fd12b88aafdd04a52c298e3940bcb189f9766d6`，本地 89 个任务目录 |
| Harbor | `71c77fdd119df12eb6ab56e5bc0f29bf62fad338`，同时冻结本地补丁摘要 |
| 模型 | 候选为当前已配置 `glm-5.3-flash`；确认实际 endpoint/deployment，记录摘要和服务商可提供的版本信息 |
| reasoning effort | 固定 High；Medium、动态降 effort、2× timeout 均另开实验 |
| context / output 上限 | 部署确认后填写，协议暂为 null，属于开跑前必填项 |
| 工具、prompt、权限 | 实际产品默认能力，权限 manual；记录冻结 schema/prompt/policy 摘要 |
| 平台 | linux/amd64；优先原生 x86_64 Linux，若使用 Apple Silicon/QEMU 必须在报告中标记 |
| 并发 | 2；资源或 API 限流不满足时在正式运行前降为 1，并冻结新配置 |
| 时间 | 每题官方 task.toml，agent multiplier=1.0；内部 deadline 继续由 adapter 计算并预留日志回收时间 |
| 采样 | 每题一次尝试；固定 Harbor `n_attempts=1`、外层 `max_retries=0`；产品内部 Provider 重试保持冻结默认策略 |
| 判题 | 未修改的官方 verifier；内部 `validation_outcome`、`goal_verification` 只作诊断 |

官方 Harbor 区分任务配置、agent 和 verifier；自定义 adapter 应遵守所固定版本的接口。实现时优先使用本地固定版本源码，不能直接套用线上最新 CLI。[Harbor 文档](https://docs.harborframework.com/core-concepts/agents/pre-integrated-agents)

不要把项目 git 工作区中的脏文档与产品修改混为一谈。发布前提交预定修改、记录剩余差异；正式 campaign 期间冻结源码、资产、任务、环境和配置。修改任何影响执行的内容后新建 campaign，不能续写旧分数。

## 4. 阶段、任务清单与放行条件

| 阶段 | 数量与成本 | 目的及放行条件 |
|---|---|---|
| G0 离线门禁 | 无模型调用 | 全部非 Live 回归、benchmark 回归、历史 15 探针和 64 组事实矩阵通过；P0 收集器新测试通过 |
| G1 容器契约 | 6 类合成场景，无真实模型 | 使用脚本 Provider 和中性任务环境，全部基础契约通过 |
| G2 模型冒烟 | 3 trials，预留 3M | 安装／配置／执行／verifier／诊断完整；reward 0 本身不阻塞，新增 harness/environment 故障阻塞 |
| G3 风险诊断 | 12 trials，预留 12M | 对已知风险有可解释证据，不能再出现无法诊断的系统性断链；不要求 12 题全过 |
| G4 正式全量 | 89 个新 trials，预留 89M | 单一固定配置完整运行，独立计分；不复用 G2/G3 的成果或分数 |

**G1 合成场景规格：**

1. 离线安装：Debian 11/glibc 2.31 与代表性任务镜像，`morrow --help`、shell 空参数和 heredoc 可执行；环境和 PATH 不污染 verifier。
2. 长进程：start 返回后能够 poll，不重复启动；task 结束清理、acceptance 服务在 headless CLI 返回及后续 verifier 阶段仍可访问；结束后有明确清理责任。验证 HTTP/文件握手等实际现象，不能只检查 Python 对象。
3. 内部 deadline：脚本 Provider 分别持续输出 activity、摘要挂起；截止前保存工具事实和 run_timeout，诊断能回收。
4. 外部强制中断：使用隔离 fixture 对指定 agent 进程执行终止，验证 mounted 日志保留、usage 为部分／未知且不为零；分别覆盖可捕获取消和不可捕获终止。不得操作其他正在评测的容器。
5. 安全恢复：成功工具后注入断流／无效响应，恢复时工具只执行一次，交付复核提示保留；压缩／降级时保留任务线索。
6. 指纹与统计端到端：故意给出过期 wheel、改变 model 参数、残缺日志、reward=1+exception；分别拒绝错误输入或正确输出覆盖缺口。没有真实任务 oracle 或答案进入 agent 上下文。

**G2：**[smoke-3.txt](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/config/v2/smoke-3.txt)：`fix-git`、`build-cython-ext`、`qemu-startup`。覆盖简单流程、构建和历史环境风险。

**G3：**[diagnostic-12.txt](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/config/v2/diagnostic-12.txt)：

| 风险方向 | 任务 |
|---|---|
| 历史环境／安装问题 | qemu-alpine-ssh |
| 长推理／超时／输出规模 | gpt2-codegolf、large-scale-text-editing、make-doom-for-mips |
| 服务保活与交付 | configure-git-webserver、nginx-request-logging |
| 精确输出和语义正确性 | filter-js-from-html、cancel-async-tasks、financial-document-processor |
| 长工具与恢复 | rstan-to-pystan、fix-ocaml-gc、regex-chess |

这是按风险选择的诊断集，不能称为随机样本、代表性总体分数或证明某个机制一定被触发。真实轨迹确认机制是否发生；未发生时记为未覆盖，不能靠题名推断。

**G4：**[full-89.txt](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/config/v2/full-89.txt)，已核对名称唯一、均存在，并冻结各目录 checksum。正式 job 不只跑“剩余 74 题”。

## 5. 预算与停止规则

本轮只读账本快照：总额 **300,000,000**，charged/reserved **175,290,098**，账面余额 **124,709,902**。这些是账本额度，不是完整实耗；不能用旧的 61/89 usage 总量推断平均成本。

- G2+G3+G4 共 104 trials，按现有 1M reservation 需 **104M** 接纳额度，账面余量约 **20.71M**。这是静态计划，**不保证实际运行不超支**。
- 每阶段结束按已知实耗及未知覆盖重新核算。若进入 G4 前不能预留完整 89M，则停止，不降低 reservation、不提高总额、不删除旧账本。
- `full` 应提供“全量接纳失败则不启动任何任务”的检查；并发多个驱动时须在同一锁内完成整批接纳，不能用无锁前置比较替代原子接纳。
- 正式运行外层不自动重跑任务。系统性环境问题可以中止 campaign，但保留所有尝试与费用；修复后新开完整 campaign。单个普通 reward 0 不触发选择性重跑。
- G3 是诊断额外支出，不能当作 G4 已花费的一部分抵扣 trial 数。若预算不够，可预先取消 G3、明确留下风险，而非缩减全量分母。
- 当前阶段不同时开 SWE 300 题，不以预算 ledger 宣称请求级硬上限。需要严格硬限额时另实现请求入口治理，不能拿任务 reservation 冒充。

## 6. 指标定义：完成率与准确率分别报告

主报告至少给出以下独立列：

| 指标 | 定义与缺失处理 |
|---|---|
| Official verifier score | 官方 reward。89 个 reward 全部存在时报告 `sum(reward)/89`；reward=1 即使带 agent exception 也保持 1 |
| Outcome coverage | 有数值 reward 的唯一任务数／89；缺失者列出，不能删除后改写正式分母 |
| Operational lower bound | 当 reward 缺失时可另报已知成功数／89，但明确是运行口径下界，不是给官方缺失 reward 填 0 |
| Agent execution completion | 运行终态、stop_code、异常、deadline 是否正常收尾；单独统计，不覆盖 verifier 结果 |
| Delivered correctness | 验证通过／失败；与内部 `execution_finished`、`validation_outcome`、`goal_verification` 交叉展示，内部 passed 不当官方成功 |
| Diagnostics coverage | 进入 AgentRun 的 trials 中，拥有 fingerprint、诊断、终态或明确 partial 标记的比例；setup 失败单列 |
| Usage coverage | 完整 usage 数、部分 usage 数、缺失数、已知 token 下界和未知请求数；字段缺失不能显示为真实 0 |
| Cost | 已知费用下界及覆盖率；覆盖不完整时总费用、每成功费用为 null 或明确标为下界 |
| Runtime | agent 时间和端到端 trial 时间分别列 p50/p95，说明是否含 setup/verifier；超时保留实际耗时 |
| Failure taxonomy | harness、provider/network、environment/setup、agent deadline、verifier failure、unknown；仅 reward 0 不自动归因于模型 |

必须生成完整的 89 行任务表，列出 planned/admitted/started/terminal/reward、run_id/trial_id、异常、指纹与日志路径。重复任务和未知任务使正式报告校验失败；不要默默选择最新或最高分。

正式 full 报告有效性要求：任务集合与 manifest 完全一致；恰好一次尝试；campaign／模型／effort／时限／资产关键指纹一致；verifier coverage=89/89。若缺失，则标记 incomplete 并输出证据，不能冒充完整分数。

旧 **37/89=41.57%** 来自跨 job 综合且配置／重跑条件混合，只可作为历史参考。新完整 job 即使数值更高，也不足以声称因果提升。要衡量 Harness 净收益，应另做固定模型部署、相同任务和环境的旧／新版本配对；受当前预算限制不默认开展。模型部署本身变化时也不能归因于 Harness。

## 7. 实施工单与测试要求

| 顺序 | 修改范围 | 交付和验证 |
|---|---|---|
| B1 | `collect_metrics.py` 与测试 | 忽略 job 汇总；reward 与状态分列；严格任务集合和重复检查；未知成本／usage 覆盖；缺失 reward；测试 reward=1+exception 仍计成功 |
| B2 | `run_tb2.py`、预算接纳接口、测试 | 配置先合并再解析默认值；dry-run 零写入；必填项、正整数、有限 multiplier 校验；显式 attempts/retries；full 原子全量接纳；resume 与冻结配置匹配 |
| B3 | `prepare_assets.sh`、预检、指纹 | 按项目要求先构建 GUI 再打包；构建清单绑定源码、wheel、依赖资产；校验所有 wheel 源码一致；冻结 Docker 镜像 digest、主机架构和关键资源；预检拒绝旧资产 |
| B4 | Harbor adapter 与合成 fixture | 完成 G1，修复任何 headless 保活或 deadline/log 回收差异；必要时动产品代码则重走离线验收并重建 wheel |
| B5 | 配置、手册与结果导出 | 确认模型实际容量、冻结 run config；生成唯一 job；验证单 job 报告与原始 reward逐项一致 |

当前已有 TB2 adapter 的 deadline、诊断、部分 usage、ATIF 和指纹可沿用，不需要重写。内部 F4 的 historical 字段以产品事实为准，benchmark 不应另推导“当前验证是否有效”。

离线统计器回归至少加入：job 汇总误入、重复任务、reward 缺失、reward=1+exception、未知费用、只有 partial usage、混合 campaign、指纹字段不同但 run_id 相同。驱动回归至少覆盖 dry-run 不写盘、全量预算不足不启动、显式来源配置及真实 CLI 参数转发。

## 8. 执行手册（B1–B5 放行后）

### 8.1 G0 与资产构建

在项目根目录：

```bash
uv run pytest -m 'not live' -q
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
PYTHONPATH=evals/benchmarks evals/benchmarks/.venv/bin/python -m unittest discover -s evals/benchmarks/tests -q
git diff --check

# 打包前按本项目规定构建 GUI；这些命令可能访问包镜像。
pnpm --dir gui install --frozen-lockfile
pnpm --dir gui typecheck
pnpm --dir gui test
pnpm --dir gui build
bash evals/benchmarks/scripts/prepare_assets.sh
```

资产构建需要 Docker、包镜像和所固定 CPython 归档可访问。先检查现有 uv/uvx 静态二进制完整；脚本并不负责下载所有资产。构建后执行 B3 预检以及 G1。GUI 不进入终端任务评分，但构建产物属于当前项目包的交付要求。

### 8.2 配置

保留现有 `.env` 中凭据，不能打印、复制到报告或命令行。确认现有模型部署后，补全下列变量为真实整数值：

- `MORROW_BENCH_CONTEXT_WINDOW_TOKENS`
- `MORROW_BENCH_MAX_OUTPUT_TOKENS`，应小于 context window，并在当前项目支持范围内。

本地 `.env` 解析器是简单 `KEY=value`；不用 `export` 前缀或依赖 shell 引号语义。以下命令显式传预算、并发和 effort，避免当前 `.env` 默认值解析顺序问题。新 campaign 必须记录这些有效值。

### 8.3 冒烟、诊断与正式运行

以下从 `evals/benchmarks` 目录执行；每阶段检查报告和门禁后再手动进入下一阶段，不能一口气无人值守串跑。

```bash
cd /Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks

# G2：先计划，确认后运行。B2 修复后 dry-run 应无写入。
.venv/bin/python run_tb2.py --tasks fix-git,build-cython-ext,qemu-startup --reasoning-effort high --concurrency 2 --agent-timeout-multiplier 1.0 --budget-total 300000000 --reservation 1000000 --job-name tb2-v2-smoke --dry-run
.venv/bin/python run_tb2.py --tasks fix-git,build-cython-ext,qemu-startup --reasoning-effort high --concurrency 2 --agent-timeout-multiplier 1.0 --budget-total 300000000 --reservation 1000000 --job-name tb2-v2-smoke

# G3：诊断单列，不合并入 full。
.venv/bin/python run_tb2.py --tasks qemu-alpine-ssh,gpt2-codegolf,large-scale-text-editing,make-doom-for-mips,configure-git-webserver,filter-js-from-html,cancel-async-tasks,nginx-request-logging,financial-document-processor,rstan-to-pystan,fix-ocaml-gc,regex-chess --reasoning-effort high --concurrency 2 --agent-timeout-multiplier 1.0 --budget-total 300000000 --reservation 1000000 --job-name tb2-v2-diagnostic

# G4：全量必须新运行所有 89 项，先复核预算与任务集合。
.venv/bin/python run_tb2.py --full --reasoning-effort high --concurrency 2 --agent-timeout-multiplier 1.0 --budget-total 300000000 --reservation 1000000 --job-name tb2-v2-full --dry-run
.venv/bin/python run_tb2.py --full --reasoning-effort high --concurrency 2 --agent-timeout-multiplier 1.0 --budget-total 300000000 --reservation 1000000 --job-name tb2-v2-full
```

`dry-run` 产生的临时 run_id 不是正式运行的 ID。记录真正启动时打印的完整 run_id 和具体 job 目录。模型或任务未通过环境门禁时不启动这些命令；本轮没有执行它们。

### 8.4 中断恢复与收集

以实际 full 运行输出的 32 位 run_id 替换占位符，保持 job-name、全部参数、环境和代码不变：

```bash
.venv/bin/python run_tb2.py --full --reasoning-effort high --concurrency 2 --agent-timeout-multiplier 1.0 --budget-total 300000000 --reservation 1000000 --job-name tb2-v2-full --resume-run-id <实际32位run_id>

# 只指向该次完整 job，不能指向 runs/jobs 或整个 high 目录。
.venv/bin/python collect_metrics.py --tb2-jobs runs/jobs/high/<实际完整job目录名> --output results/<实际完整job目录名>-metrics.json
```

占位符不是可直接粘贴的 shell 参数，应先替换。Harbor 同配置恢复只补没有可用终态的任务；已经失败但有 result 的任务不能在主分中被选择性替换。恢复时 reservation 中未知暴露继续保留，并校验账本与 trial 映射。

监控只读状态与日志，不暂停 Docker 容器来限流，也不清理仍活动的环境。collect_metrics 的既有实现还可能顺带收集 SWE 目录，报告只取指定 `tb2` 段；B1 可增加明确的 suite 选择，避免混入历史附件。

## 9. SWE-bench Lite 的单独适配清单

当前不能直接使用旧 README 的 `--limit 1` 作为可执行验收保证。除 deadline/diagnostics 外还有以下确定性问题：

1. `_install_morrow()` 将 wheel 复制为 `/opt/bench/morrow_agent.whl`，安装时却匹配 `/opt/bench/morrow_agent-*.whl`；应保留有效 wheel 文件名并精确安装。
2. 驱动输出 predictions.csv，本地固定的官方 `get_predictions_from_file()` 只接受 `.json/.jsonl`；改成官方 JSONL schema 并先用官方 loader 离线读取验证。[本地官方解析器](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/vendor/swebench/swebench/harness/utils.py:65)
3. 实例镜像应从固定版本官方 image spec 推导并验证实际可拉取，而非猜测 `ghcr.io/swe-bench/{repo}:{issue}`。镜像中检查 `/testbed` 的 HEAD 等于 `base_commit`，工作区初始干净。
4. SWE `_exec()` 当前用 `-e KEY=value` 将密钥置于 docker argv；应改为 subprocess 的 env 加 `-e KEY`，同时清理错误记录中的敏感材料。不要用真实凭据测试该路径。
5. 将 `--run-timeout-seconds`、`--diagnostic-log`、取消时日志/部分 usage 回收接入 SWE；解析 MORROW_EXIT，不能靠末尾 echo 成功掩盖进程失败。
6. 改成 run_id 独立的 workspaces、实例记录、predictions、官方 eval run_id。当前仅按 effort/instance 跳过历史记录，会在代码或模型变更后复用旧 patch。
7. patch 应相对 `base_commit` 导出，覆盖 staged/unstaged 和允许的新文件；当前普通 `git diff` 会漏掉某些交付，且 runner 未实际验证 checkout。
8. 聚合固定版本官方 report 的真实 schema，不能依赖自造 CSV 或假定存在 results.jsonl；空 patch／失败实例保留在 planned 300 分母中，不能只报告有 patch 的实例。

完成后顺序为：无密钥 runner fixture → 1 实例真实冒烟 → 固定 10 实例诊断 → 独立 300 实例完整运行。新计划另算预算，不用本次 TB2 余额承诺跑完。官方评测依赖 Docker 和指定 predictions 文件，实际接口以已固定的官方源码为准。[SWE-bench 官方评测指南](https://www.swebench.com/SWE-bench/guides/evaluation/)

## 10. 本轮交付与未执行事项

已交付本方案、v2 协议 JSON、3/12/89 任务清单，并进行只读资产／版本／账本检查及统计器合成复现。没有修改生产代码、评测驱动、官方任务或 verifier；未将真实 API key 写入工具输出或交付文件；没有提高预算、预留任务或运行付费模型。

验证结果：三份清单数量、唯一性及任务存在性通过；89 个任务目录 checksum 与协议一致；现有 benchmark 离线测试本轮实际执行 **27 tests，OK**；驱动／收集器 `--help` 及 `git diff --check` 通过。只读快照见 [readiness-audit.json](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/config/v2/readiness-audit.json)，明确标记 `execution_ready=false`，因为上述 P0 工作尚未实施。现有 27 项测试通过不能证明未覆盖的计分问题不存在。

下一项明确可实施工作是 **B1 统计器修正 + B2 驱动预检／全量接纳**，之后 B3 重建资产与 B4 容器门禁。完成这些条件，才能把“源码离线验收通过”转化为“测的是最新版、分数可信、失败可定位”的正式 benchmark。
