"""Safe local permission summaries, without native identities or observation contents."""

from morrow.core.computer_use import (
    ComputerUseAction,
    ComputerUseRuntimeStatus,
    ComputerUseScope,
    TargetRef,
)
from morrow.core.execution import ToolExecutionDisposition


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


def computer_runtime_summary(lifecycle, journal, workspace_id, agent_run_id):
    """Pure local owner/ledger reads; no device discovery or permission changes."""
    status = getattr(lifecycle, "runtime_status", None)
    if not isinstance(status, ComputerUseRuntimeStatus):
        status = ComputerUseRuntimeStatus(state="unknown", native_pending=None)
    unknown = (
        sum(
            row.tool_name == "computer_action"
            and row.disposition is ToolExecutionDisposition.UNKNOWN
            for row in journal.list_executions(workspace_id, agent_run_id=agent_run_id)
        )
        if agent_run_id is not None
        else 0
    )
    return status.model_dump(mode="json") | {"scope": "local_host", "unknown_actions": unknown}


def computer_runtime_lines(summary):
    states = {
        "not_activated": "尚未创建桌面会话",
        "idle": "当前无桌面会话",
        "active": "桌面会话运行中",
        "quarantined": "桌面调用已隔离",
        "stopping": "正在关闭桌面宿主",
        "closed": "桌面宿主已关闭",
        "unknown": "桌面状态暂不可核验",
    }
    lines = ["本机桌面状态（最近读取）：" + states[summary["state"]]]
    if summary["native_pending"] is True:
        lines.append(
            "原生调用尚未停稳，暂不能开始下一次桌面运行。"
            if summary["state"] in {"quarantined", "stopping"}
            else "原生调用正在进行。"
        )
    elif summary["native_pending"] is None:
        lines.append("原生调用状态暂不可核验，请重新 /computer status。")
    if summary["unknown_actions"]:
        lines.append(
            f"有 {summary['unknown_actions']} 次桌面动作效果未知，请检查目标窗口；不要自动重试。"
        )
    lines.append("停止或撤销会拒绝后续动作；已经投递的效果无法撤回。")
    return tuple(lines)
