# Web GUI 改造：CLI 逐命令覆盖基线

> 日期：2026-09-05；代码基线：dde2ada。
> 本表由当前 Typer 命令注册静态提取，并补入 REPL、gui、serve 入口。
> 所有“目标入口”均为改造验收要求，不表示当前 GUI 已支持；本次未运行功能测试。

[当前 PLAN](../../.agent/PLAN.md)定义执行范围与验收；[已确认方案](stage8-chat-centered-web-gui-final-proposal.md)保留决策依据。

共 157 个命令/启动入口。各命令的参数、作用域、修订冲突和确认行为均须保持等价。
实施时为每项补充共享 Application Service、最终 API、用例和结果证据；不能把功能组测试当作每条命令均通过。

| ID | 命令 | 源码位置 | 目标 GUI 入口 | 负责子计划 | 验收旅程 | 新 GUI 等价验证 |
|---|---|---|---|---|---|---|
| CLI-001 | morrow | [cli.py](../../src/morrow/interfaces/cli.py) | 中心 Chat | [2](../../.agent/subplans/2-core-chat-runtime-and-stream.md) | A01、A02、A05 | [ ] 待实施验证 |
| CLI-002 | morrow agent create | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L295 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-003 | morrow agent disable | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L417 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-004 | morrow agent edit | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L309 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-005 | morrow agent enable | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L406 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-006 | morrow agent list | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L254 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-007 | morrow agent publish | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L372 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-008 | morrow agent revoke | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L428 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-009 | morrow agent show | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L269 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-010 | morrow agent validate | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L360 | 设置 → Agents | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-011 | morrow agent-run show | [cli.py](../../src/morrow/interfaces/cli.py)，L865 | 运行详情 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-012 | morrow approval list | [approval_cli.py](../../src/morrow/interfaces/approval_cli.py)，L99 | 中心审批卡 / 权限页 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-013 | morrow approval resolve | [approval_cli.py](../../src/morrow/interfaces/approval_cli.py)，L127 | 中心审批卡 / 权限页 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-014 | morrow artifact list | [cli.py](../../src/morrow/interfaces/cli.py)，L1884 | 产物面板 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-015 | morrow artifact pin | [cli.py](../../src/morrow/interfaces/cli.py)，L1944 | 产物面板 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-016 | morrow artifact release | [cli.py](../../src/morrow/interfaces/cli.py)，L1970 | 产物面板 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-017 | morrow artifact show | [cli.py](../../src/morrow/interfaces/cli.py)，L1921 | 产物面板 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-018 | morrow grant create | [cli.py](../../src/morrow/interfaces/cli.py)，L1811 | 权限页 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-019 | morrow grant list | [cli.py](../../src/morrow/interfaces/cli.py)，L1762 | 权限页 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-020 | morrow grant revoke | [cli.py](../../src/morrow/interfaces/cli.py)，L1854 | 权限页 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-021 | morrow grant show | [cli.py](../../src/morrow/interfaces/cli.py)，L1788 | 权限页 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-022 | morrow gui | [gui_cli.py](../../src/morrow/interfaces/gui_cli.py) | 启动器 / 连接状态；保留 CLI 进程参数 | [2](../../.agent/subplans/2-core-chat-runtime-and-stream.md) | A01、A02、A05 | [ ] 待实施验证 |
| CLI-023 | morrow learning accept | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L325 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-024 | morrow learning edit | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L370 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-025 | morrow learning inbox | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L173 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-026 | morrow learning list | [cli.py](../../src/morrow/interfaces/cli.py)，L1735 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-027 | morrow learning promotions | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L259 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-028 | morrow learning reject | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L465 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-029 | morrow learning request | [cli.py](../../src/morrow/interfaces/cli.py)，L1707 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-030 | morrow learning retry | [cli.py](../../src/morrow/interfaces/cli.py)，L1676 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-031 | morrow learning review | [cli.py](../../src/morrow/interfaces/cli.py)，L1645 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-032 | morrow learning reviews | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L229 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-033 | morrow learning set-mode | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L142 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-034 | morrow learning show | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L209 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-035 | morrow learning status | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L123 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-036 | morrow learning undo | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L295 | 学习设置 / 收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-037 | morrow manage command | [management_cli.py](../../src/morrow/interfaces/management_cli.py)，L104 | 对应管理功能页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-038 | morrow manage query | [management_cli.py](../../src/morrow/interfaces/management_cli.py)，L79 | 对应管理功能页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-039 | morrow mcp add | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L99 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-040 | morrow mcp disable | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L295 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-041 | morrow mcp enable | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L286 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-042 | morrow mcp inspect | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L239 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-043 | morrow mcp list | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L181 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-044 | morrow mcp refresh | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L313 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-045 | morrow mcp remove | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L304 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-046 | morrow mcp show | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L229 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-047 | morrow mcp status | [mcp_cli.py](../../src/morrow/interfaces/mcp_cli.py)，L249 | 设置 → MCP | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-048 | morrow memory delete | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L741 | 记忆设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-049 | morrow memory disable | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L684 | 记忆设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-050 | morrow memory dispute | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L722 | 记忆设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-051 | morrow memory enable | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L703 | 记忆设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-052 | morrow memory list | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L594 | 记忆设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-053 | morrow memory selection list | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L540 | 上下文详情 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-054 | morrow memory selection show | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L570 | 上下文详情 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-055 | morrow memory show | [learning_cli.py](../../src/morrow/interfaces/learning_cli.py)，L632 | 记忆设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-056 | morrow model add | [cli.py](../../src/morrow/interfaces/cli.py)，L702 | 模型选择器 / 模型设置 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-057 | morrow model current | [cli.py](../../src/morrow/interfaces/cli.py)，L781 | 模型选择器 / 模型设置 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-058 | morrow model list | [cli.py](../../src/morrow/interfaces/cli.py)，L659 | 模型选择器 / 模型设置 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-059 | morrow model remove | [cli.py](../../src/morrow/interfaces/cli.py)，L764 | 模型选择器 / 模型设置 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-060 | morrow model show | [cli.py](../../src/morrow/interfaces/cli.py)，L681 | 模型选择器 / 模型设置 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-061 | morrow model sync | [cli.py](../../src/morrow/interfaces/cli.py)，L728 | 模型选择器 / 模型设置 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-062 | morrow model use | [cli.py](../../src/morrow/interfaces/cli.py)，L747 | 模型选择器 / 模型设置 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-063 | morrow preferences add | [cli.py](../../src/morrow/interfaces/cli.py)，L1136 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-064 | morrow preferences disable | [cli.py](../../src/morrow/interfaces/cli.py)，L1260 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-065 | morrow preferences enable | [cli.py](../../src/morrow/interfaces/cli.py)，L1236 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-066 | morrow preferences inbox accept | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L407 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-067 | morrow preferences inbox accept-many | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L477 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-068 | morrow preferences inbox edit-and-accept | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L442 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-069 | morrow preferences inbox job | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L176 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-070 | morrow preferences inbox jobs | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L154 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-071 | morrow preferences inbox list | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L287 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-072 | morrow preferences inbox preview | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L344 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-073 | morrow preferences inbox reject | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L536 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-074 | morrow preferences inbox retry | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L220 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-075 | morrow preferences inbox review | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L514 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-076 | morrow preferences inbox run-pending | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L237 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-077 | morrow preferences inbox show | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L324 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-078 | morrow preferences inbox status | [preferences_cli.py](../../src/morrow/interfaces/preferences_cli.py)，L201 | 学习与偏好收件箱 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-079 | morrow preferences list | [cli.py](../../src/morrow/interfaces/cli.py)，L994 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-080 | morrow preferences remove | [cli.py](../../src/morrow/interfaces/cli.py)，L1212 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-081 | morrow preferences replace | [cli.py](../../src/morrow/interfaces/cli.py)，L1161 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-082 | morrow preferences show | [cli.py](../../src/morrow/interfaces/cli.py)，L1058 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-083 | morrow preferences status | [cli.py](../../src/morrow/interfaces/cli.py)，L970 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-084 | morrow preferences write | [cli.py](../../src/morrow/interfaces/cli.py)，L1031 | 偏好设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-085 | morrow provider add | [cli.py](../../src/morrow/interfaces/cli.py)，L540 | 设置 → 模型服务 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-086 | morrow provider configure | [cli.py](../../src/morrow/interfaces/cli.py)，L629 | 设置 → 模型服务 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-087 | morrow provider list | [cli.py](../../src/morrow/interfaces/cli.py)，L494 | 设置 → 模型服务 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-088 | morrow provider presets | [cli.py](../../src/morrow/interfaces/cli.py)，L534 | 设置 → 模型服务 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-089 | morrow provider remove | [cli.py](../../src/morrow/interfaces/cli.py)，L590 | 设置 → 模型服务 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-090 | morrow provider show | [cli.py](../../src/morrow/interfaces/cli.py)，L504 | 设置 → 模型服务 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-091 | morrow provider test | [cli.py](../../src/morrow/interfaces/cli.py)，L603 | 设置 → 模型服务 | [5](../../.agent/subplans/5-model-reasoning-and-permissions.md) | A07、A08 | [ ] 待实施验证 |
| CLI-092 | morrow recovery resolve | [cli.py](../../src/morrow/interfaces/cli.py)，L2017 | 恢复卡 / 诊断页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-093 | morrow recovery show | [cli.py](../../src/morrow/interfaces/cli.py)，L1996 | 恢复卡 / 诊断页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-094 | morrow run | [cli.py](../../src/morrow/interfaces/cli.py)，L313 | 中心 Chat | [2](../../.agent/subplans/2-core-chat-runtime-and-stream.md) | A01、A02、A05 | [ ] 待实施验证 |
| CLI-095 | morrow serve | [serve_cli.py](../../src/morrow/interfaces/serve_cli.py) | 启动器 / 连接状态；保留 CLI 进程参数 | [2](../../.agent/subplans/2-core-chat-runtime-and-stream.md) | A01、A02、A05 | [ ] 待实施验证 |
| CLI-096 | morrow session archive | [cli.py](../../src/morrow/interfaces/cli.py)，L1415 | 左侧 Session / 会话菜单 | [4](../../.agent/subplans/4-workspace-and-session-management.md) | A03、A13 | [ ] 待实施验证 |
| CLI-097 | morrow session create | [cli.py](../../src/morrow/interfaces/cli.py)，L1325 | 左侧 Session / 会话菜单 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A02、A03 | [ ] 待实施验证 |
| CLI-098 | morrow session fork | [cli.py](../../src/morrow/interfaces/cli.py)，L1436 | 左侧 Session / 会话菜单 | [4](../../.agent/subplans/4-workspace-and-session-management.md) | A03、A13 | [ ] 待实施验证 |
| CLI-099 | morrow session list | [cli.py](../../src/morrow/interfaces/cli.py)，L1295 | 左侧 Session / 会话菜单 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A02、A03 | [ ] 待实施验证 |
| CLI-100 | morrow session resume | [cli.py](../../src/morrow/interfaces/cli.py)，L1370 | 左侧 Session / 会话菜单 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A02、A03 | [ ] 待实施验证 |
| CLI-101 | morrow session status | [cli.py](../../src/morrow/interfaces/cli.py)，L1347 | 左侧 Session / 会话菜单 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A02、A03 | [ ] 待实施验证 |
| CLI-102 | morrow skill disable | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L203 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-103 | morrow skill draft-accept | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L384 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-104 | morrow skill draft-create | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L302 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-105 | morrow skill draft-list | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L280 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-106 | morrow skill draft-reject | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L409 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-107 | morrow skill draft-show | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L325 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-108 | morrow skill draft-validate | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L359 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-109 | morrow skill enable | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L191 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-110 | morrow skill install | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L121 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-111 | morrow skill list | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L48 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-112 | morrow skill pin | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L215 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-113 | morrow skill remove | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L249 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-114 | morrow skill rollback | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L233 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-115 | morrow skill show | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L71 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-116 | morrow skill usage | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L430 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-117 | morrow skill validate | [skills_cli.py](../../src/morrow/interfaces/skills_cli.py)，L99 | 设置 → Skills | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-118 | morrow state backup | [cli.py](../../src/morrow/interfaces/cli.py)，L2100 | 设置 → 状态维护 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-119 | morrow state cleanup | [cli.py](../../src/morrow/interfaces/cli.py)，L2141 | 设置 → 状态维护 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-120 | morrow state doctor | [cli.py](../../src/morrow/interfaces/cli.py)，L2051 | 设置 → 状态维护 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-121 | morrow state events | [cli.py](../../src/morrow/interfaces/cli.py)，L2077 | 设置 → 状态维护 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-122 | morrow state verify-backup | [cli.py](../../src/morrow/interfaces/cli.py)，L2118 | 设置 → 状态维护 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-123 | morrow task accept | [cli.py](../../src/morrow/interfaces/cli.py)，L1584 | Chat 任务卡 / 任务详情 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A03、A04、A10 | [ ] 待实施验证 |
| CLI-124 | morrow task cancel | [cli.py](../../src/morrow/interfaces/cli.py)，L1605 | Chat 任务卡 / 任务详情 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A03、A04、A10 | [ ] 待实施验证 |
| CLI-125 | morrow task list | [cli.py](../../src/morrow/interfaces/cli.py)，L1491 | Chat 任务卡 / 任务详情 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A03、A04、A10 | [ ] 待实施验证 |
| CLI-126 | morrow task new | [cli.py](../../src/morrow/interfaces/cli.py)，L1558 | Chat 任务卡 / 任务详情 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A03、A04、A10 | [ ] 待实施验证 |
| CLI-127 | morrow task resume | [cli.py](../../src/morrow/interfaces/cli.py)，L1625 | Chat 任务卡 / 任务详情 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A03、A04、A10 | [ ] 待实施验证 |
| CLI-128 | morrow task show | [cli.py](../../src/morrow/interfaces/cli.py)，L1468 | Chat 任务卡 / 任务详情 | [3](../../.agent/subplans/3-chat-workspace-and-session-entry.md) | A03、A04、A10 | [ ] 待实施验证 |
| CLI-129 | morrow workflow abandon | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1193 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-130 | morrow workflow cancel | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1155 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-131 | morrow workflow clone | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L530 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-132 | morrow workflow create | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L502 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-133 | morrow workflow disable | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L641 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-134 | morrow workflow edit | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L516 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-135 | morrow workflow enable | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L621 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-136 | morrow workflow list | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L442 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-137 | morrow workflow node show | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1213 | Workflow 节点详情 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-138 | morrow workflow patch apply | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L966 | Workflow Future 编辑 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-139 | morrow workflow patch save | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L940 | Workflow Future 编辑 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-140 | morrow workflow patch validate | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L914 | Workflow Future 编辑 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-141 | morrow workflow pause | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1121 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-142 | morrow workflow plan | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L687 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-143 | morrow workflow policy set | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L752 | 编排设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-144 | morrow workflow policy show | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L740 | 编排设置 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-145 | morrow workflow publish | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L600 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-146 | morrow workflow replan decide | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1251 | Workflow Replan 面板 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-147 | morrow workflow replan list | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1232 | Workflow Replan 面板 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-148 | morrow workflow replan process | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1274 | Workflow Replan 面板 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-149 | morrow workflow rerun | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1094 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-150 | morrow workflow resume | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1137 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-151 | morrow workflow revoke | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L661 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-152 | morrow workflow run | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L770 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-153 | morrow workflow runs | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L457 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-154 | morrow workflow show | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L476 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-155 | morrow workflow status | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L1075 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-156 | morrow workflow validate | [workflow_cli.py](../../src/morrow/interfaces/workflow_cli.py)，L588 | Chat / Workflow 面板及管理页 | [7](../../.agent/subplans/7-workflow-and-cli-parity.md) | A11、A12 | [ ] 待实施验证 |
| CLI-157 | morrow workspace relink | [cli.py](../../src/morrow/interfaces/cli.py)，L789 | 工作区管理 | [4](../../.agent/subplans/4-workspace-and-session-management.md) | A01、A06 | [ ] 待实施验证 |

