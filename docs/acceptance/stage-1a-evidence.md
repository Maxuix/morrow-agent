# Stage 1A 验收证据

> 状态：2026-08-14 最终修复树的离线、Live 与真实终端验收全部通过；Stage 1A 完成。

## 最终树自动化证据

| 门禁 | 观察到的证据 |
|---|---|
| S1A-01 | `test_provider_onboarding_publishes_model_after_explicit_test`、凭据哨兵断言与 NetworkGuard 通过；临时空状态根中的真实 Provider 引导、环境凭据解析与模型初始化通过。 |
| S1A-02 | `test_ten_turns_preserve_ordered_full_history_and_stream_deltas` 明确验证十轮完整原序历史与每轮分片顺序。 |
| S1A-03 | 取消、`completed(cancelled)`、部分助手不入历史及取消后继续对话测试通过。 |
| S1A-04 | Git 根、嵌套仓库、别名、非 Git 父目录、不同 worktree、重复/并发 claim、relink 与隔离测试通过。 |
| S1A-05 | 未显式加载不注入、有效 `/continue` 加载、cleared/降级拒绝继续测试通过。 |
| S1A-06 | 正常/失败/超时/无效结构、独立/接力 fallback、clear/recreate、原子失败与备份测试通过。 |
| S1A-07 | 正常、取消、Provider 抛错、截断、缺失 finish、malformed stream 的一开始/一完成生命周期测试通过。 |
| S1A-08 | 第二 Fake Adapter、core 依赖边界、Stage 2 模块边界、无项目写入与无产品子进程测试通过。 |

## 2026-08-14 离线命令

```text
uv run pytest -m 'not live' --strict-markers   PASS (149 passed, 1 deselected)
uv run pytest --collect-only --strict-markers  PASS (150 collected; Live registered)
uv run ruff format --check .                   PASS (69 files)
uv run ruff check .                            PASS
uv run python -m compileall -q src tests       PASS
uv run morrow --help                           PASS
package/import smoke                           PASS
boundary/multiprocess/terminal/CLI subset      PASS (55 passed)
```

## 2026-08-14 最终树 Live 与人工证据

- 显式 Live：`.venv/bin/pytest -m live --strict-markers -q` 通过（`1 passed, 149 deselected`）。脱敏真实流边界统计观察到 5 个正文分片、恰好 1 个 `completed(stop)`、0 个错误、0 个公开 `reasoning`/`reasoning_content` 字段。
- 空状态真实终端：在临时 state root 和空项目中完成 Provider 引导；启动只展示 onboarding Handoff，未自动加载。连续十轮依次返回 T1–T10；长回答中 `Ctrl+C` 后新回合返回 `AFTER-CANCEL`；`/handoff update` 成功发布合法降级 Handoff；`Ctrl+D` 正常退出。
- 身份与隔离：两个普通临时项目获得不同 ID；两个真实 Git worktree 分别获得 `ws_w8uq8ZFRCceSkADs` 与 `ws_jnTJraN_31hYX0X1`。项目文件哈希与 `git status --porcelain` 保持不变。移动 project A 后 relink 保留 `ws_lpOgU4Tj8RRnvvlZ`、Profile 与 Handoff revision 2，并只在显式 `/continue` 后加载。
- 离线恢复：已加载 revision 2 的接力会话在不可达代理下收到脱敏网络错误；`Ctrl+D` 明确使用确定性降级交接保存。恢复网络后启动展示 revision 3，显式 `/continue` 后状态为已加载。
- 密钥与边界：临时 YAML、备份、状态树、仓库文本和项目目录的精确密钥哨兵扫描通过；PTY 未回显密钥。普通项目保持空，两个 worktree 保持干净；未观察到 Morrow 项目内容写入或产品 Git/Shell 子进程。
