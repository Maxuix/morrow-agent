# 修复 CLI 无效输入的错误处理

当前 CLI 在查询不存在的 Provider、测试缺少凭据的 Provider，或重连不存在的 Workspace 时，可能把内部异常类型或 traceback 暴露给用户，部分错误还会把“未知 Provider”和“凭据缺失”混为一谈。

请检查相关 CLI 和 Provider Service，实现以下行为：

- `provider test` 和 `provider show` 对未知 Provider 返回退出码 `2`，并显示包含 Provider ID 的稳定提示；
- 已配置但缺少凭据的 Provider 显示“凭据不可用”，不要被误报为未知 Provider；
- `workspace relink` 对未知 Workspace 返回受控错误和退出码 `2`；
- 用户输出不得包含 `Traceback` 或异常类名；
- 保持成功路径和已有配置语义不变。

完成后运行相关测试，并检查实际修改范围。
