# Mimo v2.5 真实用户场景验收报告

日期：2026-08-19
目标：OpenCode Go / `mimo-v2.5`
范围：当前 Stage 3 本地 Code Agent、Provider 持久化、真实 CLI/TTY、真实 Mimo 工具调用、Host 命令与 macOS 原生沙箱。

## 结论

核心链路通过验收：Provider 持久化、真实 Mimo v2.5 连接、工作空间读搜、冲突安全修改、ChangeSet、审批、只读 Git、受保护凭据和 Auto Sandboxed 命令均获得真实证据。

最终结论：Stage 3 可在当前声明的 macOS 平台关闭并提交。Keychain 异常已统一脱敏，模型连接和首个 token 已有明确的等待/超时反馈；当前仍保留的用户体验建议是减少模型偶发的无效工具参数和依赖安装尝试。

## 持久化环境

- `scripts/morrow-mimo` 固定复用 Morrow 标准状态目录 `~/.morrow`。
- API Key 由 macOS Keychain 保存，未写入仓库、YAML、日志或模型上下文。
- `provider show` 实际结果：`credential: 可用`、`models: mimo-v2.5`、`last_test: ok`。
- `provider test opencode-go` 实际结果：`连接成功`。
- `model current` 实际结果：`opencode-go/mimo-v2.5`。
- `provider presets` 实际结果：可发现 `opencode-go-mimo/opencode-go/mimo-v2.5`；持久化包装命令对不接受 state 参数的 preset 列表命令也能正常工作。

## 真实 Mimo 场景矩阵

| 场景 | 实际动作 | 结果 |
|---|---|---|
| 普通对话 | 要求只回复一句且不调用工具 | 通过；返回可见文本，0 次工具调用 |
| 目录/文件发现 | 列出目录、发现 `src/calculator.py`、读取文件 | 通过；`list_directory`、`find_files`、`read_file` 成功 |
| 文本搜索 | 搜索 `replace-me` | 通过；定位 `docs/guide.md` 第 3 行 |
| 精确修改 | 读取 SHA 后将 `replace-me` 改为 `updated-by-mimo` | 通过；仅目标文件变化，审批后发布 |
| ChangeSet | 创建 `notes/generated.txt` 后调用 `show_changes` | 通过；返回 1 条实际变更及 Diff |
| Host 命令 | 执行受控 `python3 run_acceptance.py` | 通过；退出码 0，输出 `fixture acceptance passed` |
| 只读 Git | 读取 `git_status` 和 `git_diff` | 通过；未提供 Git 写入能力 |
| 凭据保护 | 请求读取 `.env` 的 `API_KEY` | 通过；只返回受保护状态，未返回内容 |
| 审批拒绝 | 拒绝文件写入 | 通过；返回 `approval_rejected`，文件未变化，模型如实说明 |
| Auto Sandboxed | 沙箱执行受控验收脚本 | 通过；退出码 0，`sandbox_changed_paths` 为空，真实工作区未变化 |
| CLI/TTY | 确认工作空间、填写 Profile、普通对话、`/status`、确认退出 | 通过；进程退出码 0 |
| `/workspace`、`/config` | 读取工作空间和 Preferences 状态 | 通过；返回有界确定性结果 |

真实 Mimo 还出现过一次无效 `run_command` 参数，运行时返回结构化 `invalid_arguments`，模型随后自行纠正并完成命令；这不是数据越界，但说明模型调用工具时仍需更强的参数引导。

## 自动化与宿主门禁

- `uv run pytest -m 'not live'`：406 passed，2 skipped，1 deselected，409 collected。
- macOS 宿主 Seatbelt：2 passed；验证了工作区、Home、网络隔离和真实工作区不被沙箱命令修改。
- Ruff check：通过。
- Ruff format check：通过。
- `compileall`：通过。
- `git diff --check`：通过。

离线矩阵还覆盖了符号链接越界、`.git`/凭据/私钥保护、空读窗口、ChangeSet 冲突、Host/Git 风险预检、审批不可用、超时、取消、输出预算和会话恢复边界。

## 发现的问题与优化建议

### P1：Keychain 被锁定或拒绝时可能出现 traceback（已关闭）

在 Codex 受限沙箱中读取 macOS Keychain 返回 `(-50, Unknown Error)`，`provider show` 曾输出完整 traceback；宿主权限下同一凭据正常。生产环境若遇到钥匙串锁定、权限变化或后端异常，也应返回脱敏的“凭据不可用/请解锁 Keychain”，不能把 traceback 展示给用户。

处理：CredentialStore、Provider Service 和 CLI 边界统一捕获 keyring 异常，返回稳定的 `denied`、`locked` 或 `unavailable` 状态及恢复指引；回归测试确认不包含后端 traceback 或原始异常文本。

### P1：真实 Mimo 首轮等待较长，用户缺少阶段反馈（已关闭）

真实流式场景首轮曾出现约几十秒到近两分钟的等待。CLI 最终能返回，但等待期间应显示“正在连接模型/正在等待首个 token”，并提供明确的取消与超时原因。

处理：Provider 连接测试增加“正在测试模型连接”反馈；Adapter 分离连接超时与首个 token 超时，并将网络、认证、限流和模型响应失败映射为稳定用户错误。真实 `provider test opencode-go` 已复测通过。

### P2：模型偶尔生成无效工具参数或尝试安装依赖

Mimo 在命令场景中曾先生成无效参数；沙箱测试中还尝试安装缺失依赖，随后被安全策略拒绝。运行时能安全收敛，但用户会看到多余工具步骤。

建议：在模型可见工具描述中明确 `argv`/`shell` 二选一、禁止安装依赖和网络；对 `mimo-v2.5` 增加少量工具调用示例或模型专用提示约束。

### P2：Provider 预设可发现性不足（已关闭）

当前可使用 `opencode-go-mimo`，但 CLI 没有列出可用 preset 的命令，用户需要记住预设名称。

处理：增加 `provider presets`，并在 `provider add --help` 展示预设；添加 Provider 时输出 active model 是否切换，且保留已有模型配置。

## 已在本轮处理

- 增加 `opencode-go-mimo` 预设，API model ID 为 `mimo-v2.5`。
- Provider 增加 Mimo 模型时保留已有模型和 active model，不破坏原 DeepSeek 配置。
- 修复 Mimo 包装命令的持久化 state 传递，并兼容不接受 `--state-root` 的 `provider presets`。
- 增加持久化测试包装命令和使用说明。

最终复核门禁：离线测试 `418 passed, 2 skipped, 1 deselected`；宿主 macOS Seatbelt `2 passed`；Ruff、格式、Compileall、CLI help、`git diff --check` 和 wheel smoke 全部通过。未执行 push。
