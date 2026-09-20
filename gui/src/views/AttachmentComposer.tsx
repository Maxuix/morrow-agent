import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { FileSearch } from '../api/attachments'
import type { useAttachments } from '../state/attachments'
import { AttachmentPreview } from './AttachmentPreview'

/**
 * The composer's attachment area: workspace-file suggestions for a trailing
 * @, the draft tray and on-demand errors. Upload/referencing entries live in
 * the ＋ menu, so no permanent button row or hint is rendered here.
 */
export function AttachmentComposer({client,workspace,session,text,onText,attachments,imageSupported,disabled}:{client:ApiClient;workspace:string;session:string;text:string;onText:(text:string)=>void;attachments:ReturnType<typeof useAttachments>;imageSupported:boolean;disabled:boolean}) {
  const [preview,setPreview]=useState<string|null>(null)
  const [search,setSearch]=useState<FileSearch|null>(null);const [error,setError]=useState('')
  const match=/(?:^|\s)@([^\s]*)$/.exec(text);const query=match?.[1]
  useEffect(()=>{let alive=true;if(query===undefined){setSearch(null);return};void client.searchAttachmentFiles(workspace,query).then(value=>{if(alive){setSearch(value);setError('')}},()=>{if(alive)setError('工作区文件搜索失败。')});return()=>{alive=false}},[client,workspace,query])
  const needsImages=attachments.entries.some(i=>i.row?.representation?.parts.some(p=>p.media_type.startsWith('image/')))
  return <div className="attachment-composer" aria-label="消息附件">
    {search&&<div className="attachment-file-menu" aria-label="工作区文件建议">{search.files.map(file=><button className="editor-button" key={file.path} disabled={disabled} onClick={()=>{void attachments.reference(file.path);onText(text.replace(/@[^\s]*$/,''));setSearch(null)}}>{file.path} · {file.byte_size} 字节</button>)}{search.files.length>1&&<button className="editor-button" disabled={disabled} onClick={()=>{search.files.slice(0,8-attachments.entries.length).forEach(file=>void attachments.reference(file.path));onText(text.replace(/@[^\s]*$/,''));setSearch(null)}}>引用前 {Math.min(search.files.length,8-attachments.entries.length)} 个匹配文件</button>}{!search.files.length&&<p>没有匹配文件。</p>}{search.truncated&&<p>结果过多，请缩小搜索范围。</p>}</div>}
    {attachments.entries.length>0&&<div className="attachment-tray">{attachments.entries.map(item=><div key={item.key} className="attachment-card"><strong>{item.name}</strong><span>{item.busy?'正在上传':item.error??item.row?.reason??({reserved:'等待上传',uploading:'正在上传',processing:'正在解析',ready:'可发送',submitted:'已保留'} as Record<string,string>)[item.row?.state??'']}</span>
      {item.row?.reference&&<button className="editor-button" onClick={()=>setPreview(item.row!.attachment_id)}>预览</button>}
      {(item.error||item.row?.state==='failed')&&<><button className="editor-button" disabled={disabled} onClick={()=>attachments.retry(item.key)}>重试</button><label className="editor-button">重选文件<input type="file" disabled={disabled} onChange={e=>{if(e.target.files?.[0])attachments.retry(item.key,e.target.files[0])}}/></label></>}
      <button className="editor-button" disabled={disabled||item.busy} onClick={()=>void attachments.remove(item.key)} aria-label={`移除附件 ${item.name}`}>移除</button></div>)}</div>}
    {(error||attachments.error)&&<p role="alert">{error||attachments.error}</p>}
    {needsImages&&!imageSupported&&<p role="alert">当前模型不支持图像输入；请选择支持图像的模型，或移除图片/扫描 PDF。</p>}
    {preview&&<AttachmentPreview client={client} workspace={workspace} session={session} id={preview} onClose={()=>setPreview(null)}/>}
  </div>
}
