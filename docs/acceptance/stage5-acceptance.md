# Stage 5 当前实现与离线验收证据

> 状态：通用 Preference、Project Knowledge、MemorySelection、doctor/backup 已实现

本文只记录当前实现。历史 fixed-field Preference、旧快照解码、旧 Preference Candidate 晋升桥接和
阶段性评测报告已经移除。

| 场景 | 当前证据 |
|---|---|
| 通用 Preference YAML、一次性迁移、严格当前格式读取 | `tests/test_current_preference_yaml.py`、`tests/test_preference_writer.py` |
| 明确管理、审批、原子写入 | `tests/test_preference_store.py`、`tests/test_preference_proposals.py`、`tests/test_tool_contract_audit.py` |
| 新 AgentRun 重载并冻结 Preference | `tests/test_preference_context.py`、`tests/test_stage5_memory_agent_run.py` |
| Project Knowledge 与 MemorySelection | `tests/test_stage5_project_knowledge.py`、`tests/test_stage5_memory_selection.py`、`tests/test_stage5_memory_inspection.py` |
| Doctor 与 backup 引用验证 | `tests/test_preference_doctor_backup.py`、`tests/test_stage5_memory_inspection.py` |
| Workspace Profile 配置 | `tests/test_configuration_tool.py` |

当前边界：

- `manage_preferences` 是 global/workspace Preference 的唯一显式写入口；SQLite 仅保存审查和写入来源。
- `update_configuration` 只管理 Workspace Profile，不解释 Preference fixed fields。
- 普通加载只接受当前 global schema 2 和 workspace schema 3；旧 YAML 只能在状态门面处一次性迁移。
- AgentRun 只保存通用冻结条目及其 digest，不接受历史 fixed-field 或 singular Skill 引用。
- `state doctor` 保持只读；当前完整 backup 包含 SQLite、当前 Preference/Profile 与扩展
  YAML、Artifact 和被引用的 managed Skill，但不包含 CredentialStore、Keychain 或凭据字节。

标准离线门禁：

```text
uv run pytest -m 'not live'
uv run ruff format --check .
uv run ruff check .
uv run python -m compileall -q src tests
uv run morrow --help
git diff --check
```
