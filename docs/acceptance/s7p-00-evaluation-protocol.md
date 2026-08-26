# S7P-00 Evaluation Protocol Acceptance

日期：2026-08-26

状态：实现、independent review subagent review、review-repair 和离线复验完成；本记录不
表示已执行真实 Provider、Pi、network 或 credential 评测。

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
| manifest 重建和 source/dataset/protocol/config hash 检查 | `eval.py:rebuild_workspace` | 重建 tree 相等；dataset/protocol/source/config mismatch 被拒绝 |
| verifier、Git status/diff、stop/runtime、预期/意外/required 路径 | `eval.py:finalize_run` | verifier success+所有 required change 可 PASS；缺路径、意外文件、unsafe file 不能 PASS |
| 不可静默篡改的 evidence bundle | `run-result.json` artifact hashes、regular-file check 和 atomic staging | 修改 evidence、symlink artifact 或 partial bundle 后拒绝/可安全重试 |
| failed/denied/blocked 独立机械汇总 | `eval.py:summarize_runs` 的 `result_counts`、`outcome_counts`、`tool_states` | failed=3、denied=2、blocked 计数不合并；unavailable 不变成零 |
| 两次×十项完整性和门槛 | summary 的 exact task/repetition keys、profile/protocol consistency、gate | synthetic 20-bundle summary 为 `COMPLETE`，结果和 gate 可重建 |
| 兼容命令 | `list/show/prepare/verify/self-check` 保留 | self-check 覆盖全部 10 项 baseline/gold |
| Gold 与安全边界 | Gold 只在 self-check 临时目录；profile/runtime/output 拒绝敏感字段；workspace unsafe types fail closed | 未运行 live Provider/Pi/network/credential；未修改生产代码 |

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

`rebuild` 会从 manifest 重新准备 baseline，并验证当前 source checkout 的 evaluator commit、
configuration snapshot、workspace tree/marker hash。正式比较必须对 10 个任务各执行两次；
四个固定 Pi 任务 (`MORROW-003`、`MORROW-005`、`EXTERNAL-003`、`EXTERNAL-004`) 的对照
结果留给后续比较 lane，S7P-00 不伪造 Pi 数据。

## 独立 review 记录

只读 reviewer 为内部 collaboration subagent Boyle（agent id
`01a03d84-9c4d-72f3-89f0-40b3f80c6012`），审查范围为 `ce9d6abe756291189ae2a6ae5678add259809610`
至 `9ffba1a` 的全部差异；reviewer 未改文件、未提交、未合并。实现 session 对 findings 的
处置如下：

- P1 workspace symlink/hardlink/device/FIFO、root 越界和 artifact symlink：增加 lstat/real
  directory/regular-file fail-closed 检查，并把 unsafe path 纳入 unexpected evidence。
- P1 required paths、staged/unstaged/binary/rename/delete/untracked/ignored diff：required
  缺失必为非 PASS；diff 改用 `git diff HEAD --binary --no-renames` 并保存 bounded status。
- P1 MORROW-006 rename、evaluator commit/config drift、`verifier.status=not_run`：旧/新
  policy 路径均纳入 allowlist；rebuild 校验 source snapshot；validator 拒绝 not-run。
- P1 tool accountability、stop reason、credential/output 泄漏：增加 total/basic diagnostics
  与 terminal invariant；stop reason 只接受 stop code；扩展常见 credential 识别，CLI/self-check
  不打印原始 verifier 输出。
- P2 atomic finalize、partial discovery、timeout、schema hash、synthetic tests 和 CLI exit：
  finalize 使用 staged fsync/promote/rollback，可清理 crash partial；summary 发现 manifest/result
  partial bundle；verifier 有 bounded timeout；tool schema hash 绑定 schema；focused tests 不复制
  Gold，非 PASS finalize 返回非零。

Reviewer 未报告协议分类、重复次数、MORROW-003 Gold commit 或 failed/denied/blocked 分离
语义的阻塞问题。残余风险是 runtime/profile 事实仍依赖调用方提供、未引入签名；本次只覆盖
offline harness，不代表真实 Provider/Pi/network/credential 运行；`eval.py` 仍是较大的兼容
CLI 模块。

## 验证记录

以下命令使用现有只读运行时
`/Users/ruirui/Documents/Project/Agent/developing/.venv/bin/`，未执行 `uv sync`、联网安装或
live 测试。最终数值在实现门禁完成后回填；独立 review findings 返回后，受影响门禁将再次执行。

| 检查 | 结果 |
|---|---|
| focused `tests/test_code_agent_mini_eval.py` | 已通过：22 passed in 9.39s |
| `eval.py self-check` | 已通过：10 tasks；每项 baseline failed、gold passed |
| full offline pytest (`-m 'not live'`) | 已通过：1101 passed, 2 skipped, 2 deselected in 46.66s |
| Ruff format/check | 已通过：451 files already formatted；All checks passed |
| compileall | 已通过：`python -m compileall -q src tests evals/code-agent-mini` |
| eval CLI help/list/show/prepare/verify/start/finalize/summarize smoke；`morrow --help` | 已通过：兼容命令和 lifecycle 命令；预期非 PASS 的 verify/finalize/summarize 均返回非零 |
| `git diff --check` | 已通过：无输出、退出码 0 |

仓库中不提交真实运行 bundle 或模型统计；本 acceptance 记录只证明协议实现和离线 harness
行为。review-repair commit 会与初始实现 commit 分开保留，等待当前/root task 独立合并。
