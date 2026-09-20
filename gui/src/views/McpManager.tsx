import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import { ApiError } from '../api/client'
import type { DirtyGuard } from '../state/navigation'
import { Card, FieldList, OriginBadge, Pager } from './management/components'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from './management/LeaveGuard'
import { buttonClass, fieldClass } from './management/styles'
import { commandId } from './lib/editor'

type Server={server_id:string;display_name:string;transport:string;executable:string;argv_count:number;cwd_policy:string;workspace_visibility:string;requested_launch_risks:string[];enabled:boolean;config_revision:number;catalog_revision:number|null;catalog_status:string;tool_count:number;degraded_reason:string|null}
type Mapping={remote_name:string;effect:string;approval:string;enabled:boolean}
type Base={scope:string;revision:number;digest:string}
type Catalog=Base&{servers:Server[];next_cursor:string|null}
type Detail=Base&{server:Server;tools:unknown[];configuration:{timeout_ms:number;cwd:string|null;tool_policy:{allowlist:string[];mappings:Mapping[]}};credentials:{name:string;available:boolean}[];credential_status:string}

export function McpEditor({scope,base,initial,busy,onSave,onClose}:{scope:string;base:Base;initial:Detail|null;busy:boolean;onSave:(body:Record<string,unknown>)=>Promise<boolean>;onClose:()=>void}){
  const [id,setId]=useState(initial?.server.server_id??'mcp_');const [name,setName]=useState(initial?.server.display_name??'');const [executable,setExecutable]=useState(initial?.server.executable??'')
  const [argv,setArgv]=useState('');const [keepArgs,setKeepArgs]=useState(!!initial);const [cwdPolicy,setCwdPolicy]=useState(initial?.server.cwd_policy??'workspace');const [cwd,setCwd]=useState(initial?.configuration.cwd??'')
  const [timeout,setTimeoutValue]=useState(initial?.configuration.timeout_ms??15000);const [visibility,setVisibility]=useState(initial?.server.workspace_visibility??'none');const [risks,setRisks]=useState(initial?.server.requested_launch_risks??[])
  const [mappings,setMappings]=useState<Mapping[]>(initial?.configuration.tool_policy.allowlist.map(remote_name=>initial.configuration.tool_policy.mappings.find(m=>m.remote_name===remote_name)??{remote_name,effect:'none',approval:'require_approval',enabled:true})??[])
  const [environment,setEnvironment]=useState(initial?.credentials.map(v=>v.name).join('\n')??'');const [keepCredentials,setKeepCredentials]=useState(!!initial)
  const update=(index:number,values:Partial<Mapping>)=>setMappings(rows=>rows.map((r,i)=>i===index?{...r,...values}:r))
  const save=()=>onSave({action:initial?'update':'add',scope,server_id:id,expected_revision:base.revision,expected_digest:base.digest,expected_server_revision:initial?.server.config_revision??0,confirmed:true,keep_arguments:keepArgs,environment_names:keepCredentials?null:environment.split('\n').map(s=>s.trim()).filter(Boolean),definition:{server_id:id,display_name:name||id,executable,argv:keepArgs?[]:argv.split('\n').filter(Boolean),transport:'stdio',cwd_policy:cwdPolicy,cwd:cwdPolicy==='absolute'?cwd:null,timeout_ms:timeout,workspace_visibility:visibility,requested_launch_risks:risks,tool_policy:{allowlist:mappings.map(m=>m.remote_name),mappings}}})
  return <Card title={initial?'编辑 MCP 配置':'添加 MCP 服务'}><p className="text-xs">保存后为停用状态</p>
    <label className="block text-sm">服务 ID<input aria-label="MCP服务ID" className={fieldClass} value={id} disabled={!!initial} onChange={e=>setId(e.target.value)}/></label><label className="block text-sm">显示名称<input aria-label="MCP显示名称" className={fieldClass} value={name} onChange={e=>setName(e.target.value)}/></label>
    <label className="block text-sm">可执行文件绝对路径<input aria-label="MCP可执行文件" className={fieldClass} value={executable} onChange={e=>setExecutable(e.target.value)}/></label>
    {initial&&<label className="block text-sm"><input type="checkbox" checked={keepArgs} onChange={e=>setKeepArgs(e.target.checked)}/>保留现有 {initial.server.argv_count} 个启动参数</label>}
    {!keepArgs&&<label className="block text-sm">启动参数，每行一个<textarea aria-label="MCP启动参数" className={fieldClass} value={argv} maxLength={65536} onChange={e=>setArgv(e.target.value)}/></label>}
    <label className="block text-sm">工作目录策略<select aria-label="MCP目录策略" className={fieldClass} value={cwdPolicy} onChange={e=>setCwdPolicy(e.target.value)}><option value="workspace">当前工作区目录</option><option value="managed">临时托管目录</option><option value="absolute">指定绝对路径</option></select></label>{cwdPolicy==='absolute'&&<label className="block text-sm">工作目录<input className={fieldClass} value={cwd} onChange={e=>setCwd(e.target.value)}/></label>}
    <label className="block text-sm">超时（毫秒）<input type="number" className={fieldClass} min="100" max="300000" value={timeout} onChange={e=>setTimeoutValue(Number(e.target.value))}/></label>
    <label className="block text-sm">工作区可见性<select aria-label="MCP工作区可见性" className={fieldClass} value={visibility} onChange={e=>setVisibility(e.target.value)}><option value="none">无访问（stdio不可启动）</option><option value="read_only">只读（stdio无法强制，不能启动）</option><option value="read_write">读写访问（stdio的实际能力）</option></select></label>
    <fieldset><legend className="text-sm">进程启动风险声明</legend><div className="flex flex-wrap gap-3">{['network','credentials','loopback','outside_workspace','privilege_escalation','git_write','external_effect'].map(r=><label className="text-xs" key={r}><input type="checkbox" checked={risks.includes(r)} onChange={e=>setRisks(v=>e.target.checked?[...v,r]:v.filter(x=>x!==r))}/>{r}</label>)}</div></fieldset>
    <Card title="本地工具允许列表与风险映射"><p className="text-xs">远端描述不会授权工具。每个允许的工具需指定效果与审批策略；可先刷新查看名称，再回来编辑。</p>{mappings.map((m,i)=><div className="grid gap-2 rounded border border-subtle p-2" key={i}><label className="text-sm">远端工具名称<input aria-label={`MCP工具名称${i+1}`} className={fieldClass} value={m.remote_name} onChange={e=>update(i,{remote_name:e.target.value})}/></label><label className="text-sm">效果<select className={fieldClass} value={m.effect} onChange={e=>update(i,{effect:e.target.value})}><option value="none">只读/无写入效果</option><option value="session_write">工作区写入</option><option value="persistent_write">外部持久化写入</option></select></label><label className="text-sm">审批<select className={fieldClass} value={m.approval} onChange={e=>update(i,{approval:e.target.value})}><option value="require_approval">逐次审批</option><option value="allow">允许映射（仍受运行权限限制）</option><option value="deny">拒绝</option></select></label><button className={buttonClass} onClick={()=>setMappings(rows=>rows.filter((_,n)=>n!==i))}>移除此工具映射</button></div>)}<button className={buttonClass} disabled={mappings.length>=256} onClick={()=>setMappings(v=>[...v,{remote_name:'',effect:'none',approval:'require_approval',enabled:true}])}>添加工具映射</button></Card>
    {initial&&<label className="block text-sm"><input type="checkbox" checked={keepCredentials} onChange={e=>setKeepCredentials(e.target.checked)}/>保留现有凭据绑定</label>}{!keepCredentials&&<label className="block text-sm">凭据环境变量名，每行一个<textarea aria-label="MCP凭据变量名" className={fieldClass} value={environment} onChange={e=>setEnvironment(e.target.value)} placeholder="SERVICE_TOKEN"/></label>}<p className="text-xs text-secondary">凭据请在保存配置后填写。</p>
    <div className="flex gap-2"><button className={buttonClass} disabled={busy||!/^mcp_[a-z0-9][a-z0-9_-]*$/.test(id)||!executable.startsWith('/')||mappings.some(m=>!m.remote_name)||new Set(mappings.map(m=>m.remote_name)).size!==mappings.length} onClick={()=>void save().then(ok=>{if(ok)onClose()})}>保存为停用配置</button><button className={buttonClass} onClick={onClose}>取消编辑</button></div>
  </Card>
}

