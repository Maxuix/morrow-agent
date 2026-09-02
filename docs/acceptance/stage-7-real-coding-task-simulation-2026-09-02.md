# Stage 7 真实 Coding 任务模拟评测（2026-09-02）

> 后续状态：本文发现的 P1 Direct integrity 阻塞已于 2026-09-03 修复并完成原始状态复测；
> 见 `stage-7-direct-integrity-remediation-2026-09-03.md`。以下内容保留发现时证据。

## 1. Verdict

本轮真实 Provider-backed 评测中，三种内置编码工作流都能完成任务并产出正确代码：Direct、Explore–Implement–Verify、Planned Refactor 的可见测试和事后隐藏验收测试全部通过。

但当前 revision **不适合按完整 Stage 7 运维闭环发布**：Direct Workflow 成功后，状态完整性检查把 invoking-session 根任务错误地当成 isolated workflow leaf 校验，导致 `state doctor` 返回 `workflow_integrity` error，并进一步阻塞备份。

- PASS: 4
- FAIL: 1
- BLOCKED: 1
- NOT RUN: 0
- INCONCLUSIVE: 0

发布阻塞项：P1 — Direct Workflow 的成功状态会被 Workflow integrity verifier 判为损坏，备份无法创建。

## 2. Test basis

- revision: `452f3d52aa51b89dbb721caa1c4101e9e3ff8c14`
- branch: `main`；评测开始时工作树 clean，且相对 `origin/main` ahead 45
- 时间：2026-09-02 15:22–15:42（Asia/Shanghai）
- 平台：macOS，Python 3.14.5
- 启动方式：公开 `uv run morrow ...` CLI；permission mode 使用默认 `manual`
- 状态隔离：独立 state root `/private/tmp/morrow-coding-eval-rhqlMR/state`
- 工程隔离：全部模型写入限定在可丢弃目录 `/private/tmp/morrow-coding-eval-rhqlMR/project`
- 公开接口：`agent publish`、`workflow publish`、`task new`、`workflow run`、`workflow status`、`state doctor`、`state backup`
- Provider：用户已授权且实际执行；`opencode-go/deepseek-v4-flash`
- 凭据：仅复用现有 Keychain 引用；报告、命令输出和夹具均未保存密钥值
- Oracle 隔离：每个隐藏测试文件均在对应 workflow 到达终态后创建于项目目录之外，模型执行期间不可见

## 3. User Surface Inventory

| Surface / capability | Implementation evidence | Reachable states or modes | Scenario IDs | Coverage status |
|---|---|---|---|---|
| Agent publication | `morrow agent publish`；5 个 builtin agent version | direct / explorer / planner / coder / reviewer | S1–S3 | PASS |
| Workflow publication | 3 个 immutable revision；EIV/Planned 有预期 `optional_removed` Host-mode warning | Direct / serial 3-node / serial 4-node | S1–S3 | PASS |
| Direct Workflow | `wrun_2C1s1Qbldlp79OSe` | queued → running → completed/succeeded | S1 | PASS |
| Explore–Implement–Verify | `wrun_ikjSNkTEQq0cvYqL` | explorer → coder → reviewer → succeeded | S2 | PASS |
| Planned Refactor | `wrun_0v9jxn-9gZHCttTA` | explorer → planner → coder → reviewer → succeeded | S3 | PASS |
| Typed node artifacts | EvidenceBundle、PlanArtifact、TextResult、TestReport、ReviewReport 均形成绑定 | available / required output | S2–S3 | PASS |
| 代码结果与回归 | 33 个可见测试；11 个事后隐藏测试 | failing baseline → passing targeted/full suite | S1–S4 | PASS |
| Operational doctor | `state doctor --json` | `needs_repair` | S5 | FAIL |
| Backup | `state backup --name coding-eval-final` | 拒绝创建 bundle | S6 | BLOCKED |

发现范围聚焦于已实现的 Stage 7 coding workflow 公开能力；没有把路线图中的未实现能力计入覆盖率。README 与 CLI 的主要工作流入口未发现冲突。`state doctor`/`state backup` 的参数层级容易误写，但 `--help` 可发现正确入口，因此不计为产品缺陷。

## 4. Scenario results