## REPL 分发入口

以下为 CommandService 当前接受的顶层命令/别名；子操作仍按源码行为逐一迁移。

| 命令 | 目标入口/适配 |
|---|---|
| /new | 新 Session |
| /status | 会话状态 |
| /workspace | 工作区 / Profile |
| /compact | 上下文压缩 |
| /task、/accept | 任务操作 |
| /grant | 权限授权 |
| /recovery | 恢复卡 |
| /learn | 学习 |
| /memory | 记忆 |
| /preference、/preferences | 偏好 |
| /config | 保留原有迁移提示，导向偏好或 Profile |
| /exit | 离开当前交互，不关闭 Core |

运行中的 steering 和 follow-up 是额外交互行为，也必须独立验收。
新增 /model、/thinking、/permissions 等快捷入口不能计入“已有 CLI 能力”的分母。

## 计划覆盖规则

- CLI-ID 固定到 dde2ada 基线，新增入口追加 ID，不因排序改变旧 ID。
- 负责子计划是首次完成该操作 GUI 等价的责任，最终子计划 8 对全部条目做整体验收。
- 已有 CLI/API/部分 GUI 能力不等于新 Chat 布局下已验证；未运行的证据保持待验证。
- 子计划 1 补齐每项实际 Application Service、请求 DTO/路由、子操作及具体测试；
  子计划 7 消除剩余缺口，子计划 8 关闭 A12。
- C01–C16 中的附件、思考、工作区和 Session 新能力独立验收，不掺入既有 CLI 命令分母。

## manage 注册项补充基线

两个 manage 命令入口须继续展开下列真实注册类型；这些类型不重复计入 157 个 Typer 入口。

| 类型 | 当前注册项 | 负责子计划 |
|---|---|---|
| Query | context、preferences、profile、learning、knowledge、skills、skill-drafts、workflow-evaluation、workflow-policy-candidates | 7；Context 基础展示在 3 |
| Command | preferences、profile、preference-decision、learning-decision、knowledge、skill-binding、skill-draft、skill-draft-create、workflow-feedback、workflow-policy-decision、workflow-evaluation | 7 |

来源：[ManagementService](../../src/morrow/application/management.py)、
[Management 请求模型](../../src/morrow/application/management_requests.py)。
子计划 1 还须展开各类型的 action、scope、revision 和现有 CLI 功能差异，不能只测总入口。
