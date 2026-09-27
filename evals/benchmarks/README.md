# Morrow Benchmark Harness

> **v2 执行入口：**使用[新版实施与执行方案](/Users/ruirui/Documents/Project/Agent/developing/docs/research/benchmark-v2-implementation-plan-2026-09-27.md)及 [v2 协议规格](/Users/ruirui/Documents/Project/Agent/developing/evals/benchmarks/config/v2/protocol.json)。协议 JSON 是冻结规格，不是 CLI `--profile` 输入。先完成离线和容器门禁并确认模型容量，再按 3 → 12 → 独立 89 题执行。SWE Lite 暂不纳入主线。

面向 Morrow（承序）的自动化评测 harness，覆盖 **Terminal-Bench 2.0**（89 任务，Harbor 官方 harness + 官方 verifier）与 **SWE-bench Lite**（300 任务，官方 harness 评分）。TB2 驱动默认使用 300M token 任务接纳账本，须与已有账本一致；运行中的请求仍可能超出任务预留。

> 报告与简历必须标注 **SWE-bench Lite**（300），不得写成 Verified（500）。

## 目录

```
config/
  pilot-tasks.txt      # TB2 pilot 25 任务（按难度/类别分层抽样）
  env.template         # 运行环境变量模板（复制为 .env 填写）
harness/
  budget.py            # TokenBudget 预算账本（admission/finalize/refused）
  morrow_harbor_agent.py  # Harbor installed-agent：容器内安装并运行 Morrow
  bench_setup.py       # 容器内一次性引导（workspace/provider/model，不落密钥）
  swe_lite_runner.py   # SWE-bench Lite 逐实例 runner（官方镜像内运行 Morrow）
run_tb2.py             # TB2 驱动：pilot(25) / full(89) / 子集
run_swebench_lite.py   # SWE-bench Lite 驱动：patch 生成 + 官方评分
collect_metrics.py     # 指标聚合（分辨率/分层/p50p95/token/成本/失败分类）
scripts/prepare_assets.sh  # 离线资产包构建（wheel + python-build + wheelhouse）
vendor/                # harbor / terminal-bench-2 / swebench / swebench-lite（gitignored）
assets/                # 离线安装资产（gitignored）
runs/                  # 运行产物（job 结果、patch、预算账本，gitignored）
results/               # 指标报告（gitignored）
```

## 工作原理

**TB2**：`run_tb2.py` 调官方 Harbor CLI（`harbor run -p vendor/terminal-bench-2`），
agent 为自定义 installed-agent `harness.morrow_harbor_agent:MorrowAgent`：
setup 阶段把离线资产（python-build-standalone 3.12 + Morrow wheel + x86_64 wheelhouse）
上传进任务容器并完成安装；run 阶段使用任务声明的 workdir（若未声明则读取容器
`pwd`）执行 `morrow run`。`uv` / `uvx` 只进入 agent 进程的 PATH，不修改容器
全局工具链。运行中将脱敏诊断持续写入 Harbor agent 日志目录，并在结束或取消时
尽力回收原始 JSONL；有 `run.completed` 时解析终端指标，缺失时从已落盘的请求诊断
汇总已知用量并标明未知请求，不把未知量记作零。脱敏诊断还生成 ATIF `trajectory.json`。
任务成败只由每个任务自带的官方 verifier（`tests/` + task.toml）判定，harness 不做任何自定义解释。

**SWE-bench Lite**：`run_swebench_lite.py` 逐实例启动官方实例镜像
（`ghcr.io/swe-bench/{repo}:{version}`，可用 `MORROW_BENCH_SWE_IMAGE_TEMPLATE` 换镜像站），
在同一容器内离线安装 Morrow、以 `problem_statement` 为 prompt 运行，
取 `git diff` 为 model patch。旧实现输出 predictions CSV，但本地固定版本的官方
`swebench.harness.run_evaluation` 仅接受 JSON/JSONL；安装路径、镜像解析、patch 完整性
和运行隔离也需按新版方案修正后，才能进行官方评分。

**密钥处理**：provider 以 `secret=None` 创建，运行时通过 per-exec 环境变量
`MORROW_<PROVIDER>_API_KEY` 解析。TB2 adapter 使用环境传递；SWE 旧 runner 的
`docker exec -e KEY=value` 仍把值放入 argv，须按新版方案修正后再运行。
容器内 headless Linux 无 Keychain，环境凭据路径绕开了 keyring 限制。