| ID | Persona and real task | Preconditions | User actions | Expected | Actual and evidence | Status |
|---|---|---|---|---|---|---|
| S1 | 维护者修复 slug 规范化/截断 bug | Direct agent/workflow 已发布；slug 可见测试 3 个失败 | 创建根任务并运行 Direct；终态后执行隐藏 oracle | 只改 slug 模块/测试并正确处理边界 | run succeeded；changed paths 仅 `slugify.py`/测试；7/7 可见、4/4 隐藏通过 | PASS |
| S2 | 服务开发者实现库存顺序分配规则 | EIV 已发布；库存不足测试失败 | Explorer 取证，Coder 实现，Reviewer 独立审查；终态后隐藏 oracle | typed artifact 串行传递，库存原子性/重复 ID/非法数量正确 | run succeeded；Reviewer `approve`；9/9 可见、3/3 隐藏通过 | PASS |
| S3 | 库维护者安全重构 retry policy parser | Planned Refactor 已发布；大小写/ms 测试报错 | Explorer→Planner→Coder→Reviewer；运行完整回归和隐藏 oracle | 保持 API，拆分 helpers，严格 grammar，review approve | run succeeded；PlanArtifact/ReviewReport 可用；17/17 目标测试、33/33 全套、4/4 隐藏通过 | PASS |
| S4 | 发布者做跨任务最终回归 | 三个任务均完成 | `PYTHONPATH=src python3 -m unittest discover -s tests -v` | 所有可见行为无回归 | 33 tests，OK | PASS |
| S5 | 运维者检查执行后状态健康 | 三个真实 workflow 已完成 | `morrow state doctor --workspace-id ... --json` | `health=ok` | `health=needs_repair`，error=`workflow_integrity` | FAIL |
| S6 | 运维者备份评测状态 | S5 后状态仍可读 | `morrow state backup --name coding-eval-final` | 创建可验证 bundle | `backup bundle could not be completed`；无 bundle 留下 | BLOCKED |

### 关键复现详情

- S1 run：`wrun_2C1s1Qbldlp79OSe`，13 次生成请求，约 92 秒，16 次 tool execution。
- S2 run：`wrun_ikjSNkTEQq0cvYqL`，25 次生成请求，约 273 秒，40 次 tool execution。
- S3 run：`wrun_0v9jxn-9gZHCttTA`，29 次生成请求，约 329 秒，48 次 tool execution。
- S5 诊断：Direct node `direct` 的 `leaf_task_run_id=task_-9T3I1bi0atQkpEr`，该任务属于 invoking session 且 `purpose=user`；isolated workflow nodes 的 leaf task 均为 `purpose=workflow_node`。当前 verifier 对所有非空 leaf task 无条件要求 `purpose=workflow_node`。

## 5. Complex journeys

### J1 — Direct bug fix with hidden boundary oracle

目标是修复一个真实字符串工具的 Unicode 归一化、fallback、长度验证和截断边界。状态从 3 个失败的 slug 测试开始，模型只能看到公开测试和任务说明。复杂点是必须区分 `bool` 与 `int`、同时让 fallback 服从长度限制，并避免截断后尾随 `-`。工作流成功后才创建 4 个隐藏测试，覆盖混合分隔符、NFKD、最小长度和 bool；全部通过。初次预创建根任务被后续根任务标记 abandoned，评测按公开生命周期约束改为“每次运行前创建根任务”后成功，这属于 harness 恢复而非产品执行失败。

### J2 — Evidence-driven inventory feature

目标是在不修改输入 stock 的前提下，按请求顺序执行原子分配。Explorer 先识别超库存、非法数量和 duplicate ID 的缺口；EvidenceBundle 传给 Coder，Coder 写入并提交 TestReport；Reviewer 只读审查后 `approve`。隐藏 journey 使用 generator 混合成功、库存不足、重复 ID、未知 SKU 与后续精确耗尽，验证了状态跨请求携带和拒绝原子性，结果全部正确。

### J3 — Planned parser refactor with strict error contract

目标不仅是修复 parser，还要先形成可审查计划，再保持返回 shape 完成结构化重构。Explorer 给出行为证据，Planner 形成 private helper 和验证矩阵，Coder 实现，Reviewer 验证 grammar 与异常边界。隐藏测试覆盖字段逆序、大小写重复 key、2001ms 向上取整、分钟转换以及多类畸形输入均只抛 `ValueError`。最终代码结果正确；过程中 `submit_node_result` 有两次失败后自动重试成功，终态仍为 succeeded。

## 6. Findings

### P1 — Direct Workflow 成功后被完整性检查误判，阻塞备份

