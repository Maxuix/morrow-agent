# 实现 Reactive Cells

在 `react.py` 中实现一个基础响应式计算系统：

- `InputCell(initial_value)` 保存可设置的 `value`；
- `ComputeCell(inputs, compute_function)` 根据输入 Cell 的当前值计算只读 `value`；
- InputCell 改变后，所有直接和间接依赖的 ComputeCell 都更新到稳定状态；
- ComputeCell 支持 `add_callback(callback)` 和 `remove_callback(callback)`；
- 一次输入变更中，只有最终稳定值与变更前不同的 ComputeCell 才调用回调，并且每个回调只调用一次；
- 菱形依赖图不能因为中间状态而重复通知或通知错误值；
- 设置为相同输入值不应产生通知。

可以假定依赖关系是有向无环图。不要修改公开类名和方法签名。
