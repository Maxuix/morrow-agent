# Stage 7 Direct Workflow Integrity Remediation（2026-09-03）

## Outcome

真实 coding 模拟评测发现的 P1 阻塞已修复。Direct Workflow 的 invoking-session node 现在可以
合法复用其 WorkflowRun 的 user root task，同时完整性检查仍严格拒绝错误 root、错误 Session
以及 isolated node 的非 `workflow_node` leaf。

## Root cause

`verify_workflow_rows()` 原先对所有非空 `leaf_task_run_id` 使用同一不变量：对应 TaskRun 必须
具有 `purpose=workflow_node`。该规则适用于 isolated node，但 Direct 的冻结语义是复用调用
Session 中的精确 root TaskRun；该任务按设计具有 `purpose=user`。因此 Direct 本身成功且代码
结果正确，随后 Doctor 却误报 `workflow_integrity`，Backup 也因引用完整性验证失败而拒绝创建。

## Repair

- 从 WorkflowRun 的冻结 revision 解析当前 node 的 `conversation_scope`。
- `invoking_session`：要求 `leaf_task_run_id == workflow_run.root_task_run_id`，并要求 TaskRun
  属于同一 workspace、同一 conversation Session、`purpose=user`。
- 其他 scope：保留原有同 workspace、同 Session、`purpose=workflow_node` 校验。
- 新增 Direct 端到端回归：完成 Workflow 后必须通过 Doctor、Backup creation 和 Verify。

## Verification

- 定向 Direct + isolated regression：18 passed。
- Stage 7 matrix：235 passed。
- 全量离线：1538 passed，2 skipped，2 Live deselected。
- Ruff format/check、compileall、`git diff --check`：通过。
- 原始真实状态：67 个 Provider generation requests、8 个 AgentRun、3 个成功 WorkflowRun。
- 修复前：Doctor `needs_repair` / `workflow_integrity`；Backup 创建失败。
- 修复后：Doctor `ok`；`coding-eval-fixed.bundle` 创建成功。
- Bundle Verify：manifest、database integrity、foreign keys、files、YAML、skills、artifacts、
  references、credentials exclusion 全部通过，issues 为空。

未改写既有 operational data；同一真实历史仅通过修正后的只读完整性规则即可恢复健康。
