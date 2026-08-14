# Stage 1A 验收证据

日期：2026-08-13

## 自动化证据

| 门禁 | 证据 |
|---|---|
| S1A-01 | `tests/test_provider.py` 验证先测试后发布、凭据只进入 `MemoryCredentialStore`；状态 YAML 不包含凭据哨兵。 |
| S1A-02 | `tests/test_context_runtime.py` 验证原序历史、流式分片顺序和完整助手消息准入。 |
| S1A-03 | `tests/test_context_runtime.py` 与 `src/morrow/interfaces/spike.py` 验证首次取消关闭 producer、完成原因为 `cancelled`、之后可继续；真实 REPL 使用同一 task-cancel 模式。 |
| S1A-04 | `tests/test_state_and_workspace.py` 验证候选确认、Git 根目录/嵌套仓库边界、隔离、relink、路径元数据边界和工作空间单写者锁。 |
| S1A-05 | `tests/test_context_runtime.py` 验证未显式加载时 Handoff 不进入上下文，加载 revision 后才进入。 |
| S1A-06 | `tests/test_structured_and_handoff.py` 验证模型失败/Schema 失败的确定性 Handoff 兜底、原子发布、取消不兜底不写入、失败写入保留旧状态。 |
| S1A-07 | `tests/test_core_contracts.py` 与 `tests/test_context_runtime.py` 验证五类事件、严格递增 sequence、取消/错误完成语义和未知字段容忍。 |
| S1A-08 | `tests/test_core_contracts.py` 验证第二个 Fake Adapter 动态注册；`tests/test_core_contracts.py` 验证 core 不导入 SDK、CLI、渲染、YAML、keyring 或锁库。 |

## 命令与质量证据

```text
uv run morrow --help                         PASS
uv run ruff format --check .                 PASS
uv run ruff check .                          PASS
uv run pytest -m 'not live'                  PASS (48 passed, 1 expected live deselection)
```

默认测试使用临时状态根、固定 ID/时钟、内存凭据和 socket NetworkGuard，不访问用户 `~/.morrow` 或外部网络。

## Live/人工清单

- `tests/test_provider.py::test_live_provider_streams_visible_text_without_reasoning` 已在用户本机通过：`1 passed, 48 deselected`。
- 用户在修复后由 Codex 直接操控真实终端完成了完整人工流程：首次启动时 Handoff 只展示不自动加载；普通消息后 `/exit` 出现独立会话丢弃确认；重启后 `/continue` 显式加载 revision；`/handoff update` 成功；长回答 Ctrl+C 后回到提示符并可继续对话；Ctrl+D 正常保存并退出。
- 第二个临时目录通过 `workspace relink` 成功迁移，原 workspace ID 和 Handoff revision 保持有效。
- 初次人工运行曾暴露自动加载初始 Handoff 的缺陷，已修复；修复后的运行结果才计入本项验收。
