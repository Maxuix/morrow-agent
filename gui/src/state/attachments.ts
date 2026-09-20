import { useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../api/client'
import { fileMediaType, type AttachmentLimits, type AttachmentRef, type AttachmentWire } from '../api/attachments'
import { commandId } from '../views/lib/editor'

export interface AttachmentDraft {key: string; name: string; row?: AttachmentWire; error?: string; busy?: boolean}
const storageKey = 'morrow.attachments.v1'
export class AttachmentDraftStorage {
  constructor(private storage: Pick<Storage,'getItem'|'setItem'>) {}
  private all(): Record<string, AttachmentDraft[]> {
    try {
      const raw:unknown=JSON.parse(this.storage.getItem(storageKey) ?? '{}')
      if(!raw||typeof raw!=='object'||Array.isArray(raw))return {}
      return Object.fromEntries(Object.entries(raw).filter(([key,value])=>key.length<=300&&Array.isArray(value)).slice(0,20).map(([key,value])=>[key,(value as AttachmentDraft[]).filter(item=>item&&typeof item.key==='string'&&item.key.length<=128&&typeof item.name==='string'&&item.name.length<=1024&&(!item.row||(typeof item.row.attachment_id==='string'&&typeof item.row.revision==='number'&&(!item.row.representation||Array.isArray(item.row.representation.parts))))).slice(0,8)]))
    }catch{return {}}
  }
  read(workspace: string, session: string): AttachmentDraft[] {return (this.all()[JSON.stringify([workspace,session])] ?? []).slice(0,8)}
  write(workspace: string, session: string, entries: AttachmentDraft[]) {
    const all = this.all(); const key = JSON.stringify([workspace,session])
    if (entries.length && !(key in all) && Object.keys(all).length >= 20) throw new Error('已有 20 个会话附件草稿，请先清理一份。')
    if(entries.length > 8) throw new Error('每条消息最多 8 个附件。')
    if (entries.length) all[key] = entries.map(({key,name,row,error}) => ({key,name,row,error})); else delete all[key]
    this.storage.setItem(storageKey,JSON.stringify(all))
  }
}

export function useAttachments(client: ApiClient, workspace: string, session: string, limits?: AttachmentLimits) {
  const storage = useRef(new AttachmentDraftStorage(sessionStorage))
  const [entries,setEntries] = useState<AttachmentDraft[]>(() => storage.current.read(workspace,session))
  const [error,setError] = useState('')
  const current = useRef(entries); const alive = useRef(true); const files = useRef(new Map<string,File>())
  const change = (fn: (value: AttachmentDraft[]) => AttachmentDraft[]) => {
    const next = fn(current.current); current.current=next
    try {storage.current.write(workspace,session,next)} catch(e) {if(alive.current)setError((e as Error).message)}
    if(alive.current)setEntries(next)
  }
  const update = (key:string, patch:Partial<AttachmentDraft>) => change(all=>all.map(item=>item.key===key?{...item,...patch}:item))
  useEffect(() => {
    alive.current=true; let timer: ReturnType<typeof setTimeout>
    const poll=async()=>{
      for(const item of current.current){
        if(!item.row || item.busy)continue
        try {const row=await client.attachment(workspace,session,item.row.attachment_id); if(!alive.current)return
          if(row.state==='released')change(all=>all.filter(e=>e.key!==item.key))
          else if(row.revision!==item.row.revision)update(item.key,{row,error:undefined})
        } catch(e) {if(alive.current)update(item.key,{error:(e as Error).message})}
      }
      if(alive.current)timer=setTimeout(()=>void poll(),1000)
    }
    void poll(); return()=>{alive.current=false;clearTimeout(timer)}
  },[client,workspace,session])
  const run = async (key:string, file:File) => {
    files.current.set(key,file); update(key,{busy:true,error:undefined})
    try {
      let row=current.current.find(i=>i.key===key)?.row
      const media=fileMediaType(file)
      if(!row)row=await client.reserveAttachment(workspace,{command_id:key,session_id:session,name:file.name,media_type:media,byte_size:file.size})
      update(key,{row})
      if(['reserved','failed'].includes(row.state))row=await client.uploadAttachment(workspace,session,row,file,media)
      update(key,{row,busy:false})
    }catch(e){update(key,{busy:false,error:(e as Error).message})}
  }
  const add = (selected: FileList | File[]) => {
    setError('')
    for(const file of Array.from(selected)) {
      if(current.current.length >= (limits?.message_files ?? 8)){setError('附件数量达到上限，请移除后重试。');break}
      if(!file.size || file.size > (limits?.file_bytes ?? 8388608)){setError(`${file.name} 为空或超过文件大小上限。`);continue}
      const key=commandId('attachment');change(all=>[...all,{key,name:file.name,busy:true}]);void run(key,file)
    }
  }
  const reference=async(path:string)=>{
    if(current.current.length >= (limits?.message_files ?? 8)){setError('附件数量达到上限。');return}
    const key=commandId('reference');change(all=>[...all,{key,name:path,busy:true}])
    try{const row=await client.referenceAttachment(workspace,session,path,key);update(key,{row,busy:false})}
    catch(e){update(key,{busy:false,error:(e as Error).message})}
  }
  const remove=async(key:string)=>{
    const item=current.current.find(i=>i.key===key)
    if(item?.busy){setError('文件正在上传，完成后可移除。');return}
    try{if(item?.row && item.row.state!=='submitted')await client.releaseAttachment(workspace,session,item.row)
      files.current.delete(key);change(all=>all.filter(i=>i.key!==key))
    }catch(e){setError((e as Error).message)}
  }
  const consume=(references:AttachmentRef[]=[])=>{
    const ids=new Set(references.map(r=>r.attachment_id))
    change(all=>all.filter(i=>!i.row || !ids.has(i.row.attachment_id)))
  }
  return {entries,error,add,reference,remove,consume,
    retry:(key:string,file?:File)=>{const selected=file??files.current.get(key);if(selected)void run(key,selected);else setError('请重新选择原文件以重试上传。')},
    references:entries.flatMap(i=>i.row?.reference?[i.row.reference]:[]),
    ready:entries.every(i=>!i.busy&&!i.error&&i.row&&['ready','submitted'].includes(i.row.state))}
}
