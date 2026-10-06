# 公开验收索引

本索引只列 Git 中选定的验收报告；每份报告记录交付时点，不代表本次重新执行验证。
当前产品合同见[架构基线](../ARCHITECTURE.md)。本地其余 acceptance 是历史或实验档案，
不随本索引自动公开或删除。

## Computer-use

- [computer-use-analysis-and-fix-plan-2026-10-04](computer-use-analysis-and-fix-plan-2026-10-04.md)
- [computer-use-capabilities-2026-10-05](computer-use-capabilities-2026-10-05.md)
- [computer-use-final-review-fixes-2026-10-05](computer-use-final-review-fixes-2026-10-05.md)
- [computer-use-live-2026-10-04](computer-use-live-2026-10-04.md)
- [computer-use-luna-fixes-2026-10-04](computer-use-luna-fixes-2026-10-04.md)
- [computer-use-luna-retest-fixes-2026-10-04](computer-use-luna-retest-fixes-2026-10-04.md)
- [computer-use-recovery-verdict-2026-10-05](computer-use-recovery-verdict-2026-10-05.md)
- [computer-use-repair-2026-10-04](computer-use-repair-2026-10-04.md)

## Benchmark harness

- [benchmark-runtime-limits-2026-09-29](benchmark-runtime-limits-2026-09-29.md)
- [benchmark-v2-2026-09-27](benchmark-v2-2026-09-27.md)
- [harness-fixes-2026-09-27](harness-fixes-2026-09-27.md)
- [tb2-g4-89-failure-audit-2026-09-28](portable/views/docs/acceptance/tb2-g4-89-failure-audit-2026-09-28.md)
- [tb2-g4-repair-2026-09-29](tb2-g4-repair-2026-09-29.md)
- [tb2-verifier-network-2026-09-28](tb2-verifier-network-2026-09-28.md)

## 版本收尾

- [2026-10-06 版本收尾修复](version-closeout-fixes-2026-10-06.md)：逐项修复、验证与公开证据映射。
- [2026-10-06 Grok 复核修复](grok-review-fixes-2026-10-06.md)：管理命令原提交重放、草稿编辑幂等与批量偏好拒绝状态。

## 来源与复算

[Portable evidence](portable/README.md) 和 [SHA-256 manifest](portable/manifest.json) 补齐此前仅在本机的报告/原件。
原始 JSON、raw 判定与修复后 sidecar 分开保留；阅读副本不改变原结论。
公开报告的链接指向可读取副本，sidecar 内部历史 `source` 路径通过 manifest 映射原字节。
真实 API 模型全矩阵仍有余额导致的外部验收缺口；离线复算或 SDK fixture 不能替代该验收。
