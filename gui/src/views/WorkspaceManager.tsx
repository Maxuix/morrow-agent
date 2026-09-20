import { createPortal } from 'react-dom'
import { useEffect, useRef, useState } from 'react'
import type { ApiClient, DirectoryListing, WorkspaceEntry, WorkspaceList } from '../api/client'
import { commandId } from './lib/editor'

/**
 * The full-page boot surface (App start with no workspace yet): a compact
 * switcher row plus the management dialog behind it.
 */
export function WorkspaceManager({client, selected, onSelect, onManaging}: {client:ApiClient;selected:string|null;onSelect:(id:string)=>void;onManaging?:(open:boolean)=>void}) {
  const [open,setOpen] = useState(false)
  const [list,setList] = useState<WorkspaceList>({items:[],revision:0})
  const [error,setError] = useState<string|null>(null)
  const [switching,setSwitching] = useState(false)
  const chooseSeq = useRef(0)
  const load = async () => {const value=await client.workspaces();setList(value);return value}
  useEffect(()=>{void load().catch(e=>setError(e.message))},[client])
  const mayLeave = () => window.dispatchEvent(new Event('morrow:before-workspace-switch',{cancelable:true}))
  // Fast A→B clicks start two load→manage chains; only the newest one may
  // commit onSelect, so the final choice follows the last click, not the
  // last response.
  const choose = (id:string) => {if(!mayLeave())return;setSwitching(true);const seq=++chooseSeq.current
    void load().then(value=>client.manageWorkspace(id,'open',{command_id:commandId('workspace_open'),expected_revision:value.revision}))
      .then(()=>{if(seq===chooseSeq.current)onSelect(id)},e=>{if(seq===chooseSeq.current)setError((e as Error).message)})
      .finally(()=>{if(seq===chooseSeq.current)setSwitching(false)})}
  const toggle = () => {if(!mayLeave())return;setOpen(!open);onManaging?.(!open);void load().catch(e=>setError(e.message))}
  return <div className="workspace-manager">
    <div className="workspace-switcher">
      <label className="workspace-select-label">工作区 <select aria-label="选择工作区" disabled={switching} value={selected??''} onChange={e=>choose(e.target.value)}><option value="" disabled>选择工作区</option>{list.items.map(w=><option key={w.workspace_id} value={w.workspace_id}>{w.display_name}{w.available?'':'（目录失效）'}</option>)}</select></label>
      <button className="editor-button" onClick={toggle}>{open?'返回对话':'管理工作区'}</button>
      {error && !open && <span role="alert">{error}</span>}
    </div>
    <WorkspaceManagerDialog client={client} selected={selected} onSelect={onSelect} open={open} onClose={()=>{setOpen(false);onManaging?.(false)}}/>
  </div>
}

/**
 * Workspace management dialog. Rendered controlled: `open`/`onClose` come from
 * the host (boot surface or a sidebar group's … menu), `initialTarget` picks
 * the workspace to manage right after opening, and `onChange` lets the host
 * reload its own workspace list after register/rename/relink/remove.
 */
