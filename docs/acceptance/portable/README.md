# Portable acceptance evidence

此目录保存公开报告所需、此前仅存在于本机的原件。`raw/` 的每份文件均按原字节复制；
[manifest.json](manifest.json) 记录原仓库相对路径、归档路径、SHA-256 和字节数。
`views/` 仅为 Markdown 阅读副本：只增加来源提示并将链接改为相对路径，原报告在 `raw/` 保持不变。
历史判定、原始结果和重算 sidecar 不互相覆盖；本次没有新原生输入或 Provider 请求。

已跟踪的 `evidence/computer-use-recovery-verdict-2026-10-05/recomputed.json` 仍保持原字节。
其中 `source` 指向原路径；可以在 manifest 中用该路径找到逐字节相同的 raw 文件。
需要离线复算时，在新的临时目录按 manifest 的 `source` 路径复制原件，
再按报告列出的源码版本和方法运行；不要把读取副本或 hash 记录当成新的现场验收。
公开导航使用 `views/`，`raw/` 中历史绝对路径仅是原始证据，不作为可移植导航。

此归档是公开报告引用闭包，不是整个本地 acceptance 目录备份。未被引用的本地实验材料仍保留在本机。

SWEbench 被引用的单个解析器及 MIT LICENSE 按原字节保存，来源见 [provenance](swebench-source-provenance.json)。
G4 完整 job 的模型/工具原始流未公开；[来源清单](tb2-g4-job-index.json) 记录本地文件名与大小，
公开汇总、冻结 manifest、原 job aggregate result 和 89 份逐题结构化 result.json 可读取；
这些原件包含记录的得分、失败分类和阶段时间，不包含 agent stream。此范围不等于逐题原始流完整复算包。

在 Git 导出目录运行 [verify_public_evidence.py](../../../evals/computer_use/verify_public_evidence.py)
可检查原件 SHA、恢复 sidecar 来源链和公开本地导航。此检查不运行历史脚本，也不发起输入或请求。
