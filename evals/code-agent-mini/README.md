# Morrow Code Agent Mini Eval

这是一个面向本地、低成本对照测试的 10 项 Code Agent 评测集。S7P-00 将它从手工 CSV
升级为可重建、版本化、不可静默篡改的 run bundle；它用于发现能力缺口和比较 Agent，
不用于生成具有统计意义的公开排行榜。

## 任务构成

| 范围 | 数量 | 难度 | 说明 |
|---|---:|---|---|
| Morrow 历史任务 | 6 | 简单 1 / 中等 2 / 困难 3 | CLI、Provider、恢复、持久化、异步 Worker 和 Runtime Policy |
| 外部任务 | 4 | 简单 1 / 中等 1 / 困难 2 | 固定版本的 Aider/Exercism Python 任务 |

历史任务只记录基线 Commit、Gold Commit、任务说明和验证选择器。准备时从本仓库 Git 对象
创建没有历史记录的隔离工作区；外部任务只保存必要的 Starter、独立验证器和 self-check
参考实现。Gold 只允许进入 `self-check` 的临时目录，不能进入被测 Agent 工作区。

## 协议和 profile

`protocol.toml` 是 S7P-00 v1 的权威合同：它冻结七类任务结果、五种独立工具终态、每项
至少两次重复、必需证据、Stage 7 门槛和四个 Pi 对照任务。修改门槛必须发布新的协议版本。

`profile.template.json` 是严格的非秘密运行 profile 模板。复制到评测目录之外后填写 Agent、
Provider/model revision、采样、工具 schema hash、权限、预算、system prompt/project
instructions 快照和执行版本。不能写入 credential、reasoning、完整工具参数/结果或 traceback；
未知字段会被拒绝，缺省计量必须显式写 `"unavailable"`，不会静默变成零。

## 兼容命令

在 Morrow 仓库根目录执行：

```bash
.venv/bin/python evals/code-agent-mini/eval.py list
.venv/bin/python evals/code-agent-mini/eval.py show MORROW-001
.venv/bin/python evals/code-agent-mini/eval.py prepare MORROW-001 /tmp/morrow-eval-001
.venv/bin/python evals/code-agent-mini/eval.py verify MORROW-001 /tmp/morrow-eval-001
```

`prepare` 仍会创建一个新的目录、放入基线源码或外部 Starter、写入 Agent 可见的 `TASK.md`，
并初始化只有一个 baseline Commit 的 Git 仓库；它拒绝覆盖已存在的路径。`verify` 从固定的
Gold 测试或独立外部验证器读取测试，不修改被测工作区。

## 可重建的运行流程

每项任务至少建立两次新重复。profile 和 run bundle 应放在评测数据集之外；只有
`run-manifest.json` 生成的 `workspace/` 可交给 Agent：

```bash
RUNS=/tmp/morrow-s7p-00-runs
PROFILE=/tmp/morrow-s7p-00-profile.json
mkdir -p "$RUNS"

.venv/bin/python evals/code-agent-mini/eval.py start MORROW-003 \
  "$RUNS/morrow-003-1" --repetition 1 --profile "$PROFILE"
```

`start` 在 Agent 执行前冻结 evaluator commit、数据集/协议/任务/config 哈希、重复编号、
workspace baseline commit/tree、非秘密 profile、权限和 change allowlist。allowlist 只在
manifest 中，marker 不包含 Gold 或预期修改策略。Agent 只能在 `.../morrow-003-1/workspace`
中工作。

Agent 结束后，单独准备不含敏感内容的 `runtime-evidence.json`，只记录工具终态计数、有限的
诊断计数、usage 和 stop code/reason。例如：

```json
{
  "schema_version": 1,
  "availability": "available",
  "tool_states": {"succeeded": 12, "failed": 0, "denied": 0, "cancelled": 0, "blocked": 0},
  "tool_diagnostics": {"invalid_arguments": 0, "unaccounted_tool_calls": 0,
                        "total_tool_calls": 12, "basic_tool_blocked": 0},
  "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "cost": 0.0,
             "duration_ms": 1, "rounds": 1, "user_interventions": 0, "rework_count": 0},
  "stop": {"code": "completed", "reason": "completed"}
}
```

