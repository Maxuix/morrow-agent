import type { DesktopRuntimeStatus } from '../api/settings'

const states: Record<DesktopRuntimeStatus['state'], string> = {
  not_activated: '尚未创建桌面会话', idle: '当前无桌面会话', active: '桌面会话运行中',
  quarantined: '桌面调用已隔离', stopping: '正在关闭桌面宿主', closed: '桌面宿主已关闭',
  unknown: '桌面状态暂不可核验',
}

/** Native settling and action effects are independent of chat completion. */
export function DesktopRuntimeNotice({status}: {status: DesktopRuntimeStatus}) {
  return <div className="permission-live-state">
    <p className="menu-note" role="status">本机桌面状态（最近读取）：{states[status.state]}</p>
    {status.native_pending === true && <p className="menu-note" role="status">
      {status.state === 'quarantined' || status.state === 'stopping'
        ? '原生调用尚未停稳，暂不能开始下一次桌面运行。' : '原生调用正在进行。'}
    </p>}
    {status.native_pending === null && <p className="menu-note">原生调用状态暂不可核验，请刷新授权状态。</p>}
    {status.unknown_actions > 0 && <p className="menu-error" role="alert">
      有 {status.unknown_actions} 次桌面动作效果未知，请检查目标窗口；不要自动重试。
    </p>}
    <p className="menu-note">停止或撤销会拒绝后续动作；已经投递的效果无法撤回。</p>
  </div>
}
