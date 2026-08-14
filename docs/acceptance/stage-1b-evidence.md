# Stage 1B 验收证据

日期：2026-08-13

| 门禁 | 自动化证据 |
|---|---|
| S1B-01 | `tests/test_context_runtime.py` 覆盖三层标量/指令合并；`tests/test_preferences_and_orchestration.py` 覆盖 unset、快照刷新和下一轮可见。 |
| S1B-02 | `tests/test_preferences_and_orchestration.py` 覆盖 must-not-trigger、must-trigger、混合输入、敏感字段零写入和一次结构化提取。 |
| S1B-03 | `CommandService` 与终端状态转换覆盖干净/dirty `/new`、`/continue`、独立退出确认、保存失败保留原会话；配置自然语言请求先预览确认；Handoff 取消测试证明不兜底不写。 |
| S1B-04 | `tests/test_state_and_workspace.py` 覆盖 writer lock、revision conflict、corrupt/future schema、失败 replace、`.bak` 读取和旧文档保留；清除操作在锁内完成检查并保留备份。 |
| S1B-05 | `tests/test_provider.py` 覆盖添加、激活策略、重新配置、全局 Preferences 保留；`test_cli_commands.py` 在 NetworkGuard 下验证本地 Provider/model 命令。 |
| S1B-06 | `tests/test_state_and_workspace.py` 覆盖 relink 保留原 ID/状态；Profile/Handoff 的清理路径分别操作各自文档。 |

## 质量门

```text
uv run ruff format --check .                 PASS
uv run ruff check .                          PASS
uv run pytest -m 'not live'                  PASS (48 passed, 1 expected live deselection)
```

所有默认测试使用临时状态根，socket 访问由 `tests/conftest.py` 阻断；没有默认联网、真实钥匙串、用户主目录或项目内容读写。

## 外部验收进度

- 真实 OpenCode Go 请求已通过：`pytest -m live` 返回 `1 passed, 48 deselected`。
- 已完成真实临时工作空间启动、Provider 引导、首次 Profile/Handoff 写入、独立会话确认、显式 `/continue`、`/handoff update`、长回答 Ctrl+C、Ctrl+D 退出，以及第二个临时目录的 `workspace relink`。
- 初次人工运行发现并修复了初始 Handoff 自动加载问题；修复后的完整流程已由 Codex 直接操控终端复验。
