import { useState } from 'react'
import type { ApiClient } from '../api/client'
import type { SessionWire } from '../api/types'
import { commandId } from './lib/editor'
import { useDismissablePopover } from './lib/popover'

export function SessionActions({client,workspace,session,active,onChanged,onFork}: {client:ApiClient;workspace:string;session:SessionWire;active:boolean;onChanged:()=>Promise<void>;onFork:(session:SessionWire)=>void}) {
  const [title,setTitle] = useState(session.metadata?.title??'')
  const [error,setError] = useState<string|null>(null); const [busy,setBusy] = useState(false)
  const [checkpoints,setCheckpoints] = useState<{checkpoint_id:string;source_end_position:number}[]>([])
  const [checkpoint,setCheckpoint] = useState('')
  const menu = useDismissablePopover()
  const action = async (fn:()=>Promise<void>)=>{setBusy(true);setError(null);try{await fn();await onChanged()}catch(e){setError((e as Error).message);await onChanged().catch(()=>{})}finally{setBusy(false)}}
  return <details ref={menu} className="session-actions" onToggle={e=>{if(e.currentTarget.open){setTitle(session.metadata?.title??'');void client.sessionCheckpoints(workspace,session.session_id).then(r=>setCheckpoints(r.checkpoints),e=>setError(e.message))}}}><summary className="editor-button">管理对话</summary>
    <div className="session-action-popover space-y-3"><p className="break-all text-xs">{session.session_id} · {session.lifecycle} · {session.health}</p>
      {error&&<p role="alert" className="text-failed">{error}</p>}
      <label>对话名称<input aria-label="对话名称" className="workspace-input" maxLength={120} value={title} onChange={e=>setTitle(e.target.value)}/></label>
      <div className="flex gap-2"><button className="editor-button" disabled={busy||!title} onClick={()=>void action(async()=>{await client.sessionMetadata(workspace,session.session_id,{command_id:commandId('title'),expected_revision:session.metadata?.revision??0,title})})}>保存对话名称</button><button className="editor-button" disabled={busy} onClick={()=>void action(async()=>{await client.sessionMetadata(workspace,session.session_id,{command_id:commandId('pin'),expected_revision:session.metadata?.revision??0,pinned:!session.metadata?.pinned})})}>{session.metadata?.pinned?'取消置顶':'置顶对话'}</button></div>
      <label className="block">分叉切点<select aria-label="分叉切点" className="workspace-input" value={checkpoint} onChange={e=>setCheckpoint(e.target.value)}><option value="">最新闭合对话</option>{checkpoints.map(c=><option key={c.checkpoint_id} value={c.checkpoint_id}>位置 {c.source_end_position-1} · {c.checkpoint_id.slice(-8)}</option>)}</select></label>
      <button className="editor-button" disabled={busy||active||session.health!=='ok'} onClick={()=>void action(async()=>{const result=await client.sessionFork(workspace,session.session_id,commandId('fork'),checkpoint?{checkpoint_id:checkpoint}:{});onFork(result.session)})}>从切点创建分叉</button>
      {session.lifecycle==='archived'?<button className="editor-button" disabled={busy||active} onClick={()=>void action(async()=>{await client.sessionLifecycle(workspace,session.session_id,'unarchive',session.updated_at,commandId('unarchive'))})}>恢复对话</button>:<button className="editor-button" disabled={busy||active} onClick={()=>void action(async()=>{
        let current=session
        if(session.current_task_run_id){const task=await client.getTask(session.current_task_run_id);await client.cancelTask(task.task_run_id,task.row_version,commandId('close_task'));current=(await client.chatSession(workspace,session.session_id)).session}
        await client.sessionLifecycle(workspace,session.session_id,'archive',current.updated_at,commandId('archive'))
      })}>{session.current_task_run_id?'结束当前任务并归档':'归档对话'}</button>}
      <p className="text-xs text-secondary">分叉不会回退项目文件。</p>
    </div>
  </details>
}
