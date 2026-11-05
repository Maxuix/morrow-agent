# 代码修改审阅与撤销 M1 验收记录

日期：2026-11-06（Asia/Shanghai）。用户明确选择完整 M1（执行书 E0～E7）。
基线为 fetch 后的 `main 697581f8`，隔离分支 `feat/code-review-m1`。
原 `feat/four-metric-evaluation` 的 staged/unstaged 内容未移入本功能，也未重置。
本报告不宣称 M2 修改块/结构恢复或 M3 任意 Shell 撤销已实现。

## 交付与证据

| 工作包 | 交付/证据入口 |
| --- | --- |
| E0 生产入口和发布原语 | `tests/test_workspace_change_factory.py`：生产 factory + 脚本 Provider 跑普通聊天、Workflow 写文件；`test_workspace_restore_publication.py`：无覆盖发布、目标抢占、父目录更换、打开描述符继续写入 |
| E1 契约、快照、迁移 | `core/workspace_changes.py`、schema 50 两表；内部原字节快照不能走普通 Artifact 读取；`test_workspace_change_storage.py`、`test_workspace_change_upgrade.py` 验证空文件/BOM/CRLF、敏感拒绝、引用、备份恢复与迁移回滚 |
| E2 统一修改记录 | `WorkspaceChangeRecorder` 在副作用前持久化 A/B 与意图，随后核对；factory 测试覆盖三类入口，包括双文件沙箱提升及记录/快照失败时不写入 |
| E3 恢复与核对 | `WorkspaceRestoreService`；`test_workspace_restore.py` 覆盖再次恢复、连续链/间隙、真实 Git index 保持、人工编辑/chmod/新 inode、预览过期、部分成功、写后中断及不重放核对 |
| E4 API/CLI/事件 | `workspace_change_routes.py`、`changes_cli.py`；测试验证任务/会话作用域、命令重放、审阅独立、固定快照、摘要确认和同 Core 执行 |
| E5 审阅 UI | 任务“查看变更”、文件净变化/时间线、历史 A/B 两侧 Diff、当前 C、覆盖与增删行、独立审阅状态；分页不伪装完整 |
| E6 恢复 UI | 预览与确认分开；默认文件视图选整条已加载链；逐文件结果、断线查询原操作、再次恢复及历史测试提示；DOM 测试包含重复点击、源码文本安全显示、丢响应后重挂载 |
| E7 兼容与故障 | schema 升级生成既有备份并回滚失败；快照保留图和备份往返；doctor 只读审计；源文件 API/恢复共用占用判断，cancelled Host 仍按真实进程存活阻止恢复 |

恢复不修改历史工具事实、Git index 或 HEAD；恢复本身形成新修改记录。会话历史仍只有
Session 的 ConversationLog 写入器；恢复事实通过下一轮上下文装配提示，不改写旧消息。

## 已执行验证

最终应用/测试代码冻结在 `69815aa8`。本轮最终完整离线回归：
`uv run pytest -m 'not live' -q`，**3245 passed / 2 deselected，210.46 秒**。
以下门禁均在该应用/测试代码上实际执行；后续仅收尾文档与 Git 交付。

- `pnpm --dir gui typecheck`：通过。
- `pnpm --dir gui test`：**751 passed / 111 files**。
- `pnpm --dir gui build`：通过；JS **1797.4 KiB / 1900 KiB**、CSS **119.7 KiB / 120 KiB**。
- `uv run ruff check .`、`uv run ruff format --check .`：通过（812 files）。
- `uv run python -m compileall -q src tests`：通过。
- `uv run morrow --help`、`uv run morrow changes --help`、`uv run morrow changes restore --help`：通过。
- `git diff --check`：通过；新文档相对链接存在性核对通过。

真实浏览器使用本地 Vite 页面和脚本 API，检查 **390×844、768×1024、1440×1000**：长路径
可换行、按钮可达、历史 Diff 可读、预览/确认分开、结果与再次恢复入口可见。这是布局与
交互验收；真实 Core 的鉴权、命令与落盘由 API/factory 离线测试证明，不能相互替代。

第一轮 Python 全量 **6 failed / 3228 passed / 2 deselected**：四项是升级测试把当前 schema
硬编码成 49，已改用 `SUPPORTED_SCHEMA_VERSION`；两项是 clean worktree 缺少本地忽略的
历史验收材料。另有首次 collection 缺少 `evals/code-agent-mini/eval.py`。从原工作区复制
对应本地评测目录和 11 份证据，仅用于重现原仓库测试环境，未把这些忽略材料夹带提交。
后续全量在该完整测试材料环境中运行。未运行 Live、真实模型、真实网络或桌面设备操作。

## 容量与测量

固定环境：macOS arm64、Python 3.13.0、本地临时文件系统。每大小 20 次，单文件，包含
无覆盖保全文件，不含模型、网络与 GUI。时间仅是本机观测，没有用 wall-clock sleep 断言正确性。

| 文件大小 | 快照/账本 p50 / p95 | 预览 p50 / p95 | 恢复 p50 / p95 |
| --- | --- | --- | --- |
| 8 KiB | 5.692 / 7.824 ms | 1.710 / 2.081 ms | 3.828 / 5.734 ms |
| 256 KiB | 37.450 / 39.693 ms | 3.045 / 3.290 ms | 7.156 / 8.489 ms |

快照/账本列是构造已知 A/B 的合成发布，不冒充生产模型整轮延迟；预览和恢复调用同一业务服务。
GUI build 现有预算曾因新增样式超约 0.2 KiB 失败，复用已有工具类后通过，未放宽预算。

## 明确边界

- 本轮实测 macOS arm64；Linux 无真实 runner 验收，不宣称验证跨平台发布。缺少可靠原语时恢复拒绝。
- 自动恢复只覆盖普通受控 UTF-8 创建/编辑/替换和同类沙箱提升。结构修改、二进制、敏感原文、
  链接、任意 Shell、人工 API 与外部编辑有明确降级；旧任务不伪造恢复材料。
- 不保证任意不合作进程持续写入时的全局隔离。保全文件保留打开描述符的后续字节；结果未知时
  需要核对，不能删除捕获文件。保全文件另占工作区磁盘空间。
- 普通快照默认 30 天恢复资格，可固定；引用不因时间或容量压力自动删除。首版未提供批量释放
  修改账本引用或后台自动回收；2 GiB 工作区额度耗尽会拒绝新的受保护写入。
- 多文件预检不是跨文件事务；中途失败准确报告已完成/失败/未执行，启动与重试核对不继续写文件。
- 捕获、恢复可分别关闭；只读历史和核对保持可用。生产入口与开关说明见[使用与架构](../architecture/code-review-undo.md)。

## Git 交付

代码/测试验证提交依次为 `9088d526`、`15936099`、`0e99ed54`、`98ab0f3b`、
`4519d82f`、`4a48445b`、`69815aa8`。本报告与使用指南另作收尾文档提交；
交付按仓库规则使用 main 快进历史，未制作发行包、未升级版本。原评测分支及其未提交内容保留。
