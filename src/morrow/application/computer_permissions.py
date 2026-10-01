"""Safe local permission summaries, without native identities or observation contents."""

from morrow.core.computer_use import ComputerUseAction, ComputerUseScope, TargetRef


def computer_scope_summary(scope: ComputerUseScope) -> dict[str, object]:
    return {
        "apps": [app.bundle_id for app in scope.apps],
        "window_scope": "selected_windows" if scope.schema_version == 2 else "legacy_app_windows",
        "window_count": len(scope.windows) if scope.schema_version == 2 else None,
        "operations": [operation.value for operation in scope.operations],
        "delivery": scope.delivery.value,
        "image_share": scope.image_share.value,
    }


def computer_scope_lines(scope: ComputerUseScope) -> tuple[str, ...]:
    summary = computer_scope_summary(scope)
    windows = (
        f"{summary['window_count']} 个明确选中窗口"
        if summary["window_scope"] == "selected_windows"
        else "旧应用范围（未记录固定窗口）"
    )
    operations = "观察与操作" if "action" in summary["operations"] else "仅观察"
    delivery = "前台" if summary["delivery"] == "foreground" else "后台"
    images = "不分享图像" if summary["image_share"] == "none" else "分享受控窗口图像"
    return (
        "桌面授权（与 Shell Host 权限独立）",
        "应用：" + "、".join(summary["apps"]),
        f"窗口：{windows}",
        f"能力：{operations} · {delivery}投递 · {images}",
    )


def computer_action_preview_lines(
    scope: ComputerUseScope, target: TargetRef | None, action: ComputerUseAction
) -> tuple[str, ...]:
    """Only safe action facts are projected; input text, AX content and native references stay private."""
    label = target.display_label or target.app.bundle_id if target else "观察已失效，请重新观察"
    app = target.app.bundle_id if target else "、".join(item.bundle_id for item in scope.apps)
    names = {
        "type_text": "输入文本（内容不展示）",
        "scroll": "滚动",
        "press_key": "按键",
        "hotkey": "组合键",
    }
    name = names.get(action.type, "单击")
    if action.type == "click":
        name = ("右键" if action.button == "right" else "左键") + (
            "双击" if action.count == 2 else "单击"
        )
    if action.type == "press_key":
        name = f"按键：{action.key}"
    elif action.type == "hotkey":
        name = "组合键：" + "+".join(action.keys)
    elif action.type == "scroll":
        direction = {"up": "上", "down": "下", "left": "左", "right": "右"}[action.direction]
        name = f"向{direction}滚动 {action.amount} 单位"
    location = (
        "窗口图像坐标 " + str((action.x, action.y))
        if getattr(action, "x", None) is not None
        else "可访问性控件"
        if action.element_ref is not None
        else "当前窗口焦点"
    )
    delivery = "前台" if scope.delivery.value == "foreground" else "后台"
    images = "不分享图像" if scope.image_share.value == "none" else "分享受控窗口图像"
    return (
        "能力：独立桌面窗口授权，不授予 Shell Host 权限",
        f"动作：{name}",
        f"目标窗口：{label}",
        f"应用：{app}",
        f"定位：{location}",
        f"投递：{delivery} · {images}",
        "仅审批此次动作；窗口内容不能授予权限。",
    )
