import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import { ApiError } from '../api/client'
import { Card, FieldList } from './management/components'
import { buttonClass, fieldClass } from './management/styles'
import { commandId } from './lib/editor'

type Row={review_id?:string;job_id?:string;activation_id?:string;operation_id?:string;status:string;state?:string;row_version:number;review_version?:number;task_outcome_id?:string;target?:string;path?:string;scope?:string;failure_code?:string}
type Page={items:Row[];next_cursor:string|null}
export function learningRowIdentity(tab:string,row:Row){return tab==='activations'?row.activation_id!:tab==='promotions'?row.operation_id!:row.review_id!}

export function useKnowledgeAction(client:ApiClient,onChanged:()=>void){
  const [busy,setBusy]=useState(false);const [message,setMessage]=useState('');const [job,setJob]=useState<string|null>(null)
  const retry=useRef<{key:string;id:string}|null>(null)
  useEffect(()=>{if(!job)return;let alive=true;const poll=()=>void client.knowledgeJob(job).then(value=>{if(!alive)return;setMessage(`审阅请求：${value.status}${value.error?' · '+value.error:''}`);if(!['running','queued'].includes(value.status)){setJob(null);onChanged()}},e=>{if(alive)setMessage(e.message)});poll();const timer=setInterval(poll,1000);return()=>{alive=false;clearInterval(timer)}},[client,job])
  const act=async(kind:'learning'|'inbox',body:Record<string,unknown>)=>{if(busy)return false;setBusy(true);setMessage('');const key=JSON.stringify([kind,body]);const id=retry.current?.key===key?retry.current.id:commandId('knowledge');retry.current={key,id};try{const result=await client.knowledgeCommand(kind,{...body,command_id:id});retry.current=null;setMessage(`操作：${result.status}`);if(result.command_id)setJob(result.command_id);onChanged();return true}catch(e){if(e instanceof ApiError&&e.status<500)retry.current=null;setMessage((e as Error).message);return false}finally{setBusy(false)}}
  return {act,busy,message}
}

