# Benchmark v2 实施验收记录

日期：2026-09-27。实现分支：`feat/benchmark-v2`。本记录对应
`docs/research/benchmark-v2-implementation-plan-2026-09-27.md` 的 B1–B5。

## 已实施

- B1：只认 Harbor trial 结果，排除 job 汇总；官方 reward 与 agent exception 分列。
  重复、缺失、未知任务和关键指纹差异使 full 报告无效；费用/usage 保留覆盖率和已知下界。
  `fixed_version_full` 只在冻结 89 题完整、每题一次且 reward 全部已知时出现。
- B2：驱动先合并 `.env` 与进程环境再解析默认值，正整数与有限时限校验；
  dry-run 只读且核对任务，`--full` 核对冻结 89 题 checksum。
  每 job 显式 `n_attempts=1`、`max_retries=0`，整批预算在同一锁中全有或全无地接纳。
  模型容量必须显式填写并转发到 Harbor adapter；启动前还会核对产品的默认
  16,384 token 输出预留及上下限，避免预算接纳后才在 Session 启动失败。
- B3：`prepare_assets.sh` 先构建并验证 GUI，然后构建 wheel、下载 amd64 依赖、
  在 Debian 11 无网络安装，通过后替换旧资产。构建清单绑定最近一次产品源码提交、
  源码树、wheel、CPython、uv、wheelhouse、锁文件、主机架构和兼容镜像 ID。
  运行预检再次逐文件核对 wheel 内 Python/GUI 静态文件与当前源码。
- B4：新增 `container_contracts.sh`，在 Debian 11 与官方 `fix-git` 任务镜像
  以无网络方式安装当前 wheel，运行脚本 Provider、Shell、长进程、deadline、headless
  与恢复相关测试；独立容器执行 TERM/KILL 并核对挂载日志留存。四个独立 Harbor
  fixture 使用本地脚本 Provider 验证正常结束、服务跨 verifier、可捕获中断与强制终止。
  修复 headless acceptance 长进程被 `asyncio` 事件循环销毁时终止的问题。
- B5：README、环境模板与冻结任务配置按新版协议更新；单 job 报告有完整任务表、
  官方 reward 覆盖率、运行状态和证据路径。

## 实际验证

| 检查 | 结果 |
|---|---|
| `uv run pytest -m 'not live' -q` | 2564 passed，2 deselected |
| Benchmark `unittest discover` | 38 项通过 |
| `uv run ruff format --check .`、`uv run ruff check .`、`compileall` | 通过 |
| GUI typecheck/test/build | 103 文件、702 测试通过；JS 1765.2/1900 KiB，CSS 119.7/120 KiB |
| wheel 与源码逐文件核对 | 0 处不一致；旧 wheel 原有 33 处 |
| Debian 11 与 `fix-git` 镜像 | 各 56 项无模型测试通过；两种镜像均无网络安装 |
| 独立容器 TERM/KILL | 两次挂载日志留存通过 |
| 89 题只读 dry-run | 89 题 checksum 一致；需预留 89M，快照可接纳 |
| 中性 Harbor 基础闭环 | `g1-neutral-3ee300441fee`：Harbor exit 0，verifier reward 1.0，exception 为 null，`run.completed` 与指纹存在；收集器读取真实 `agent/` 日志，usage 13 tokens，费用未知保持 null |
| 服务跨 verifier | `g1-service-5298aa92c5db`：`bash mode=start lifecycle=acceptance` 返回运行中，同一 CLI 内 poll 与 HTTP 检查通过；CLI 退出后 verifier 再读到 `G1_SERVICE_OK`，reward 1.0，无异常 |
| 可捕获中断 | `g1-interrupt-88c0cd46b9d1`：SIGINT 后 `run.completed` 的 finish_reason 为 cancelled、execution_finished 为 false，13 tokens 可读；Harbor 记录 agent exception，reward 1.0 未被覆盖 |
| 强制终止 | `g1-kill-da7049c3fa8f`：SIGKILL 后无 `run.completed`，adapter 回收 partial metrics：已知 input 10/output 3 tokens、未知费用请求 1；Harbor 记录 agent exception，reward 1.0 未被覆盖 |

首次中性 trial 使用 10,000 token 假模型窗口，低于 Morrow 默认 16,384 token
预留，Morrow 以 exit 2 拒绝启动；Harbor 仍 exit 0 且 verifier reward 1.0。
由此修复容量预检、Harbor 原始 `exception_type` 解析及真实 `agent/` 日志路径，
验收脚本现在要求无 exception、`run.completed`、终态指标和指纹同时存在。
重新以 65,536 token 假模型窗口运行后基础闭环通过。前一次失败 trial 保留于
`runs/g1/jobs/g1-neutral-390986833a18/`，不得计为通过。

产品源码提交：`99a625167c7114ccb00e527429f6b4734b77a188`。
wheel SHA256：`a1d64f1158d97cce2c5b082450d071fd1d16c0e6c2bafb88c3c44eb2958b8b5c`。
Debian 11 镜像 ID：`sha256:6f519a81440354a85eb592c5f32109ab80605f6b892455983a6f618bf87fabe9`。
`fix-git` 镜像 ID：`sha256:94101036f001a0968ae6b71bd9642f254228f9c0d125b3877dd42b9c9234147b`。
宿主机为 Apple Silicon arm64，容器以 `linux/amd64` 运行；正式报告须注明 QEMU 环境。

账本在本次检查后仍为总额 300,000,000、charged/reserved 175,290,098、
剩余 124,709,902。本轮未运行真实模型试次，未写入新预算条目或正式分数。

## G1 覆盖与执行边界

- 六类基础契约：离线安装在两种镜像实测；服务保活与 poll 在真实 Harbor job 实测；
  内部 deadline 的持续 activity、挂起压缩和工具事实由容器内脚本 Provider 测试覆盖；
  可捕获与不可捕获中断由两种独立 Harbor job 覆盖；断流／无效响应后的复核重试
  和工具只执行一次由容器内故障注入测试覆盖；过期 wheel、关键指纹变化、残缺日志、
  reward=1+exception 由预检／收集器回归及原始 trial 对照覆盖。
- Headless acceptance 进程使用独立 OS 句柄和空标准输出，以便 CLI 结束后保持服务；
  该模式下后续 poll 可读状态但不收集 stdout/stderr。Harbor 在 trial 结束时负责销毁任务容器。
- 外层 Harbor 超时由单元测试核对向 Morrow 转发；尚未做单独的真实 Harbor 超时 fixture。

## 尚未放行的模型阶段门禁

- `.env` 尚未提供当前部署确认的 context window 与 max output 数值；协议仍为 null。
  不根据模型名称猜测容量，也不把模板值当成确认值。
- 因模型容量未确认，G2 三题、G3 十二题和 G4 独立 89 题均未启动；
  SWE-bench Lite 按协议保持关闭。
- 已验证改动 ff-only 合入本地 `main`；本次没有远端推送授权，
  本地 `main` 相对 `origin/main` 仍领先（未 fetch），远端同步待处理。
