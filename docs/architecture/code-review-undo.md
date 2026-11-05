# 文件变更审阅与撤销（M1）

Morrow 为普通聊天、Workflow 和受支持的沙箱文本提升保存逐次修改记录。任务结果的
“查看变更”打开专用面板；默认按文件显示连续净变化，也可以切换修改时间线。
“标记已审阅”只记录阅读状态；撤销文件、接受任务结果和 Git 提交是不同操作。

## 使用

1. 打开任务结果的“查看变更”，选择历史 Diff。A/B 是这次修改前后的历史版本，
   “比较当前文件 C”打开磁盘正文。覆盖提示说明是否还有命令影响未完整捕获。
2. 默认文件视图选择该文件已加载的修改链；时间线可选择单次修改。存在分页时先加载
   所需记录。中间有外部变化、未选择的记录或其他任务修改时，不会强行合并。
3. 选择一个或多个文件，点击“预览撤销”，检查各文件的 B → A 目标，再确认恢复。
   执行会重新校验字节、模式、文件与目录身份；变化后拒绝旧预览。
4. 结果逐项显示已恢复、冲突、未执行或需要核对。断线后用“查询原操作状态”查询同一
   操作；不要另造命令 ID 重做一次。需要核对时只对照意图和磁盘，不自动继续文件写入。
5. 成功后可“预览恢复刚才撤销的内容”。再次恢复遵守相同校验，旧测试需重新运行。

终端通过已有 Core 操作，不能另起无锁文件写进程：

```bash
morrow changes list --session <session_id> --task <task_run_id>
morrow changes diff --session <session_id> --task <task_run_id> --change <change_id>
morrow changes restore-preview --session <session_id> --task <task_run_id> \
  --change <change_id> --command-id cmd_preview1
morrow changes restore --session <session_id> --preview <operation_id> \
  --preview-digest <preview_digest> --command-id cmd_restore1
morrow changes restore-status --session <session_id> --operation <operation_id>
```

`--change` 可重复。恢复命令在交互终端展示计划并确认；脚本需同时传入预览摘要和 `--yes`。
连接失败时先查询输出的操作身份，再决定是否用原请求重试。没有运行中的 Core 时命令拒绝，
不会回退成一个独立写入器。

## 支持与保护边界

| 来源/状态 | 审阅与恢复 |
| --- | --- |
| 受控文本创建、精确编辑、替换、同类沙箱提升 | 完整快照；当前状态吻合时支持文件级恢复 |
| BOM、LF、CRLF、无末尾换行、空文件、执行位 | 保存原字节与模式，不从 Diff 反推原文 |
| 后续编辑、chmod、同内容新 inode、父目录/工作区替换 | 旧预览拒绝；不会覆盖未知当前内容 |
| 删除、移动、重命名、二进制、敏感内容、链接 | 只保留可用元数据和明确原因，不提供自动恢复 |
| 任意 Host 命令、外部编辑器、人工文件 API | 不冒充本任务受控修改；命令影响提示不等于捕获保证 |
| 旧任务只有路径/Diff，或快照缺失、损坏、过期 | 只读降级，不从当前文件伪造历史版本 |

受控捕获开启后，快照或账本失败会在写文件前失败。捕获与恢复开关独立：启动进程时设置
`MORROW_CAPTURE_CHANGES=0` 可关闭新捕获；`MORROW_RESTORE_CHANGES=0` 可关闭恢复写入。
既有记录、历史 Diff 和中断核对保持可读。关闭捕获后的新任务没有恢复保障。

首版额度：单文件每侧 8 MiB；单任务所有 Artifact（含快照）共享 256 MiB；工作区快照
2 GiB；单次恢复最多 50 个文件、128 MiB 计划。列表默认 50、最多 200，游标固定视图；
Diff 按页读取。容量不足不会静默降级为“已备份”。

普通快照的恢复资格窗口为 30 天，可在面板中“固定恢复快照”。引用保护与时间窗口分开：
过期不自动删除原文，活跃修改/恢复引用仍被保留。当前没有自动释放历史修改引用的后台任务；
达到工作区额度后会拒绝新的受保护写入。首版不提供批量删除修改账本来腾出额度的功能。

恢复使用同目录无覆盖捕获与发布。旧目录项保存在文件所在目录的
`.morrow-restore-…-capture`；失败时可能还保留 `…-new`。结果列出相对位置。
这些文件保留外部编辑器已打开描述符的后续写入，不会被恢复流程顺手删除；核对后再由用户
管理其保留，不能把它们当作无条件可删的缓存。它们独立占用工作区磁盘空间。

macOS arm64 已完成真实文件系统原语和响应式浏览器验收。Linux 使用可用的 `renameat2`
原语，但本轮没有真实 Linux runner 验收；缺少无覆盖/目录句柄能力的平台拒绝恢复。
不保证任意不合作进程持续写入时的全局事务隔离；明确的竞态点保全原文并报告不确定状态。

## 所有权与持久化

- `WorkspaceChangeRecorder` 在 SQLite owner 上先发布完整 A/B 内部快照和 prepared 记录；
  文件副作用在受等待保护的 worker 上执行，再回 owner 核对。普通聊天仍走 `AgentLoop.run_task()`。
- `WorkspaceChange` 绑定工作区、会话、任务、AgentRun、ToolExecution 和修改序号。沙箱一次
  调用的多个文件各有独立记录。Workflow 旧 ChangeCapture 从这些快照派生。
- schema 50 增加 `workspace_changes`、`workspace_restore_operations`，状态是普通 TEXT/JSON，
  枚举由 Pydantic/Journal 验证。升级先生成既有 SQLite 备份；失败回滚，不能删除数据库重建。
- 内部快照拒绝敏感原文，不能先脱敏再恢复；普通 `read_artifact`、任务产物与模型读取入口
  不能读取它们。变化/恢复引用加入既有 Artifact 保留图，备份包含字节，doctor 只读审计。
- 恢复操作有稳定身份、5 分钟预览、命令幂等、逐项进度及补偿记录。预检全通过不代表批次
  原子：首个中途失败后停止，已完成项不自动反向重做。
- 恢复和人工源码替换使用 Core 同一写入占用判断，包含真实存活的后台 Host 进程；
  cancelled 不等于进程退出。Session 的 ConversationLog 仍是聊天历史唯一写者。
- 新一轮上下文会提示工作区恢复事实；任务产物将旧测试标为历史版本。恢复不改写旧工具
  执行事实，也不改变 Git index 或 HEAD。

验收证据见 [M1 验收报告](../acceptance/code-review-undo-m1-2026-11-06.md)。