export function LearningOperations({client,refresh,onChanged}:{client:ApiClient;refresh:number;onChanged:()=>void}){
  const [policy,setPolicy]=useState<{mode:string;row_version:number}|null>(null);const [summary,setSummary]=useState<{pending_reviews?:number;proposed_candidates?:number;failed_reviews?:number}|null>(null)
  const [tab,setTab]=useState('reviews');const [status,setStatus]=useState('');const [cursor,setCursor]=useState<string|undefined>();const [history,setHistory]=useState<(string|undefined)[]>([])
  const [page,setPage]=useState<Page>({items:[],next_cursor:null});const [error,setError]=useState('');const [preview,setPreview]=useState<{id:string;action:string;label:string;lines?:string[]}|null>(null)
  const {act,busy,message}=useKnowledgeAction(client,onChanged)
  useEffect(()=>{let alive=true;void client.knowledgeQuery<{policy:{mode:string;row_version:number};pending_reviews?:number;proposed_candidates?:number;failed_reviews?:number}>('learning-status').then(value=>{if(alive){setPolicy(value.policy);setSummary(value)}},e=>{if(alive)setError(e.message)});return()=>{alive=false}},[client,refresh])
  useEffect(()=>{let alive=true;void client.knowledgeQuery<Page|Row[]>(tab,{status,cursor,limit:50}).then(value=>{if(alive){setPage(Array.isArray(value)?{items:value,next_cursor:null}:value);setError('')}},e=>{if(alive)setError(e.message)});return()=>{alive=false}},[client,refresh,tab,status,cursor])
  const reset=()=>{setCursor(undefined);setHistory([]);setPreview(null)}
  return <Card title="Learning 模式与审阅"><p className="text-xs">作用域：当前项目。模式只影响后续学习；候选接受、配置推广和 Skill 启用保持独立。</p>
    {policy&&<label className="block text-sm">学习模式<select aria-label="学习模式" className={fieldClass} value={policy.mode} disabled={busy} onChange={e=>void act('learning',{action:'mode',mode:e.target.value,expected_row_version:policy.row_version})}><option value="off">关闭</option><option value="review_only">审阅后决定</option><option value="explicit_auto" disabled>自动学习（当前 Core 尚未开放）</option></select></label>}
    <FieldList items={[
      {label:'待处理审阅',value:summary?.pending_reviews!=null?String(summary.pending_reviews):undefined},
      {label:'待确认候选',value:summary?.proposed_candidates!=null?String(summary.proposed_candidates):undefined},
      {label:'失败审阅',value:summary?.failed_reviews!=null?String(summary.failed_reviews):undefined},
    ]} />
    <nav className="flex flex-wrap gap-2">{[['reviews','审阅记录'],['promotions','待恢复推广'],['activations','配置激活与撤销']].map(([key,label])=><button type="button" className={buttonClass} key={key} aria-pressed={tab===key} onClick={()=>{setTab(key);setStatus('');reset()}}>{label}</button>)}</nav>
    <label className="block text-sm">状态筛选<select className={fieldClass} value={status} onChange={e=>{setStatus(e.target.value);reset()}}><option value="">全部 / 待处理</option>{(tab==='reviews'?['pending','running','completed','failed','superseded']:tab==='promotions'?['prepared','needs_resolution','finalized','aborted']:[]).map(s=><option key={s}>{s}</option>)}</select></label>
    {page.items.map(row=>{const id=learningRowIdentity(tab,row);return <article className="rounded-lg border border-subtle p-3 text-sm" key={id}><p>{row.target??row.path??'记录'} · {row.status??row.state}{row.failure_code?` · ${row.failure_code}`:''}</p>
      {tab==='reviews'&&<><button type="button" className={buttonClass} disabled={busy||row.status!=='pending'} onClick={()=>void act('learning',{action:'review',target:id,expected_row_version:row.row_version})}>运行此审阅</button><button type="button" className={buttonClass} disabled={busy||row.status!=='failed'} onClick={()=>void act('learning',{action:'retry',target:id,expected_row_version:row.row_version})}>重试失败审阅</button></>}
      {tab==='promotions'&&<div className="flex flex-wrap gap-2">{[['retry','重试推广'],['finalize','完成已写入推广'],['cancel','取消未写入推广'],['abort','终止冲突推广']].map(([action,label])=><button type="button" className={buttonClass} disabled={busy} key={action} onClick={()=>setPreview({id,action,label})}>{label}</button>)}</div>}
      {tab==='activations'&&row.status==='active'&&<button type="button" className={buttonClass} onClick={()=>void client.knowledgeQuery<{preview?:string[]}>('undo-preview',{identity:id}).then(value=>setPreview({id,action:'undo',label:'撤销此激活',lines:value.preview}),e=>setError(e.message))}>预览撤销此激活</button>}
    </article>})}
    {!page.items.length&&<p className="text-sm text-secondary">当前筛选没有记录。</p>}
    <div className="flex gap-2"><button type="button" className={buttonClass} disabled={!history.length} onClick={()=>{setCursor(history.at(-1));setHistory(h=>h.slice(0,-1))}}>上一页</button><button type="button" className={buttonClass} disabled={!page.next_cursor} onClick={()=>{setHistory(h=>[...h,cursor]);setCursor(page.next_cursor!)}}>下一页</button></div>
    {preview&&<Card title="确认具体变更"><p>{preview.label}</p>{preview.lines?.length?<ul className="asset-evidence">{preview.lines.map(line=><li key={line}>{line}</li>)}</ul>:<p className="text-xs text-secondary">将按服务端允许的动作执行。</p>}<button type="button" className={buttonClass} disabled={busy} onClick={()=>void act('learning',preview.action==='undo'?{action:'undo',target:preview.id}:{action:'promotion',target:preview.id,recovery_action:preview.action}).then(ok=>{if(ok)setPreview(null)})}>确认执行</button><button type="button" className={buttonClass} onClick={()=>setPreview(null)}>返回</button></Card>}
    {(error||message)&&<p role="status">{error||message}</p>}
  </Card>
}
