# Subplans

将过大的主计划拆分为按顺序执行的子计划，并将子计划文件放在此目录。

建议命名：

```text
01-foundation.md
02-runtime.md
03-validation.md
```

每个子计划应至少说明：

- 目标和范围
- 前置依赖
- 可执行任务
- 完成标准
- 交付结果

一次只激活和执行一个子计划。子计划完成后，先验证完成标准，再同步 `PLAN.md`、`TODO.md`、`TRACKER.md` 和 `LOG.md`。除非确有必要，不要继续创建嵌套子计划。
