# Computer-use 五项能力补齐（2026-10-05）

结论：最新复测中的五项功能已完成驱动实现、Morrow 接入和独立原生 fixture 验证，最终构建 **5/5 passed**。每案只进入一次 SDK 输入，原生完成均为 `unknown`，实际 delivery 与请求一致；两项 enabled 的精确后置条件均 `passed`。本轮没有恢复内容关键词分类、字段禁读、截图遮罩或已退役的 guarded SDK。

## 根因与实际修复

| 场景 | 原未通过原因 | 补齐内容 | 最终独立证据 |
| --- | --- | --- | --- |
| 前台 enabled 精确读回 | 官方 0.30.4 没有旧 token 对应对象的属性读取接口；新树不能证明旧对象身份 | 增加固定 `read_element_attribute`，从缓存保留同一 AX 对象，核对 PID/窗口，在新观察前读取 Boolean enabled | 1 次点击、count +1；旧 token 哈希一致，exact/readable/true，后置条件 passed，delivery=foreground |
| 后台 enabled 精确读回 | 同上 | 同一接口及验证链，保持后台语义点击 | 1 次点击、count +1；exact/readable/true，后置条件 passed，delivery=background |
| 前台坐标滚轮 | 官方前台 HID 路径在此宿主未收到 wheel；此前由 Morrow 门禁拒绝 | 在既有选定窗口置前校验后，发送单个 PID/window wheel 流；功能版才解除对应门禁 | 区域内 3 次 wheel，每次 deltaY=-1；scrollOffset 0→30，delivery=foreground |
| 后台元素左键双击 | 官方 AppKit 坐标/投递组合失效；语义激活不能替代物理双击 | 保留真实元素 token，使用实际窗口局部坐标、一个 public PID 流；发送两个原生鼠标事件对，禁止 AXOpen 替代 | clickCount=[1,2]，count +2，局部坐标 (80,382)，delivery=background |
| 语义滚动容器 | SDK 树折叠 AXScrollArea/AXScrollView，无法取得合法容器引用 | 只折叠布局 AXGroup，保留实际容器和 token；原生容器采用单个 PID/window 流，避免双流合并加倍 | 公开 axscrollarea/fixture-scroll；观察 token=派发 token；0 张图、区域内 3 次 wheel，offset 0→30，delivery=background |

Morrow 精确验证读取发生在原动作之后、新窗口快照之前；验证失败不会掩盖已经发生的输入。对象失效或属性不可读仍为 unavailable，不能换成同名对象。Application 保留已有精确属性结果，同时继续采集动作后的新观察。原来的 process birth、窗口归属、图像几何、审批、观察寿命和消费一次规则继续生效。

## 构建与安装

使用官方源 commit `bf6c76786d938070f4ecf1e44004752f69f518b8`，tag `cua-driver-rs-v0.30.4`，加上仓库 [functional.patch](../../vendor/cua-driver-functional/functional.patch)。最终分发版本为 **0.30.4+morrow.3**，macOS arm64 的 `computer-use` extra 指向固定 wheel，其他平台继续使用官方 0.30.4 并保持 unavailable。