- 影响：使用 Direct 的用户虽然能得到正确代码和 succeeded run，但工作区随后进入 `needs_repair`，无法创建完整备份。
- 最短公开复现：发布并运行 `builtin_direct_workflow` 至 succeeded，然后执行 `morrow state doctor --workspace-id <id> --json`，再执行 `morrow state backup`。
- 期望：Direct invoking-session node 合法引用其 user root task；doctor 为 `ok`，backup 可创建。
- 实际：doctor 报 `workflow_integrity`，backup 报 `backup bundle could not be completed`。
- 可复现性：本隔离状态 1/1；数据库只读核对显示 Direct leaf task 为相同 session 的 `purpose=user`，而 `verify_workflow_rows()` 对任何非空 `leaf_task_run_id` 都要求 `purpose=workflow_node`。
- 边界：`src/morrow/application/workflows/integrity.py` 的 node leaf ownership 校验没有区分 `conversation_scope=invoking_session` 与 isolated node。
- Workaround：本轮未修改状态；只运行 isolated EIV/Planned 的状态此前可通过 doctor，但一旦包含 Direct run，备份仍被阻塞。

### P3 — succeeded 终态仍保留失败的结构化提交项

- 影响：Planned Refactor 最终成功，但 terminal outcome 的 `unresolved_items` 仍含两个 `submit_node_result:failed`，容易让运维或上层 UI 误判是否仍需处理。
- 复现：查看 `wrun_0v9jxn-9gZHCttTA` 的最终 `workflow status`。
- 期望：自动重试已成功后，失败尝试作为历史/重试证据呈现，不再列为 unresolved。
- 实际：`result_status=succeeded`、所有节点 completed，但 `unresolved_items` 仍有两项 failed。
- 可复现性：本轮 1/1 Planned run；未观察到功能性影响。
- Workaround：以 run/node terminal status、required artifacts 和外部测试为最终判断依据。

## 7. Coverage and gaps

- 发现的核心 coding workflow 模板：3；已执行：3；覆盖率 100%（3/3）。
- 计划 scenario：6；已执行：6；执行率 100%（6/6）。
- 正确结果 scenario：4/4 PASS（3 个独立 coding journey + 1 个最终全量回归）。
- 运维闭环：doctor 0/1 PASS；backup 0/1 完成。
- 状态转换：agent/workflow publication、root task open、node queued/running/completed、typed artifact available/bound、root ready_for_acceptance、doctor needs_repair、backup refusal。
- distinct complex journeys：3。
- Provider-backed：执行 3，blocked 0，omitted 0。
- 未覆盖：pause/cancel/retry/resume、并发 DAG、auto-sandboxed 权限、其他 Provider/model、Linux/Windows、真实 Git 仓库提交与远端集成。原因是本轮目标是追加真实 coding 任务正确性，而不是重复全量 Stage 7 功能矩阵。

## 8. Provider evidence

- adapter/model：`opencode-go/deepseek-v4-flash`
- 总生成请求：67（Direct 13 + EIV 25 + Planned 29）
- 真实行为：代码读取、文件编辑、测试执行、typed node result 提交、独立 review
- 结果：3/3 workflow completed/succeeded；67 个请求均已关闭，doctor 计数 `agent_run_open_model_requests=0`
- 聚合耗时：约 694 秒（约 11 分 34 秒）
- cost/token：当前公开状态未提供可用数值，因此不推测
- 安全：未记录 provider 原始 payload、凭据或完整私有上下文

## 9. Recommended next actions

### 产品修复

1. P1：让 Workflow integrity verifier 按冻结的 `conversation_scope` 校验 leaf ownership。isolated node 继续要求独立 `workflow_node` task；invoking-session Direct 应允许 leaf 指向同 workspace/session 的 user root task，并校验它就是 run root。
2. 增加回归：真实或 scripted Direct run succeeded → `state doctor` ok → `state backup` 创建 → `state verify-backup` ok，同时保留 isolated ownership 的负向篡改测试。
3. P3：自动重试最终成功后，把先前 `submit_node_result:failed` 放入历史/attempt evidence，不保留在 `unresolved_items`。

### 测试环境

1. 每个 workflow 临运行前创建 root task，避免同一 Session 预创建多个 root 时前序任务按设计被 abandoned。
2. 保留“终态后生成项目外隐藏 oracle”的方式，避免模型针对隐藏测试过拟合。

### 后续授权覆盖

1. 修复 P1 后原样重跑三条 coding journey，并强制完成 doctor、backup、verify-backup 闭环。
2. 再补一个失败后 `workflow retry/resume` 的 coding journey，以及一个 auto-sandboxed promotion journey。
