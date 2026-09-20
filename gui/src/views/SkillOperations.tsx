import { useCallback, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import { ApiError } from '../api/client'
import type { ManagedSkill } from '../api/management'
import type { DirtyGuard } from '../state/navigation'
import { Card, FieldList } from './management/components'
import { LeaveGuardDialog, useLeaveGuard, useLeavePrompt } from './management/LeaveGuard'
import { buttonClass, fieldClass } from './management/styles'
import type { Mutate } from './management/types'
import { commandId } from './lib/editor'

type Validation={valid:boolean;tree_digest?:string;skill_id?:string;conflicts:string[];errors:string[];file_count:number;total_bytes:number}
function useSkillAction(client:ApiClient){
  const retry=useRef<{key:string;id:string}|null>(null);const [message,setMessage]=useState('');const [busy,setBusy]=useState(false)
  const run=async<T,>(body:Record<string,unknown>):Promise<T|null>=>{if(busy)return null;setBusy(true);const key=JSON.stringify(body);const id=retry.current?.key===key?retry.current.id:commandId('skill');retry.current={key,id};try{const result=await client.skillAction<T>({...body,command_id:id});retry.current=null;setMessage(body.action==='validate'?'校验完成，请检查来源、权限和脚本。':'操作已保存。');return result}catch(e){if(e instanceof ApiError&&e.status<500)retry.current=null;setMessage((e as Error).message);return null}finally{setBusy(false)}}
  return {run,message,busy}
}
export function SkillRemoval({client,scope,item,version,digest,onChanged}:{client:ApiClient;scope:string;item:ManagedSkill;version:string;digest:string;onChanged:()=>void}){
  const [remove,setRemove]=useState<'binding'|'version'|null>(null);const {run,message,busy}=useSkillAction(client)
  const source=item.versions.find(v=>v.version.version_id===version)?.version.source_kind??item.status.source_kind
  const target=scope==='global'?'全局安装':'本项目绑定'
  return <div className="space-y-2"><div className="flex gap-2"><button type="button" className={buttonClass} onClick={()=>setRemove('binding')}>移除此{scope==='global'?'全局':'项目'}绑定</button><button type="button" className={buttonClass} disabled={!version||!['imported','generated'].includes(source)} onClick={()=>setRemove('version')}>移除所选包版本</button></div>
    {remove&&<Card title="确认移除 Skill"><p className="text-sm">{target} · {item.status.name} · {source}</p><p className="text-sm">{remove==='version'?'将删除此不可变包版本并移除绑定。仍被运行或历史引用的版本不能删除。':'移除当前范围的选择与启用绑定；包版本保留。'}</p><button type="button" className={buttonClass} disabled={busy} onClick={()=>void run({action:'remove',scope,skill_id:item.status.skill_id,source,version_id:remove==='version'?version:null,expected_binding_digest:digest,confirmed:true}).then(v=>{if(v){setRemove(null);onChanged()}})}>确认移除</button><button type="button" className={buttonClass} onClick={()=>setRemove(null)}>返回</button></Card>}
    {message&&<p role="status">{message}</p>}</div>
}
export function SkillOperations({client,scope,onChanged,registerGuard}:{client:ApiClient;scope:string;mutate:Mutate;onChanged:()=>void;registerGuard?:(guard:DirtyGuard)=>()=>void}){
  const [path,setPath]=useState('');const source='imported';const [preview,setPreview]=useState<Validation|null>(null)
  const {run,message,busy}=useSkillAction(client)
  const dirty=path.trim().length>0||preview!==null
  const {open:guardOpen,confirmLeave,settle}=useLeavePrompt()
  const promptLeave=useCallback(()=>dirty?confirmLeave():Promise.resolve(true),[confirmLeave,dirty])
  useLeaveGuard(registerGuard,dirty,promptLeave)
  const target=scope==='global'?'全局（所有项目可选用，默认不在本项目启用）':'本项目（默认停用）'
  return <div className="space-y-4"><Card title="校验并安装本地 Skill"><label className="block text-sm">包含 SKILL.md 的目录<input aria-label="Skill 本地目录" className={fieldClass} value={path} onChange={e=>{setPath(e.target.value);setPreview(null)}} placeholder="/absolute/path/to/skill"/></label><p className="text-xs text-secondary">安装目标：{target}</p><button type="button" className={buttonClass} disabled={busy||!path.startsWith('/')} onClick={()=>void run<Validation>({action:'validate',scope,path,source}).then(setPreview)}>校验目录与预览</button>
    {preview&&<><FieldList items={[
      {label:'结果',value:preview.valid?'校验通过':'校验未通过'},
      {label:'文件数',value:String(preview.file_count)},
      {label:'冲突',value:preview.conflicts.join('；')||'无'},
      {label:'错误',value:preview.errors.join('；')||'无'},
    ]}/><button type="button" className={buttonClass} disabled={busy||!preview.valid||preview.conflicts.length>0} onClick={()=>void run({action:'install',scope,path,source,expected_tree_digest:preview.tree_digest,confirmed:true}).then(v=>{if(v){setPreview(null);onChanged()}})}>确认安装此版本（默认停用）</button></>}{message&&<p role="status">{message}</p>}
    <LeaveGuardDialog open={guardOpen} title="Skill 安装草稿尚未完成" description="离开将丢失未提交的安装配置。" onDiscard={()=>{setPath('');setPreview(null);settle(true)}} onStay={()=>settle(false)}/>
    </Card>
  </div>
}
