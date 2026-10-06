# Morrow 文档索引

项目使用入口是[中文 README](../README.zh-CN.md) 和 [English README](../README.md)。
当前架构描述代码职责和边界，验收报告记录指定修订的结果；历史计划和提案不替代当前代码。

## 按任务查找

| 任务 | 入口 |
| --- | --- |
| 安装、配置模型、启动终端或 Web GUI | [中文 README](../README.zh-CN.md) / [English README](../README.md) |
| 理解模块、所有权与依赖方向 | [架构总览](ARCHITECTURE.md) |
| 查看版本收尾与后续复核修复 | [公开验收索引](acceptance/README.md)、[最新 Grok 核实修复](acceptance/grok-review-fixes-2026-10-06.md) |
| 核对历史证据原字节与引用 | [Portable evidence](acceptance/portable/README.md) |
| 使用 Benchmark harness | [TB2 协议与评测入口](../evals/benchmarks/README.md) |
| 核对安装包或显式执行受控桌面验收 | [Computer-use gates](../evals/computer_use/README.md) |
| 构建 wheel/sdist | [README 构建说明](../README.zh-CN.md#构建分发包)、[构建脚本](../scripts/build_release.py) |

## 架构专题

| 问题 | 入口 |
| --- | --- |
| Agent 输入、工具循环与 Workflow 如何推进？ | [运行时](architecture/runtime.md) |
| 哪些状态持久化，如何恢复和清理？ | [状态](architecture/state.md) |
| CLI、Core API 与 GUI 如何共享业务服务？ | [接口](architecture/interfaces.md) |
| Provider、Skills、MCP 与学习如何接入？ | [扩展](architecture/extensions.md) |

## 仓库与分发边界

Git 公开项目源码、测试、GUI、评测工具、当前架构，以及明确选定的验收报告和证据。
[验收索引](acceptance/README.md) 列出报告；[可移植证据](acceptance/portable/README.md)
保存必要原件、SHA-256 和阅读副本。报告按日期和修订解释验证范围，不把原始判定改写为后来结果。

未选定的实施计划、指南、研究、审查记录及个人材料留作本地档案，默认 ignored。
运行缓存、Benchmark jobs/runs/assets、依赖环境和生成的 GUI bundle 也不随 Git 提交。
`.gitignore` 只控制新增文件，不取消已跟踪文件；清理前先核对引用，保留仍用于复算的原件。

Python wheel 由 `src/morrow` 和预构建 GUI 组成，公开 docs 不作为 wheel 数据发布。
sdist 的实际文件边界由 [Hatch 配置](../pyproject.toml) 决定，GUI 先构建后再打包；
公开仓库范围与分发包范围分别检查。