## 旧版快速开始（新版执行请使用上方方案）

新版驱动的 `--dry-run` 只读取账本并核对任务；`--full` 还核对冻结的 89 个任务及目录摘要。
正式运行先在一个账本锁内接纳整批任务，不能只启动余额容许的子集。`prepare_assets.sh`
先构建 GUI，再生成源码、wheel 和依赖资产绑定清单；运行预检拒绝过期 wheel。
收集结果时将 `--tb2-jobs` 指向单个 job；只有任务、reward 和关键指纹完整匹配时
`report_kind` 才为 `fixed_version_full`。费用和 usage 缺失在报告中保留覆盖缺口。
无模型容器检查入口为 `bash evals/benchmarks/scripts/container_contracts.sh`。
真实 Harbor 链路可在仓库根目录依次执行
`evals/benchmarks/.venv/bin/python evals/benchmarks/scripts/g1_harbor_fake.py --task g1-neutral`、
`--task g1-service`、`--task g1-interrupt`、`--task g1-kill`（后三条替换同一命令的 task 值）。
这些 fixture 仅用本地脚本 Provider，不写模型预算账本；独立 trial 输出在 `runs/g1/`。

```bash
cd evals/benchmarks

# 0. 一次性：准备离线资产（wheel/python-build/wheelhouse，已验证 linux/amd64）
scripts/prepare_assets.sh

# 1. 配置（填入真实 endpoint 与 key）
cp config/env.template .env

# 2. TB2 pilot（25 任务，dry-run 先看计划）
python3 run_tb2.py --pilot --dry-run
python3 run_tb2.py --pilot
# 通过后跑全量 89
python3 run_tb2.py --full

# 3. SWE-bench Lite 暂不直接运行；先完成新版方案第 9 节适配。

# 4. 聚合指标
python3 collect_metrics.py
```

`prepare_assets.sh` 每次重建当前源码 wheel 和依赖 wheelhouse，按
`manylinux_2_28_x86_64` 解析依赖，并在无网络的 Debian 11 容器中完成安装冒烟，
成功后才替换评测资产。准备阶段需要可用的 Docker 镜像和包镜像源；试次安装不访问网络。

## 预算方案（默认 300M token 接纳额度）

每个任务 admission 先占用 reservation。完整用量在任务结束后按 Morrow
`run.completed.usage.total_tokens` 结算；部分或未知用量保留至少 reservation，
并单列已知实耗与未知覆盖数。所有驱动经进程锁共享 `runs/budget-ledger.json`。

以下为旧预算安排，不适用于新版独立全量 job。新版采用 3+12+89 个新 trials，按
1M/trial 预留 104M；继续使用当前 300M 总额账本，并在每阶段核算后决定是否接纳。
旧版分配仅保留作历史参考：

| 阶段 | 内容 | 预留 |
|---|---|---|
| TB2 pilot | 25 任务 × ~1.0M | 25M |
| TB2 full 剩余 | 64 任务 × ~1.0M | 64M 内 |
| SWE-bench Lite 冒烟 + 分批 | 每实例 ~0.5–1.5M | 视 pilot 实测在剩余额度内滚动 admission |

若实测单任务均值显著高于预留，应缩小每批 `--limit` 并观察账本
`used/remaining` 以及 `unknown_exposure_count`；余额不足下一次 reservation 时停止接纳。

## 收集的指标（collect_metrics.py 输出 results/metrics.json）

- TB2：Resolution Rate；按 difficulty/category 通过率；p50/p95 任务耗时；
  Input/Output/Total tokens（含每任务 p50/p95）；tool_calls/tool_rounds/
  model_attempts（模型请求）/retry_count/上下文压缩（dropped+cleared cycles）；
  单任务成本、单成功任务成本；失败分类（模型失败/工具失败/超时/环境失败/预算耗尽，
  来自 exception_info + stop_code）。缺少终态时的已知 token 下界与未知请求数
  单列为 `partial_usage`，不混入完整终态 token 总数。
- SWE-bench Lite：% Resolved（官方 report）；按 repo 通过率；p50/p95 完成时间；
  tokens 总量与单实例分布；patch 统计（patched/empty/error）；官方 results.json 原文引用。