export function WorkspaceManagerDialog({client, selected, onSelect, open, onClose, initialTarget=null, onChange}: {client:ApiClient;selected:string|null;onSelect:(id:string)=>void;open:boolean;onClose:()=>void;initialTarget?:string|null;onChange?:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [list,setList] = useState<WorkspaceList>({items:[],revision:0})
  const [error,setError] = useState<string|null>(null)
  const [busy,setBusy] = useState(false)
  const [path,setPath] = useState(''); const [name,setName] = useState('')
  const [mode,setMode] = useState<'open'|'create'>('open')
  const [directory,setDirectory] = useState<DirectoryListing>({})
  const [target,setTarget] = useState<WorkspaceEntry|null>(null)
  const [displayName,setDisplayName] = useState('')
  const load = async () => {const value=await client.workspaces();setList(value);return value}
  useEffect(() => {
    if (!open) {dialog.current?.close(); return}
    dialog.current?.showModal()
    void load().then(value => {
      const entry = initialTarget ? value.items.find(w=>w.workspace_id===initialTarget) : null
      if (entry) {setTarget(entry);setDisplayName(entry.display_name);setPath(entry.path)}
    }).catch(e=>setError((e as Error).message))
    return () => {dialog.current?.close()}
    // Reopening re-runs the open sequence; initialTarget is read at open time.
  }, [open])
  const action = async (fn:()=>Promise<void>) => {setBusy(true);setError(null);try{await fn();await load();onChange?.()}catch(e){setError((e as Error).message);await load().catch(()=>{})}finally{setBusy(false)}}
  const browse = (value?:string) => void action(async()=>{const result=await client.directories(value);setDirectory(result);if(result.path)setPath(result.path)})
  const manage = (kind:string) => {if(!target)return;void action(async()=>{
    const result=await client.manageWorkspace(target.workspace_id,kind,{command_id:commandId('workspace_'+kind),expected_revision:list.revision,...(kind==='rename'?{display_name:displayName}:{}),...(kind==='relink'?{path}: {})})
    if(kind==='remove'){setTarget(null);const updated=await load();onChange?.();if(selected===target.workspace_id)onSelect(updated.items[0]?.workspace_id??'')}else setTarget({...target,...result.workspace})
  })}
  return createPortal(<dialog ref={dialog} className="workspace-dialog" aria-label="工作区管理" onCancel={event => {event.preventDefault();onClose()}}><section className="workspace-management-panel">
      <button className="editor-button workspace-dialog-close" onClick={onClose}>返回对话</button>
      <h1 className="font-serif text-2xl">工作区管理</h1>
      {error && <p role="alert" className="text-failed">{error}</p>}
      <div className="workspace-management-grid">
        <div><h2>最近工作区</h2>{list.items.map(w=><button className="workspace-entry" key={w.workspace_id} onClick={()=>{setTarget(w);setDisplayName(w.display_name);setPath(w.path)}}><strong>{w.display_name}</strong><small>{w.path}</small><small>{w.available?'可连接':'目录失效，请重连'}</small></button>)}</div>
        <div className="space-y-3">
          <fieldset><legend>打开或创建文件夹</legend><label><input type="radio" name="folder-mode" checked={mode==='open'} onChange={()=>setMode('open')}/>打开已有文件夹</label> <label><input type="radio" name="folder-mode" checked={mode==='create'} onChange={()=>setMode('create')}/>新建文件夹</label></fieldset>
          <label className="block">{mode==='create'?'父目录':'目录路径'}<input aria-label="目录路径" className="workspace-input" value={path} onChange={e=>setPath(e.target.value)}/></label>
          <div className="flex gap-2"><button className="editor-button" disabled={busy} onClick={()=>browse()}>可浏览位置</button><button className="editor-button" disabled={busy||!path} onClick={()=>browse(path)}>浏览目录</button></div>
          {directory.roots?.map(root=><button className="workspace-entry" key={root} onClick={()=>browse(root)}>{root}</button>)}
          {directory.parent && <button className="editor-button" onClick={()=>browse(directory.parent!)}>上一级</button>}
          <div className="directory-children">{directory.items?.map(item=><button className="workspace-entry" key={item.path} onClick={()=>browse(item.path)}>{item.name}</button>)}</div>
          {directory.truncated&&<p>目录列表已达 200 项；可输入精确路径继续。</p>}
          {mode==='create' && <label className="block">新文件夹名称<input aria-label="新文件夹名称" className="workspace-input" value={name} onChange={e=>setName(e.target.value)}/></label>}
          <button className="editor-button" disabled={busy||!path||(mode==='create'&&!name)} onClick={()=>void action(async()=>{const result=await client.registerWorkspace({command_id:commandId('workspace_register'),action:mode,path,...(mode==='create'?{name}: {})});onSelect(result.workspace.workspace_id);setTarget(result.workspace)})}>{mode==='create'?'创建并注册':'打开并注册'}</button>
          {target && <fieldset className="space-y-3 border-t border-subtle pt-4"><legend>管理：{target.display_name}</legend><label className="block">工作区名称<input aria-label="工作区名称" className="workspace-input" value={displayName} onChange={e=>setDisplayName(e.target.value)}/></label><div className="flex flex-wrap gap-2"><button className="editor-button" disabled={busy||!displayName} onClick={()=>manage('rename')}>保存名称</button><button className="editor-button" disabled={busy||!path} onClick={()=>manage('relink')}>重连到上述目录</button><button className="editor-button" disabled={busy} onClick={()=>manage('remove')}>从列表移除</button></div><button className="editor-button" disabled={busy||!!target.git_root} onClick={()=>manage('git-init')}>在此工作区初始化 Git</button></fieldset>}
        </div>
      </div>
    </section></dialog>, document.body)
}