然后 finalize。它会保存 verifier 原始输出、Git status/diff、预期/意外路径、runtime/stop
evidence 以及每个 artifact 的 hash：

```bash
.venv/bin/python evals/code-agent-mini/eval.py finalize \
  "$RUNS/morrow-003-1/run-manifest.json" \
  --runtime-evidence /tmp/morrow-003-1-runtime-evidence.json

.venv/bin/python evals/code-agent-mini/eval.py rebuild \
  "$RUNS/morrow-003-1/run-manifest.json" /tmp/morrow-003-1-rebuilt
```

`rebuild` 只接受当前匹配的数据集/协议哈希、evaluator commit 和 configuration snapshot，
并验证 fresh workspace 的 baseline tree 和 marker；源 checkout 必须干净才能成为可比较
运行。显式 dirty diagnostic 会保留有限摘要，但永远不能通过比较门槛。

`finalize` 会用 staged+unstaged 的 binary Git diff、untracked 文件和 `--ignored` status
记录变更，也保留 rename/delete 路径。symlink、hardlink、设备/FIFO 等非普通文件以及
workspace 根目录越界会 fail closed；每个 task 的所有 `required_paths` 都必须出现，才能
进入 PASS。敏感 verifier 输出会被拒绝而不会写入 bundle；非 PASS 的 `finalize` 返回非零。

完成 10 项各两次后机械汇总：

```bash
.venv/bin/python evals/code-agent-mini/eval.py summarize "$RUNS" \
  --output "$RUNS/summary.json"
```

summary 会重新验证 manifest、结果和所有 evidence hash，拒绝重复 task/repetition、混用
protocol/profile、partial bundle、缺证据、意外重复和不可用必需计量。`INCOMPLETE` 永远不是
PASS；`unavailable` 永远不是零。结果类和工具终态分别汇总，`FAIL_*`、`DENIED_POLICY`、
`BLOCKED_ENV` 不会被合并。只有 verifier 成功、没有意外修改、所有 required paths 出现、
证据完整且源可比较时，单次结果才可为 PASS；汇总命令在完整且冻结门槛通过时才返回成功。

## S7P-09 Morrow/Pi 重复基线

S7P-09 在上述不可变 bundle 之上增加严格的共同条件计划、28-run counterbalanced schedule、
Pi 0.84.2 JSONL 归一化、Morrow 安全 trace 归一化、权限等价证明、campaign admission/budget
边界和机械 paired comparison。模板 `comparison-plan.template.json` 故意不能直接通过校验；
所有 `REPLACE` 值、两个完整 profile、精确 28 项 schedule 和最终 integrity 都必须在 hold point
审批后冻结，不能把占位符当作运行默认值。

以下命令全部是离线合同检查，不读取 credential，也不发出模型请求：

```bash
.venv/bin/python evals/code-agent-mini/eval.py schedule
.venv/bin/python evals/code-agent-mini/eval.py permission-check /tmp
.venv/bin/python evals/code-agent-mini/eval.py plan-check /protected/comparison-plan.json
.venv/bin/python evals/code-agent-mini/eval.py campaign-preflight \
  /protected/comparison-plan.json /protected/raw-evidence
.venv/bin/python evals/code-agent-mini/eval.py campaign-capacity \
  /protected/comparison-plan.json /protected/raw-evidence
```

`campaign-preflight` 验证干净的 Morrow commit/tracked-source hash、dataset/protocol、Pi 0.84.2
executable/package hash、evidence-root 身份/权限/空间、start-not-before 和权限矩阵。credential readiness
与 no-tool model probe 明确报告为未执行。`campaign-capacity` 优先采用 finalized runtime usage；
该值不可用时回退到 Morrow durable request journal 或去重后的 Pi assistant usage，并以
`max(reservation, known usage)` 作为下一次 admission 的保守计量。具体 Token 硬上限由冻结计划
决定；没有货币上限时 cost 仍保留可用性事实，但不参与准入。