function catalogStatusLabel(status: string): string {
  if (status === 'ready') return '目录可用'
  if (status === 'stale') return '目录过期'
  if (status === 'missing') return '尚未刷新目录'
  if (status === 'error') return '目录刷新失败'
  return status
}

function toolName(entry: unknown, index: number): string {
  if (entry && typeof entry === 'object') {
    const record = entry as { name?: unknown; remote_name?: unknown }
    if (typeof record.name === 'string') return record.name
    if (typeof record.remote_name === 'string') return record.remote_name
  }
  return `工具 ${index + 1}`
}

export function McpManager({client,scope,refresh,item,registerGuard}:{client:ApiClient;scope:string;refresh:number;item?:string;registerGuard?:(guard:DirtyGuard)=>()=>void}){
  const [catalog,setCatalog]=useState<Catalog|null>(null);const [detail,setDetail]=useState<Detail|null>(null);const [selected,setSelected]=useState('');const [page,setPage]=useState(0);const [local,setLocal]=useState(0)
  const [form,setForm]=useState<{base:Base;initial:Detail|null}|null>(null);const [confirm,setConfirm]=useState<{action:string;detail:Detail}|null>(null);const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');const [job,setJob]=useState<string|null>(null)
  const [secret,setSecret]=useState('');const [env,setEnv]=useState('');const retry=useRef<{key:string;id:string}|null>(null)
  const load=()=>setLocal(v=>v+1)
  const dirty=form!==null||secret.trim().length>0
  const {open:guardOpen,confirmLeave,settle}=useLeavePrompt()
  const promptLeave=useCallback(()=>dirty?confirmLeave():Promise.resolve(true),[confirmLeave,dirty])
  useLeaveGuard(registerGuard,dirty,promptLeave)
  const inspect=async(id:string)=>{try{const v=await client.mcpQuery<Detail>({scope,identity:id});setDetail(v);setSelected(id);setEnv(v.credentials[0]?.name??'');setSecret('');setMessage('')}catch(e){setMessage((e as Error).message)}}
  useEffect(()=>{let alive=true;setCatalog(null);setDetail(null);setSelected('');setMessage('');void client.mcpQuery<Catalog>({scope,page}).then(v=>{if(alive)setCatalog(v)},e=>{if(alive)setMessage(e.message)});return()=>{alive=false}},[client,scope,page,refresh,local])
  useEffect(()=>{
    if(!item||!catalog)return
    const needle=item.trim().toLowerCase()
    const hit=catalog.servers.find(s=>s.server_id.toLowerCase()===needle||s.display_name.toLowerCase()===needle)
      ??catalog.servers.find(s=>s.display_name.toLowerCase().includes(needle)||s.server_id.toLowerCase().includes(needle))
    if(hit)void inspect(hit.server_id)
    else setMessage(`未找到名为「${item}」的 MCP 服务，已打开列表。`)
  },[item,catalog])
  useEffect(()=>{if(!job)return;let alive=true;const poll=()=>void client.mcpJob(job).then(v=>{if(!alive)return;setMessage(`MCP目录刷新：${v.status}${v.error?' · '+v.error:''}`);if(!['running','queued'].includes(v.status)){setJob(null);load();if(selected)void inspect(selected)}},e=>{if(alive)setMessage(e.message)});poll();const timer=setInterval(poll,1000);return()=>{alive=false;clearInterval(timer)}},[client,job])
  const act=async(body:Record<string,unknown>)=>{if(busy)return false;setBusy(true);const key=JSON.stringify(body);const id=retry.current?.key===key?retry.current.id:commandId('mcp');retry.current={key,id};try{const v=await client.mcpAction({...body,command_id:id});retry.current=null;setMessage(`MCP操作：${v.status}`);if(v.command_id)setJob(v.command_id);else{setDetail(null);setSecret('')}load();return true}catch(e){if(e instanceof ApiError&&e.status<500)retry.current=null;setMessage((e as Error).message);return false}finally{setBusy(false)}}
  const saveSecret=async()=>{if(!detail)return;setBusy(true);try{await client.mcpCredential({scope,server_id:detail.server.server_id,expected_server_revision:detail.server.config_revision,environment_name:env,secret});setSecret('');await inspect(detail.server.server_id);setMessage('凭据已保存；之后的新连接使用当前值。')}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}}
  const origin = scope === 'global' ? 'global' : 'workspace'
  return <div className="space-y-4"><p role="status" className="text-sm">{message}</p>
    {!catalog && !message && <p role="status">正在读取 MCP 目录…</p>}
    {catalog&&<><button type="button" className={buttonClass} disabled={busy} onClick={()=>setForm({base:catalog,initial:null})}>添加 MCP 服务</button><Pager page={page} next={catalog.next_cursor} onChange={setPage}/></>}
    {form&&<McpEditor key={form.initial?.server.server_id??'new'} scope={scope} base={form.base} initial={form.initial} busy={busy} onSave={act} onClose={()=>setForm(null)}/>}
    <Card title="MCP 目录与状态">{catalog?.servers.map(s=><button type="button" className={`${buttonClass} block w-full text-left`} key={s.server_id} onClick={()=>void inspect(s.server_id)}>
      <span className="flex flex-wrap items-center gap-2"><OriginBadge origin={origin} />{s.display_name} · {s.enabled?'已启用':'停用'} · {catalogStatusLabel(s.catalog_status)} · {s.tool_count} 项工具</span>
      {s.degraded_reason && <span className="block text-xs">限制：{s.degraded_reason}</span>}
    </button>)}{catalog?.servers.length===0&&<p className="text-sm text-secondary">此范围尚无 MCP 服务。</p>}</Card>
    {detail&&<Card title={detail.server.display_name}>
      <FieldList items={[
        {label:'传输',value:detail.server.transport},
        {label:'可执行文件',value:detail.server.executable},
        {label:'目录策略',value:detail.server.cwd_policy},
        {label:'工作区可见性',value:detail.server.workspace_visibility},
        {label:'目录',value:catalogStatusLabel(detail.server.catalog_status)},
        {label:'工具数',value:String(detail.server.tool_count)},
        {label:'限制',value:detail.server.degraded_reason},
      ]} />
      {detail.server.catalog_status !== 'ready' && <p role="status">{detail.server.degraded_reason ?? '请连接并刷新工具目录。'}</p>}
      <details><summary>工具目录、状态与本地映射</summary>
        {detail.tools.length
          ? <ul className="asset-evidence">{detail.tools.map((tool, index) => <li key={index}>{toolName(tool, index)}</li>)}</ul>
          : <p className="py-2 text-xs text-secondary">暂无工具目录</p>}
      </details>
      <div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} onClick={()=>setForm({base:detail,initial:detail})}>编辑 MCP 配置</button>{[['refresh','刷新 MCP 目录'],[detail.server.enabled?'disable':'enable',detail.server.enabled?'停用 MCP 服务':'启用 MCP 服务'],['remove','移除 MCP 服务']].map(([action,label])=><button type="button" className={buttonClass} disabled={busy||!!job} key={action} onClick={()=>setConfirm({action,detail})}>{label}</button>)}</div>
      <Card title="凭据（仅写入）"><p className="text-xs">{detail.credential_status}</p>{detail.credentials.map(v=><p className="text-xs" key={v.name}>{v.name} · {v.available?'已配置':'缺失或不可访问'}</p>)}{detail.credentials.length>0&&<><label className="block text-sm">环境变量<select aria-label="MCP凭据变量" className={fieldClass} value={env} onChange={e=>setEnv(e.target.value)}>{detail.credentials.map(v=><option key={v.name}>{v.name}</option>)}</select></label><label className="block text-sm">新凭据<input aria-label="MCP新凭据" className={fieldClass} type="password" autoComplete="new-password" value={secret} onChange={e=>setSecret(e.target.value)}/></label><button type="button" className={buttonClass} disabled={busy||!secret} onClick={()=>void saveSecret()}>保存 MCP 凭据</button></>}</Card>
    </Card>}
    {confirm&&<Card title="确认 MCP 操作"><p className="text-sm">{scope==='global'?'全局安装':'本项目'} · {confirm.detail.server.display_name}</p><p className="break-all text-sm">{confirm.detail.server.executable} · 目录策略：{confirm.detail.server.cwd_policy}</p><p className="text-sm">{confirm.action==='refresh'?'将启动此进程进行握手与目录读取，使用其已声明凭据。':confirm.action==='remove'?'删除此范围的配置；之后的运行不能再选择它。':'更改之后的运行是否可选择此服务；工具仍受当前权限与审批限制。'}</p>
      <FieldList items={[
        {label:'工作区可见性',value:confirm.detail.server.workspace_visibility},
        {label:'启动风险',value:confirm.detail.server.requested_launch_risks.join('、')||'未声明'},
      ]} />
      <button type="button" className={buttonClass} disabled={busy} onClick={()=>void act({action:confirm.action,scope,server_id:confirm.detail.server.server_id,expected_revision:confirm.detail.revision,expected_digest:confirm.detail.digest,expected_server_revision:confirm.detail.server.config_revision,confirmed:true}).then(ok=>{if(ok)setConfirm(null)})}>确认 MCP 操作</button><button type="button" className={buttonClass} onClick={()=>setConfirm(null)}>返回</button></Card>}
    <LeaveGuardDialog
      open={guardOpen}
      title="MCP 配置尚未保存"
      description={secret.trim() ? '凭据尚未保存，离开将丢失输入。' : '服务配置尚未保存。'}
      onDiscard={() => { setForm(null); setSecret(''); settle(true) }}
      onStay={() => settle(false)}
    />
  </div>
}
