# Luna 实时报告：问题修复与验证

依据：[最新实时控制报告](portable/views/docs/acceptance/computer-use-luna-control-2026-10-04.md)。本轮从 main `35a49216` 开始，修复分支 `fix/computer-use-luna-findings`。原始 request、receipt、图片和判定记录不改写。

## 根因与处理

| 问题 | 根因及本轮处理 | 状态 |
| --- | --- | --- |
| 语义点击看起来没有效果 | 指针坐标 NaN 使 JSONEncoder 中止，状态文件停留在旧 revision。正式 fixture v4 把非有限坐标导出为 null，并提供 positionKnown；计数、按钮回调独立导出。Swift 编码回归实际注入 NaN/Infinity，确认 count/callback/revision 仍能保存。 | 已修复；最新报告的语义点击效果证据成立，SDK unknown 保留 |
| 模型首次调用 discover 已过期 | 选择绑定借用了候选列表 30 秒寿命。确认后的绑定现在不随列表到期清空，消费一次后移除；运行入口核对 process birth，discover/observe/action 仍核对选定 PID、窗口、几何。显式重选、停止、隔离或 shutdown 继续撤销绑定。 | 已修复；120 秒逻辑时钟延迟可开运行，PID 替换、重复消费仍拒绝 |
| 推理期间动作观察过期 | 动作观察仍是单独的新鲜度门禁。新增全局 max_observation_age_seconds（1–30，默认30），CLI 可配置；服务预览/执行与最后 SDK 入口使用相同预算。工具结果给出 captured_at、valid_for_seconds、expires_at。 | 已修复配置与可见性；不默认扩大 TTL、不自动重投 |
| stale 恢复被 action_count 判失败 | 原判定器要求工具调用一次。现在分别输出 action_attempts、sdk_input_entries、recovered_not_started 和 native_completion；只有同 call ID 的 stale/not_started 或确定的 stale preflight 拒绝允许换新观察恢复。旧观察复用、unknown/completed 后重试、缺失关联、两次原生入口均不通过。 | 已修复 |
| 旧 fixture 文件或未知轮事件坐标影响判定 | 新采集要求 schema v4、exportHealthy、有效字段和动作后的 revision 前进。wheel 区域判定遇到 null、NaN、Infinity 或未知位置直接不能建立区域证据，不抛异常，也不改成0。 | 已修复；历史证据不补造健康标志 |
| 4097 字符末尾对模型不可见 | 原公开观察只有前4096字符。新增 value_tail，最多512字符，来自实际 SDK value 的末尾；前缀/尾部/描述/标签共同计入32 KiB总预算，元素引用继续保留。text_appears 可由实际尾部确认正例；截断内容的缺失仍不能证明不存在。 | 已修复公开读回；未运行新的真实 Luna 闭环验收 |
| 引用证明依赖 scripted_target | 新采集按实际模型 call ID/observation ID、公开 ref、受信 registry token 与原生参数 token hash 记录 reference_proofs。不读取 scripted_target；原归档中的 false 不重写。 | 已补齐未来采集 |
| receipt 未接受与中断缺少诊断 | 新增可复用 live_bridge 与独立 run_live_controller 入口，原子写 request/receipt 状态；记录 request/receipt hash、received/accepted、取消/超时/JSON/schema/binding/I/O 错误类别。未接受的 receipt 不加入决定记录；接受也不代表原生执行。 | 已修复桥记录，并保留旧桥和原始错误证据 |

顺带修正 AX 投影循环中复用最后一个节点 depth 的问题，各节点保留自己的深度。没有恢复 password/credential/secure 等工具关键词过滤。

## 仍未解决的能力

- **旧 element_ref 的精确 enabled 读回**：官方0.30.4公开接口没有对旧 token 所绑定对象进行跨快照属性读取的能力；保留 verification_unavailable/not_checked。新观察出现 enabled=true 不充当该对象的精确验证。
- **前台坐标 wheel 与后台双击**：本次实时报告在 Morrow 门禁处拒绝，SDK入口为0，不能据此推导驱动本次实际执行失败。保留门禁的依据来自既有0.30.4直接诊断：正确区域前台 wheel 无事件，后台双击窗口坐标错误。没有通过切换 delivery、改为键盘滚动或两次 AXPress 假装修好。
- **语义滚动容器**：本轮修复选择过期阻塞；SDK 0.30.4仍可能折叠真实容器。不会给普通文本节点伪造容器 ref。是否暴露合法容器及原生按 ref 滚动，仍需新的独立 fixture 验收。
- **真实模型/API复验**：本轮不消耗真实 Provider、不发起原生桌面动作。新的 receipt runner 能用于后续 Luna 控制，但离线通过不替代真实模型和 SDK 矩阵。

SDK核对依据是安装的0.30.4生成接口与已有直接诊断；[官方 SDK 文档](https://github.com/trycua/cua/tree/cua-driver-rs-v0.30.4/libs/cua-driver)供接口定位，主线文档不能证明本地发行包已经修复。

## 验证

| 验证 | 本轮结果 |
| --- | --- |
| Computer-use 定向回归 | 初轮502 passed；最后增加HTTP隔离用例、收紧桥hash检查后，相关36 passed；不相加为不同测试总数 |
| 全套 `uv run pytest -m 'not live' -q` | 最终3118 passed / 2 deselected，202.09秒；首轮3117 passed，200.77秒 |
| Ruff check | 全仓库通过 |
| Ruff format | 本轮34个Python文件通过；全仓库仅用户原有 `evals/benchmarks/run_tb2.py` 格式差异，不修改其内容、不声称全仓库format通过 |
| compileall / CLI / diff | src/tests/evals-computer-use compileall、morrow help、receipt runner help、git diff --check通过 |
| GUI | pnpm typecheck、test、build及bundle预算门禁通过 |
| Fixture | 完整SwiftUI fixture构建成功；独立Swift编码回归确认非有限位置不阻断状态导出；未启动应用 |
| 包 | GUI构建后 `uv build` 成功，生成wheel/sdist |

没有发起新的真实模型调用或原生桌面输入，全部新增测试使用fake SDK、逻辑时钟、合成receipt或独立Swift编码可执行文件。全套离线结果不补造真实模型/SDK复验。

代码提交：`b20c7ff2`（生产观察/选择/fixture）、`60b4f96a`（判定器/receipt桥/未来采集）；文档在同一修复分支交付。按已有授权ff-only合入main并push，最终状态另见本地执行记录。用户原benchmark/docs dirty及staged audit未触碰、未提交；原验收归档未重写。
