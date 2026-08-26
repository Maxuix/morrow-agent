# S7P-00 Evaluation Protocol Acceptance

日期：2026-08-26

状态：实现与初始离线验证完成，等待 independent review subagent review；本记录不表示已
执行真实 Provider、Pi、network 或 credential 评测。

## 范围

S7P-00 只改造 `evals/code-agent-mini` 的评测协议和记录链路，不改变 `src/morrow/`、
AgentLoop、Provider、公开事件、Operational Store、生产 prompt 或 runtime-policy。旧的
`stage7-direct-agent-baseline.md` 未被回写；本记录只更正其统计解释：

```text
38 failed + 16 denied = 54 non-success
```

## 验收合同

| 合同 | 实现证据 | 离线证据 |
|---|---|---|
| 协议版本、七类结果、五种工具终态、两次重复和冻结门槛 | `evals/code-agent-mini/protocol.toml`、`eval.py:load_protocol` | `tests/test_code_agent_mini_eval.py::test_protocol_freezes_taxonomy_repetitions_and_gate` |
| 非秘密 profile 严格字段和显式 unavailable | `profile.template.json`、`eval.py:validate_profile` | profile 未知/敏感/缺失字段测试；所有缺省计量不转为零 |
| Run Manifest、任务策略和 fresh workspace | `eval.py:start_run`、`manifest.toml` | 两次 start 的 baseline/dataset/protocol hash 相等，marker 不含 change policy |
| manifest 重建和 dataset/protocol hash 检查 | `eval.py:rebuild_workspace` | 重建 tree 相等；dataset/protocol mismatch 被拒绝 |
| verifier、Git status/diff、stop/runtime、预期/意外路径 | `eval.py:finalize_run` | verifier success+expected change 可 PASS；增加意外文件不能 PASS |
| 不可静默篡改的 evidence bundle | `run-result.json` artifact hashes 和 integrity envelope | 修改 `runtime-evidence.json` 后 `validate_run_bundle` 拒绝 |
| failed/denied/blocked 独立机械汇总 | `eval.py:summarize_runs` 的 `result_counts`、`outcome_counts`、`tool_states` | failed=3、denied=2、blocked 计数不合并；unavailable 不变成零 |
| 两次×十项完整性和门槛 | summary 的 exact task/repetition keys、profile/protocol consistency、gate | synthetic 20-bundle summary 为 `COMPLETE`，结果和 gate 可重建 |
| 兼容命令 | `list/show/prepare/verify/self-check` 保留 | self-check 覆盖全部 10 项 baseline/gold |
| Gold 与安全边界 | Gold 只在 self-check 临时目录；profile/runtime/output 拒绝敏感字段 | 未运行 live Provider/Pi/network/credential；未修改生产代码 |

`PASS` 同时需要外部 verifier 成功、没有 unexpected path、存在相关 expected change、
evidence 完整、源 checkout 可比较且 stop code 为 `completed`。summary 缺重复、重复键、
额外键、混用 protocol/profile、当前 hash 不匹配、敏感/缺失/不可用必需 evidence 时为
`INCOMPLETE`，不评估通过门槛。

## 操作证据

运行前用 `profile.template.json` 生成评测目录外的 profile：

```bash
.venv/bin/python evals/code-agent-mini/eval.py start TASK_ID RUN_DIR \
  --repetition 1 --profile /tmp/morrow-s7p-00-profile.json
```

Agent 只能使用 `RUN_DIR/workspace`。运行结束后提供不含 credential、reasoning、完整工具
参数/结果或 traceback 的 runtime evidence，再执行：

```bash
.venv/bin/python evals/code-agent-mini/eval.py finalize RUN_DIR/run-manifest.json \
  --runtime-evidence /tmp/runtime-evidence.json
.venv/bin/python evals/code-agent-mini/eval.py summarize RUNS_DIR \
  --output RUNS_DIR/summary.json
```

`rebuild` 会从 manifest 重新准备 baseline，并验证 workspace tree/marker hash。正式比较
必须对 10 个任务各执行两次；四个固定 Pi 任务 (`MORROW-003`、`MORROW-005`、
`EXTERNAL-003`、`EXTERNAL-004`) 的对照结果留给后续比较 lane，S7P-00 不伪造 Pi 数据。

## 验证记录

以下命令使用现有只读运行时
`/Users/ruirui/Documents/Project/Agent/developing/.venv/bin/`，未执行 `uv sync`、联网安装或
live 测试。最终数值在实现门禁完成后回填；独立 review findings 返回后，受影响门禁将再次执行。

| 检查 | 结果 |
|---|---|
| focused `tests/test_code_agent_mini_eval.py` | 已通过：9 passed in 7.37s |
| `eval.py self-check` | 已通过：10 tasks；每项 baseline failed、gold passed |
| full offline pytest (`-m 'not live'`) | 已通过：1088 passed, 2 skipped, 2 deselected in 48.60s |
| Ruff format/check | 已通过：451 files already formatted；All checks passed |
| compileall | 已通过：`python -m compileall -q src tests evals/code-agent-mini` |
| CLI help/list | 已通过：eval lifecycle help、10 task list、`morrow --help` |
| `git diff --check` | 已通过：无输出、退出码 0 |

仓库中不提交真实运行 bundle 或模型统计；本 acceptance 记录只证明协议实现和离线 harness
行为。初始实现提交后由 independent review subagent 审查完整 diff；发现问题由实现 session
修复、复验并提交 review-repair commit。