两侧正式 runner 都先创建共享 normalized trace，再投影为 `finalize` 可直接接受的安全
runtime-evidence。Morrow runner 通过普通 bootstrap/AgentLoop/ToolExecutor
组合注入 bounded EvaluationApprovalPort；Pi runner 只加载 content-hashed policy extension，禁用用户
extension/skill/template/session，并用 macOS Seatbelt 约束 bash：

```bash
.venv/bin/python evals/code-agent-mini/eval.py run-morrow \
  /protected/run/workspace /protected/run/morrow-state /protected/run/prompt.txt \
  /protected/run/morrow.normalized.json
.venv/bin/python evals/code-agent-mini/eval.py run-pi \
  /protected/run/workspace /protected/run/pi-raw /protected/run/prompt.txt \
  /protected/run/pi.normalized.json
```

`run-pi` 的 evidence directory 必须预先以 mode `0700` 创建；raw stdout/stderr 只保留在该非 Git
目录，命令输出仅返回 normalized 文件和 raw stream 的 hash/byte count。

受保护的 Pi JSONL 不进入 Git。归一化命令只创建 bounded JSON，并且 stdout 只显示输出路径与 hash：

```bash
.venv/bin/python evals/code-agent-mini/eval.py normalize-pi /protected/pi.raw.jsonl \
  /protected/pi.normalized.json --duration-ms 1234 --workspace /protected/run/workspace
.venv/bin/python evals/code-agent-mini/eval.py normalize-morrow /protected/morrow.safe.json \
  /protected/morrow.normalized.json
```

归一化器只保留 round/attempt、工具 ordinal/capability/终态、workspace 相对路径、validator
kind/status、首读/首写/首验证、compaction/retry、usage/cost/duration、rework/intervention 和 stop
facts；prompt、response、reasoning、完整参数/结果、stdout/stderr 与 traceback 不会进入输出。未知 Pi
事件、重复 authoritative message、未终结工具调用或未知 stop reason 都 fail closed。

正式 compare 还要求 28 个 create-only admission 与冻结 schedule 完全一致，并从 20 个 Morrow
和 8 个 Pi bundle 重新验证 profile/dataset/protocol/evidence hash、完整 metrics、Morrow gate、四任务
stable quality deficit、Morrow-only basic-tool blocker 与总预算：

```bash
.venv/bin/python evals/code-agent-mini/eval.py compare /protected/comparison-plan.json \
  /protected/morrow-runs /protected/pi-runs --admissions-root /protected/admissions \
  --output /protected/comparison-summary.json
```

输出使用 create-only 写入，并同时生成同目录 `baseline.json`。共同 Provider/model 与 Token 预算
已获批准；在 Pi credential、served revision/sampling、两侧 readiness hash 及 clean source/evidence
pins 全部通过前，仍不得执行 formal admission。当前离线 harness 通过不等于 S7P-09 campaign PASS。

## 数据集 self-check

```bash
.venv/bin/python evals/code-agent-mini/eval.py self-check
.venv/bin/python evals/code-agent-mini/eval.py self-check EXTERNAL-003 MORROW-006
```

self-check 只在临时目录构造 baseline 和 Gold，确认 baseline 失败、Gold 通过；不调用模型、
不消耗 Token、不访问网络。正式评测不能把参考实现复制到 Agent 工作区。

## 依赖和边界

运行器只使用 Python 标准库、Git 和项目现有的 Pytest，不增加项目依赖。S7P-00 不运行真实
Provider、Pi、network 或 credential 测试，也不修改 AgentLoop、Provider、公开事件、
Operational Store、生产 prompt 或 runtime policy。

旧的 `results-template.csv` 已退役；结果必须以 run bundle 和机器汇总 JSON 为准。历史
Stage 7 Direct baseline 保持不变，已知统计修正记录在
[`docs/acceptance/s7p-00-evaluation-protocol.md`](../../docs/acceptance/s7p-00-evaluation-protocol.md)。
外部材料的固定来源和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
