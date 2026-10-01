"""Safe local permission summaries, without native identities or observation contents."""

from morrow.core.computer_use import ComputerUseScope


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
