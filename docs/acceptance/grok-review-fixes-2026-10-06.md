# Grok 审查核实与修复 — 2026-10-06

核实基线：main `638b3b33a14706d9c89d96e96ced659ac61a9015`，原用户 benchmark/docs 工作保留。
范围为 [Grok 报告原文](evidence/version-closeout-2026-10-06/grok-review-original.md) 的两项问题，
延续版本收尾修复，不开启新阶段、不运行 Live/native。
代码修复提交：`d5a38db8`（管理重试与 Draft）和 `30c74f8a`（批量偏好）。
原文 SHA-256：`33561416de7fa82b8908b173a5a1279fb1c42a8cebb772ba82c4b96ad9b5f6ee`。

## 逐项结论

| 条目 | 核实结论 | 修复 |
| --- | --- | --- |
| Issue 1：共享管理重试 | 属实。网络或 5xx 后读回会改变 Skill 启用方向、binding digest、Draft id/row version；原 hook 只在正文完全相同时复用 ID，页面下一次点击会成为新命令。 | 深拷贝并保留完整原命令：kind、target、body 和 command_id。提供“重试原操作”，结果未知时阻止新的不同提交；Skill 写控件禁用但列表/历史仍可查看。成功或明确 4xx 后解除等待。 |
| Issue 1：Draft 服务细节 | `edit` 忽略 command_id 属实，但“原命令必然重复写一行”不准确：直接调用同命令重试在基线报 StorageError，唯一修订约束挡住重复；GUI 管理层已有事务回执。真正的 GUI 重复修订风险是针对后继草稿发出新命令。 | 保留管理层原回执，补齐公开 edit 服务的幂等回执；同 ID 同请求重放，同 ID 不同目标/正文/文件拒绝。内层命令 ID 派生，避免与管理层回执冲突。没有增加表或 DDL 约束。 |
| Issue 2：批量偏好结果文案 | 属实。submit 的 false 同时表示明确拒绝和未确认，批量面板没展示现有 batch row 错误信息。 | 复用 batch row 信息展示结果：400/403/422 显示拒绝原因，409 明确内容变化导致拒绝；网络/503 才显示未确认。保持现有 boolean 写接口、输入和原修订重试规则。 |

读回的启用开关或新 Draft 只说明当前事实；界面不会仅凭它们声称原命令已提交。
“重试原操作”用服务端命令回执确认原提交结果，确认后才允许新的相反操作。
未确认请求保存在当前页面 hook 内，沿用现有内存保留范围；未增加跨浏览器重载的持久化队列。

## 改动与回归

- [共享管理 hook](../../gui/src/views/management/hooks.ts)、[工具页](../../gui/src/views/tools/WorkspaceToolsPage.tsx)、[Skill 页面](../../gui/src/views/SkillManager.tsx) 和知识页采用同一原命令重试入口。
- [真实工具页 DOM 测试](../../gui/src/views/tools/WorkspaceToolsPage.dom.test.tsx) 覆盖：enable 已提交后网络/503 丢回复，读回已启用仍重放原 enable；Draft 编辑读回后继修订仍发送原 URL、正文、row version 和 ID。
- [Hook 测试](../../gui/src/state/managementWrites.dom.test.tsx) 覆盖深拷贝、不同命令阻止、React 渲染前双击，以及明确 409 后新命令 ID。
- [Draft 服务](../../src/morrow/application/skills/drafts.py) 与 [管理层](../../src/morrow/application/management.py) 的 [回归](../../tests/test_skill_drafts.py) 覆盖关闭重开数据库后重放、不同请求冲突、事务内回执重查时临时包清理，以及 HTTP edit/validate/reject 原命令重放先于 stale 检查。validate/reject 无需新增服务实现。
- [批量偏好 DOM 测试](../../gui/src/views/preferences/BatchPanel.dom.test.tsx) 覆盖 400/403/422/409/network/503；确认输入保留、原 revision 保留、未知结果复用 ID、明确拒绝重新生成 ID。

两名 subagent 分别负责 Draft 和批量偏好；另对完整管理重试改动作独立只读复核，未发现阻断问题。
本轮没有新框架、存储层或能力开关。

## 验证

- Python 全量离线：3205 passed、2 deselected，211.98 秒。
- GUI：typecheck、110 files / 747 tests、build 与原 bundle budget 全部通过。JS 1783.2 KiB / 1900 KiB，CSS 119.5 KiB / 120 KiB。
- 后端相关回归：53 passed；全部纳入最终全量门禁。
- Ruff format/check、compileall、CLI help、git diff --check：通过。
- 本轮未执行新 wheel 安装 smoke；前轮安装证据仍是其当时的代码快照，本轮不以其替代新的功能回归。

验证原始日志及 SHA 见 [manifest](evidence/version-closeout-2026-10-06/manifest.json)，
其中 `grok-*` 为本轮增量，前轮日志原字节保留。
既有真实 API 模型矩阵余额缺口保持原状态，不由本轮离线验证改写。
