# Morrow Code Agent Mini Eval

这是一个面向本地、低成本对照测试的 10 项 Code Agent 评测集。它用于发现明显能力缺口和比较
Morrow Direct、未来 Workflow 与 Pi 等 Agent 的相对表现，不用于生成具有统计意义的公开排行榜。

## 任务构成

| 范围 | 数量 | 难度 | 说明 |
|---|---:|---|---|
| Morrow 历史任务 | 6 | 简单 1 / 中等 2 / 困难 3 | 从真实修复提交提取，包含 CLI、Provider、恢复、持久化、异步 Worker 和 Runtime Policy |
| 外部任务 | 4 | 简单 1 / 中等 1 / 困难 2 | 固定版本的 Aider/Exercism Python 任务，包含实现、文件处理、响应式传播和解释器 |

每个仓库历史任务只记录基线 Commit、Gold Commit、任务说明和验证选择器。准备时通过本仓库 Git
对象生成一份没有历史记录的隔离工作区，因此评测集本身不会保存六份 Morrow 源码。外部任务只保存
必要的 Starter、独立验证器和用于数据集自检的参考实现。

## 快速使用

在 Morrow 仓库根目录执行：

```bash
.venv/bin/python evals/code-agent-mini/eval.py list
.venv/bin/python evals/code-agent-mini/eval.py show MORROW-001
.venv/bin/python evals/code-agent-mini/eval.py prepare MORROW-001 /tmp/morrow-eval-001
```

`prepare` 会：

1. 创建一个新的目标目录；
2. 放入修复前源码或外部 Starter；
3. 写入 Agent 可见的 `TASK.md`；
4. 初始化只有一个 baseline Commit 的新 Git 仓库。

它拒绝覆盖已存在的路径。建议每次使用新的临时目录。

随后把准备目录作为 Agent 的唯一工作空间，将 `TASK.md` 内容作为任务说明。例如 Morrow 可从项目
根目录启动：

```bash
.venv/bin/morrow --dir /tmp/morrow-eval-001
```

Agent 完成后，从 Morrow 仓库根目录运行工作区外的验证器：

```bash
.venv/bin/python evals/code-agent-mini/eval.py verify MORROW-001 /tmp/morrow-eval-001
```

验证器不会写入被测工作区，也不会把 Gold Patch 放入工作区。仓库任务从固定 Gold Commit 读取目标
测试，外部任务使用本评测集维护的独立测试。返回码 `0` 表示通过，非零表示失败。

## 数据集自检

以下命令会对每项任务临时构造 baseline 和 gold 工作区，确认 baseline 失败且 gold 通过：

```bash
.venv/bin/python evals/code-agent-mini/eval.py self-check
```

也可以只检查指定任务：

```bash
.venv/bin/python evals/code-agent-mini/eval.py self-check EXTERNAL-003 MORROW-006
```

自检不调用任何模型，不消耗 Token，也不访问网络。

## 低成本运行方案

日常修改不需要运行全部 10 项：

- Prompt、工具或 Context 修改：选择 2–3 个相关任务，每项运行一次；
- Stage 7 前基线：Morrow 跑完整 10 项一次；
- Pi 对照：先跑 `MORROW-003`、`MORROW-005`、`EXTERNAL-003`、`EXTERNAL-004`；
- Stage 7 Workflow 对照：只对确认可能受益于多节点协作的困难任务比较 Direct 与 Workflow；
- 只有结果不稳定或接近发布门禁时才重复运行。

推荐为单次运行固定相同模型版本、权限、网络条件、时间、Tool Round 和 Token 预算。原生产品能力对比
可以保留各自默认工具；如果目的是比较 Agent Loop，则应另外建立相同工具和沙箱条件的实验组。不要把
两种实验混成一个分数。

## 记录结果

复制 `results-template.csv` 后记录：

- Agent、模型和重复次数；
- 是否通过隐藏验证；
- 总时长、Tool Call 和 Token；
- 用户干预次数；
- 失败分类和非预期文件修改。

请不要用参考实现调优 Agent。仓库历史任务的 Gold Commit 和外部任务的 `solution/` 仅用于
`self-check`；正式运行时 Agent 只能访问 `prepare` 生成的工作区。

## 依赖与来源

- 运行器只使用 Python 标准库、Git 和项目现有的 Pytest，不增加项目依赖。
- 仓库任务不调用真实 Provider、网络、CredentialStore 或用户状态。
- 外部材料的固定来源和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
