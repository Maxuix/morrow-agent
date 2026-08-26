# S7P-05 Validation and Completion Truth 验收报告

## 1. 结论

S7P-05 已在当前 topic branch 完成实现。Direct-agent 的最终 `stop` 现在由运行时有界证据决定：
普通命令成功不会伪装成 validation，change task 需要可归因的净工作区变化，required validation
必须是精确 `(validator_kind, scope)` 的 recognized `ValidationFact`，路径越界、禁止路径、未闭合
调用、known failure 和可选 verifier 都会阻止不真实的完成声明。业务语义正确性不在本项判定范围内。

最终候选文本在 completion gate 通过前不写入 `ConversationLog` 或公开 `text.delta`；失败时最多注入
一次只含固定事实码、路径摘要和下一动作的 system correction。Session/ConversationLog 仍是唯一聊天
写入路径，公开事件类型与字段保持不变。

## 2. 范围与基线

| 项目 | 记录 |
|---|---|
| Branch | `codex/feat/s7p-05-validation-completion-truth` |
| Review baseline | `2c035098263fee3f93d66abc42bc09a36bbb1c42` |
| Platform | 2026-08-27；macOS worktree |
| Persistence | AgentRun snapshot 冻结 contract/baseline；terminal metrics 使用 schema v18 safe defaults |
| Provider | 仅使用 `ScriptedModelProvider`/fake fixtures；未运行 live Provider/model/Pi/MCP/network/credential 测试 |
| Workspace safety | baseline 使用 bounded no-follow manifest、SHA-256、symlink target identity；`.git`、环境、cache 和 protected metadata 不读取正文 |
| Compatibility | 旧 snapshot/terminal row 可按旧字段解码；旧 command/unspecified 对话保留兼容路径 |
| Unchanged | 未改变权限、tool effect、runtime-policy defaults、public AgentEvent 类型/字段、依赖或三份 research 文档 |

## 3. 实现证据

| 合同 | 实现与覆盖 |
|---|---|
| Command 与 validation 分离 | `CommandToolFact` 只记录 bounded process outcome；Process preflight 仅识别 pytest、Ruff、compileall 及受限 static/build forms 后生成 scoped `ValidationFact`；utility、ambiguous shell、wrapper/control-flow bypass fail closed |
| Outcome Contract | `OutcomeMode`、target/allowed/forbidden paths、required validation、verifier id、no-change 和 preparation version 均为 immutable bounded models；compiler 对 change/explanation/unspecified 保守分类 |
| Workspace baseline | pre-admission capture bounded regular-file/symlink manifest，使用 no-follow directory fd 与稳定 hash；Git HEAD/pointer 只保留安全摘要；truncation、scan error、baseline drift 和 repository change 不宣称成功 |
| Completion gate | before/after net diff、target、unexpected/forbidden path、run attribution、exact validation scope、known failure、unresolved tool 与 optional verifier 均由 runtime-owned `CompletionChecker` 检查 |
| Final claim safety | final candidate buffered；gate failure 不 append assistant、不 emit final delta；一次 fact-only correction 后再次失败使用精确 stop code |
| Durable resume | `PreparedAgentRunSpec`/`AgentRunSnapshot` 冻结并 rehydrate contract/baseline；空 resume input 不重新推断原任务 contract，也不接受新的 baseline |
| Observability/terminal | Session 保存 process-local completion/validation facts；terminal 区分 tool/command/validation/completion basis；schema v18 只增加 bounded aggregate fields |

## 4. 场景矩阵

| 场景 | 预期 | 状态 |
|---|---|---|
| exit-zero utility (`ls` 等) | 只有 `CommandToolFact`；validation 为 `not_run` | PASS |
| recognized pytest/Ruff/compileall | 记录 kind 与 normalized scope；pass/fail/timeout/cancel 保持区分 | PASS |
| shell control、`sh -c`、多命令、越界/符号链接 scope | 不生成 validation fact | PASS |
| change 无净 diff、reverted write | `missing_required_change`，不接受 prose | PASS |
| target/allowed/forbidden path | 目标和允许范围严格匹配；unexpected/forbidden 使用精确 stop code | PASS |
| required validation mismatch/failure | missing 或 failed validation 阻止最终 stop | PASS |
| unrecognized/failed tool 与 unresolved call | 保留 bounded failure evidence；未闭合调用阻止 completion | PASS |
| optional verifier | true 为 `verified`；false reject；缺失/异常/invalid inconclusive | PASS |
| pre-existing dirty/untracked content | baseline unchanged dirt 不归因；无法证明归因时 inconclusive | PASS |
| symlink、扫描截断、非 Git workspace | 不跟随链接；bounded incomplete baseline 不宣称 clean success | PASS |
| fresh/resume durable run | snapshot 与 rehydrated spec 保持 contract/baseline 相等 | PASS |
| rejected final/history | 不产生 final text delta，不追加 rejected assistant；只允许一次 correction | PASS |
| scripted Direct-agent | production bootstrap、审批、真实 file mutation、scoped pytest 和 terminal metrics 全链路覆盖 | PASS |

## 5. 离线验证记录

测试使用当前 worktree 的绝对路径 `.venv`，并先确认 `import morrow` 指向当前 worktree 的
`src/morrow`。没有联网、安装依赖或运行 `uv sync`；宿主 `uv` cache 权限/DNS 失败不作为代码失败。

| Gate | 结果 |
|---|---|
| S7P-05 completion/terminal/agent context focused regression | `90 passed` |
| capabilities/process/tool-loop/context focused gates | 待最终实现提交后记录 |
| observability/store/preparation focused gates | 待最终实现提交后记录 |
| recovery/journal/product acceptance/mini-eval focused gates | 待最终实现提交后记录 |
| full offline pytest (`not live`) | 待最终实现提交后记录 |
| Ruff format/check | 受影响文件已通过；repository-wide gate 待最终记录 |
| compileall | 待最终实现提交后记录 |
| CLI help (`morrow`, `morrow run`) | 待最终实现提交后记录 |
| `git diff --check` | 待最终实现提交后记录 |

## 6. 审查记录

按本任务要求，提交实现与初步验证后启动只读 `gpt-5.6-luna` / reasoning `max` reviewer，审查
完整 `2c035098263fee3f93d66abc42bc09a36bbb1c42...HEAD` diff，重点包括 false-positive validation、
命令解析绕过、scope、脏文件归因、symlink/扫描、文本泄漏、history、freeze/rehydration、verifier
authority、stop-code 精度和只证明 mock 的测试。正式 reviewer id、verdict、confirmed findings
及修复后的重新验证将在 reviewer 返回后补录。

## 7. 明确缺口

本验收不推断业务语义正确性，不替代真实 Provider/model/Pi/MCP/network/credential 质量验收，也不
覆盖 S7P-06。Verifier 只有在应用显式注入时才具有 authority；没有 verifier 时 completion basis
明确为 `runtime_evidence_without_verifier`，不会显示为 verified。
