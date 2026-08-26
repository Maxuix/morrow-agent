# 实现 Forth 子集解释器

在 `forth.py` 中实现 `evaluate(input_data)`。`input_data` 是多行 Forth 源码组成的字符串列表，返回最终整数栈。

需要支持：

- 有符号整数；
- `+`、`-`、`*`、`/`，除法向零截断；
- `DUP`、`DROP`、`SWAP`、`OVER`；
- 使用 `: name definition ;` 定义新单词；
- 单词大小写不敏感，可引用此前定义的单词；
- 重定义只影响之后编译的定义和调用，不应追溯改变已有定义；
- 栈元素不足时抛出 `StackUnderflowError`；
- 除零抛出 `ZeroDivisionError`；
- 未定义单词、非法定义或用数字作为单词名时抛出 `ValueError`。

保持公开异常类和函数签名不变。