## 断点续跑与持久化（中断后无需全量重跑）

| 层 | 持久化位置 | 中断后续跑行为 |
|---|---|---|
| 预算账本 | `runs/budget-ledger.json`（进程锁与原子写入） | 每次新运行使用独立 run ID；完整 usage 按实耗结算，未知或部分 usage 保留至少 reservation，并在汇总单列已知实耗和未知覆盖数 |
| TB2 任务 | `runs/jobs/<effort>/<job>-<run-id>/trials/*/result.json` | 默认新建运行；`--resume-run-id <id>` 仅在指纹完全相同时续跑，Harbor 跳过已有 result.json 的 trial |
| TB2 过程日志 | 固定 Harbor 版本的 trial `agent/` 下的 `morrow-diagnostics.jsonl`、`trajectory.json`、尽力回收的 `morrow-run.jsonl` 以及终态或部分指标 JSON；收集器也兼容旧 `agent/logs/` 布局 | 脱敏诊断随运行逐条刷盘；`_finalize_from_job_logs` 只补正仍处于 admitted 状态的条目 |
| SWE-bench 实例 | `runs/swebench-lite/instances-shard<N>.jsonl`（append）+ `workspaces/*__patch.diff` + `*__morrow-run.jsonl` | 默认跳过 jsonl 中已记录的实例（`--rerun` 可强制重跑）；predictions CSV 每次从磁盘上的 patch 文件重建，已完成的实例不会丢 patch |
| 容器残留 | docker | SWE runner 每次启动实例前 `docker rm -f` 同名残留容器，驱动被杀不会卡死续跑 |

两个注意点：

1. TB2 新运行自动生成 run ID，并在 `runs/manifests/<id>.json` 冻结源码、dirty patch、wheel、锁文件、Harbor 及补丁、任务内容与非密配置。每个 trial 的 `agent/morrow-fingerprint.json` 另记实际 prompt 摘要、容器 Python/libc/架构、有效超时和已完成 AgentRun 的冻结 schema/prompt 摘要。使用 `--resume-run-id` 才复用旧预算键；改动配置时开启新运行。
2. 若驱动进程被 SIGKILL，按原 run ID 续跑；SWE 侧最后一个实例无 jsonl 记录时会以新 run ID 重跑并单独计费。已有账本 reservation 会保留未知用量，不能当作真实 token 硬上限。

`collect_metrics.py --tb2-jobs` 应指向**单个固定配置 job**，才可解释为该版本的分数；默认扫全部 jobs 的输出只表示混合 campaign。诊断子集单独保存，不能与全量分混用。预算账本仅控制任务接纳；请求可能超出预留量，严格硬预算需要在模型请求接纳处实现额外上限。

## 已知限制（CN 网络环境）

- docker.io / ghcr.io 直连受限：docker hub 走 `docker.m.daocloud.io` 已可用；
  ghcr 的 SWE-bench 镜像需配 `MORROW_BENCH_SWE_IMAGE_TEMPLATE` 指向可达镜像站，
  或用 `swebench.image_builder` 按官方 spec 本地构建（base 镜像走 daocloud 代理）。
- TB2 任务镜像（docker hub `alexgshaw/*`）走 daocloud 拉取；个别任务
  `allow_internet=false` 时模型 API 需走宿主机可达网络（Harbor local 环境下
  容器共享宿主机网络，一般无碍）。
- 首次 `harbor run` 会为 25 个 pilot 任务拉取镜像（每个 0.5–3 GB），请预留磁盘。

## 验证记录（2026-09-24）

- `prepare_assets.sh` 重建当前源码 wheel 和 58 个依赖 wheel；
  `cryptography-50.0.1` 使用 `manylinux_2_28_x86_64`。
- 两个本地 Debian 11 / glibc 2.31 QEMU 任务镜像内，无网络安装链路通过：
  python-build-standalone → venv → wheelhouse 装 Morrow → `morrow --help`。
- Harbor 适配器 7 项离线回归通过，涵盖超时取消日志回收、非零退出分类、
  PATH 隔离、workdir 继承和 reasoning effort 传递；资产准备的成功与失败事务测试通过。
- Morrow 全量离线测试 2490 通过、2 项 live 未运行。
- 本次修复未运行模型评测任务；SWE-bench 实例镜像仍需独立验证。
