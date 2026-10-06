# Morrow 文档索引

Git 仓库公开项目入口、当前架构及明确选定的验收报告/证据。历史验收解释当时的结果，
不替代当前代码规范。未选定的实施计划、指南、研究、审查记录及个人材料是本地档案，默认 ignored。
`.gitignore` 只控制新增文件，不取消已跟踪文件。

Python wheel 由 `src/morrow` 和预构建 GUI 组成，公开 docs 并不作为 wheel 数据发布；
sdist 的文件边界由 Hatch 配置决定，不能用仓库公开政策代替实际包检查。
[验收索引](acceptance/README.md) 列出公开报告，[可移植证据](acceptance/portable/README.md)
保存必要原件、SHA 和阅读副本。清理前先核对报告引用，不能把所有 ignored acceptance 当缓存。

| 入口 | 用途 |
| --- | --- |
| [项目 README](../README.md) | 安装、首次配置与常用命令 |
| [架构总览](ARCHITECTURE.md) | 当前边界、目录职责与关键不变量 |
| [运行时](architecture/runtime.md) | AgentLoop、Workflow、暂停/继续与输出 |
| [状态](architecture/state.md) | Store、写入权威、恢复与备份 |
| [接口](architecture/interfaces.md) | CLI/Core API、事件与 GUI 导航/编辑器 |
| [扩展](architecture/extensions.md) | 工具、Provider、Skills、MCP 与学习 |
