# 保留并安全展示 Provider 连接诊断

Provider Adapter 已经能够产生部分类型化错误，但错误经过 Service 和 CLI 后经常退化成异常类名或泛化消息；代理、传输层错误和被包装的底层连接错误也可能被错误归为 `internal`。

请修复 Provider 错误分类和展示链路：

- 在有界、防循环的异常因果链中识别既有类型化 Provider 错误；
- 将连接、网络、代理、传输和底层 `ConnectionError` / `OSError` 归为稳定的 `network` 分类；
- 保持认证、限流、超时和无效响应的稳定分类；
- `provider add`、`provider configure` 和 `provider test` 使用一致、可操作的脱敏提示；
- 不得把 SDK 原始异常文本、代理地址、凭据或 traceback 输出到终端；
- 测试必须完全离线，不发起真实 Provider 请求。

完成后运行 Provider 和 CLI 相关测试。