[发布构建、源码、原生证据与 checksums](https://github.com/Maxuix/morrow-agent/releases/tag/cua-driver-morrow-v0.30.4.3)；[复现步骤](../../vendor/cua-driver-functional/README.md)；[构建来源证据](evidence/computer-use-capabilities-2026-10-05/build-provenance.json)。构建使用上游 Cargo.lock、原 Python/UniFFI bindings，并检查两个原生载荷的签名。没有借用退役 SDK 的原生实现。

- patch SHA-256：`d269b57ed44653e515855e359899c73d9e7ccd40070d412e2816f0cc8ac733bf`
- wheel SHA-256：`f0b544a1ec4eed63085cfa3226c6436494f4705441c7abf97f564e555d5d9f7e`
- dylib SHA-256：`dc35e83565283558c7ee46e08ff834bcd36eaebf56855084dae200985c557703`
- CLI SHA-256：`298903433c53dafadb5f838d8015fc35b5d741a894edbb6d60364bac6c63023a`

```sh
uv sync --locked --extra computer-use
```

wheel 的部署目标为 macOS 14 arm64；本次现场宿主为 macOS 27.0.1，Python 3.13。部署目标不等于已验证所有较旧 macOS 宿主。

## 验证方法及证据边界

最终统一门禁使用 [run_capabilities_fixture.py](../../evals/computer_use/run_capabilities_fixture.py)：每案一个新 Python host 和独立 AppKit fixture 实例，经普通 AgentLoop、ToolExecutor、审批和真实 SDK 执行。脚本从公开工具结果选择实际引用/截图，并用独立 fixture 状态核对效果；不是 mock SDK。所有事件实际发生，健康 schema v4 导出与新 revision 均有效。最终五案均 **一次动作尝试、一次输入、零恢复、零重投**。

[最终五案完整汇总](evidence/computer-use-capabilities-2026-10-05/campaign.json) 保留 `scripted_provider=true`。该统一门禁是可重复的原生回归，**没有宣称本轮运行 Luna max 或真实 API 模型**。此前本轮 root Codex 还通过 receipt-only LiveController 实时读取真实工具返回/截图并控制候选构建，保留 [前台属性](evidence/computer-use-capabilities-2026-10-05/live-enabled-foreground.json)、[后台属性](evidence/computer-use-capabilities-2026-10-05/live-enabled-background.json)、[前台滚轮](evidence/computer-use-capabilities-2026-10-05/live-wheel-foreground.json)、[后台双击](evidence/computer-use-capabilities-2026-10-05/live-double-background.json)、[语义滚动](evidence/computer-use-capabilities-2026-10-05/live-scroll-semantic.json)。这些较早候选证据的双击 delivery 曾不可判定，不能作为最终构建回执证明。

SDK dispatch 仍没有独立效果确认，因此原生 `unknown` 没有被提升为 completed。fixture 的效果通过、属性后置条件通过与原生完成状态分别记录。后台坐标双击、右键双击未在本次补齐范围，继续 unsupported；官方未补丁版本的原门禁继续保留，不自动改换手势或 delivery。

真实生产 API Provider 全矩阵仍缺余额，未发起外部模型调用。该外部验收缺口与此次五项驱动功能补齐分开记录。

## 候选失败与纠正

原始 2026-10-04 复测归档保持原字节。此次调试中的失败同样保留，未覆盖成通过：

- fixture 直接执行 app 二进制虽创建窗口，却无法稳定注册 AX/前台身份；启动超时或 ax_window_unresolved 的候选没有原生输入。改用 LaunchServices `open -W -n -a`，依据独立状态绑定真实 PID，并检查可执行路径及 process birth；关闭只针对本案 fixture，不把 launcher 当实际应用。
- 首次属性工具未加入驱动既有 observation 授权分类，读取被拒绝；修正分类并运行对应 Rust 回归。此前动作已发生且为 unknown，没有重试。
- 首次 foreground HID wheel 候选无事件、无偏移；改用一次选定窗口 PID 流后复验，而不是在同次输入后自动 fallback。背景容器双投递流曾把每次 deltaY 合并为 -2；最终单流为 -1。
- `morrow.2` 候选的双击事件已发生，但新 legacy path 标签 `cg` 不被上游 action-record 解析器支持，导致 `action_outcome_mismatch`。交付检查发现 SDK 错误 envelope 的 `execution_state=unknown` 被旧 Morrow 误归 not_started。最终 `.3` 改用既有 `cgevent` 标签，同时补上 Morrow 的 unknown 执行状态归一化回归。`.2` 发布已标记 superseded，原 wheel/证据保留；不会替换已发布字节。

[候选原始汇总与回执缺陷](evidence/computer-use-capabilities-2026-10-05/candidate-morrow-2-campaign.json)、[候选构建](evidence/computer-use-capabilities-2026-10-05/candidate-morrow-2-build.json) 及该目录中的 candidate-* 单案文件均保持原值。最终 `.3` 五案又完整重跑，双击为 unknown/background；没有仅根据旧的独立效果 passed 宣称已交付。

## 检查与交付

最终 computer-use offline：**538 passed in 25.15s**。完整 offline：**3153 passed、2 deselected in 209.61s**。Ruff check、改动文件 scoped format、compileall、morrow --help、git diff --check、GUI build（预算通过）和 uv build 均通过。全仓库 format 仅用户已有 evals/benchmarks/run_tb2.py 差异，未改动该文件。安装默认 `uv sync --locked --extra computer-use` 和 `uv lock --check` 均通过，installed module/distro 版本为 0.30.4+morrow.3，dylib SHA 与最终原生复验相同；锁文件未更新其他第三方依赖。安装后的相关 offline 再验 **538 passed in 15.03s**。

Python 3.12 的两个独立非 editable wheel 环境均通过：无 SDK / 安装功能 SDK，普通 AgentLoop 执行真实临时文件读取，SDK import/network attempts 均为 0，desktop=not_activated；pip check 与 CLI --help 也通过。wheel/sdist 的 30 个 GUI 文件与本次构建哈希一致。见 [无 SDK smoke](evidence/computer-use-capabilities-2026-10-05/package-no-extra.json)、[有 SDK smoke](evidence/computer-use-capabilities-2026-10-05/package-extra.json)、[默认安装与原生载荷](evidence/computer-use-capabilities-2026-10-05/default-extra-install.json)、[发布文件哈希](evidence/computer-use-capabilities-2026-10-05/release-digests.json)。

完整日志：[offline](evidence/computer-use-capabilities-2026-10-05/offline.log)、[安装后 computer-use](evidence/computer-use-capabilities-2026-10-05/computer-installed.log)、[GUI 构建](evidence/computer-use-capabilities-2026-10-05/gui-build.log)、[包构建](evidence/computer-use-capabilities-2026-10-05/package-build.log)、[Rust observation 授权分类回归](evidence/computer-use-capabilities-2026-10-05/rust-observation-classification.log)（1 passed，其余过滤）。

实现提交：8680e1fa、cc96036a；分发接入提交：9887130a。独立补齐分支基于已验证 main d6949e4e，复用现有工作树，未触碰或提交用户原有 benchmark/docs staged/dirty；本报告及有界证据另以文档提交保留。
