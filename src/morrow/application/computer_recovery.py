"""Local recovery guidance for bounded device codes, without raw native diagnostics."""

_GUIDANCE = {
    "unsupported_foreground_scroll_delivery": "官方 SDK 0.30.4 的前台坐标滚动无法可靠投递，动作未执行；如需图像滚动，请由用户明确为新运行选择后台投递。",
    "unsupported_double_click_delivery": "官方 SDK 0.30.4 的后台双击无法可靠投递，动作未执行；如需双击，请由用户明确为新运行选择前台投递。",
    "element_geometry_unavailable": "控件缺少可核验几何，未投递鼠标手势；请使用新图中的精确坐标。",
    "unsupported_scroll_target": "所选控件不是可观察的滚动容器；请在新图中定位实际滚动区域。",
    "unsupported_attribute": "官方 SDK 当前仅公开 enabled 属性；该属性不支持，动作未投递。",
    "disabled": "启用桌面功能后，为下次运行重新选择窗口；启用不会授予权限。",
    "sdk_missing": "在当前宿主环境安装 computer-use 可选组件后，重新查看宿主状态。",
    "native_unverified": "原生验收尚未通过，请保留人工操作；启用开关不能绕过验收。",
    "tcc_missing": "在系统隐私与安全设置中检查实际运行宿主的辅助功能和屏幕录制权限，再重新检查状态。",
    "no_interactive_session": "请在本机已登录的交互桌面中运行宿主，再重新选择窗口。",
    "desktop_busy": "另一桌面运行或尚未停稳的原生调用仍占用桌面；查看本机运行状态，等待停稳后重新选择。",
    "stale_observation": "旧观察或选择已失效；重新读取窗口或观察，使用新的引用，不重复旧动作。",
    "unknown_target": "目标窗口已失效；重新读取并明确选择窗口，不自动扩大到其他窗口。",
    "images_not_supported": "当前模型不支持图像；选择语义模式或更换支持图像的模型，并为新运行重新选择窗口。",
    "model_image_tools_required": "为新运行选择同时支持图像与函数工具的模型，或显式改用语义模式。",
    "image_missing": "窗口图像未取得；检查宿主权限与目标窗口，重新观察或显式改用语义模式。",
    "image_publish_failed": "观察图像未发布；检查本地存储并重新观察，已经投递的动作不要自动重试。",
    "image_budget": "观察图像预算已耗尽；停止当前桌面运行，明确选择新运行的范围与图像分享方式。",
    "needs_approval": "请在交互式会话中重新选择窗口，并确认本次动作；无审批通路不会执行动作。",
}


def computer_recovery_guidance(code: str) -> str:
    return _GUIDANCE.get(code, "请查看本机桌面状态并重新明确选择窗口；不要自动重试效果未知的动作。")


def computer_refusal_message(code: str) -> str:
    return f"桌面操作不可用（{code}）：{computer_recovery_guidance(code)}"
