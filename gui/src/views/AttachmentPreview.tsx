import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { AttachmentPart, AttachmentWire } from '../api/attachments'

const readingNames: Record<string,string> = {text:'已提取文本',image:'图像输入',pdf_text:'PDF 文本',pdf_images:'PDF 扫描页图像',pdf_mixed:'PDF 文本与扫描页图像'}
export function AttachmentPreview({client,workspace,session,id,onClose}: {client:ApiClient;workspace:string;session:string;id:string;onClose:()=>void}) {
  const [row,setRow]=useState<AttachmentWire|null>(null);const [error,setError]=useState('')
  useEffect(()=>{let alive=true;void client.attachment(workspace,session,id).then(r=>{if(alive)setRow(r)},e=>{if(alive)setError(e.message)});return()=>{alive=false}},[client,workspace,session,id])
  return <section className="attachment-preview" aria-label="附件预览"><header><strong>{row?.name??'附件'}</strong><button className="editor-button" onClick={onClose}>关闭预览</button></header>
    {error&&<p role="alert">{error}</p>}{row?.representation&&<><p>{row.representation.source_path?`${row.representation.source_path} · `:''}{readingNames[row.representation.reading]} · {row.byte_size} 字节{row.representation.pages>0?` · ${row.representation.pages} 页`:''}</p>
    {row.representation.omitted_chars>0&&<p role="status">受上下文额度限制，省略 {row.representation.omitted_chars} 字符；模型只读取下方提取部分。</p>}
    {row.representation.parts.map(part=><PreviewPart key={part.artifact_id} client={client} workspace={workspace} session={session} id={id} part={part}/>)}
    {row.representation.previews.map(part=><PreviewPart key={part.artifact_id} client={client} workspace={workspace} session={session} id={id} part={part} previewOnly/>)}
    </>}</section>
}
function PreviewPart({client,workspace,session,id,part,previewOnly=false}:{client:ApiClient;workspace:string;session:string;id:string;part:AttachmentPart;previewOnly?:boolean}){
  const [text,setText]=useState('');const [error,setError]=useState('')
  useEffect(()=>{let alive=true;if(part.media_type==='text/plain')void client.attachmentText(workspace,session,id,part.artifact_id).then(value=>{if(alive)setText(value)},()=>{if(alive)setError('文本读取失败，请重新打开预览。')});return()=>{alive=false}},[client,workspace,session,id,part])
  return <figure>{part.page&&<figcaption>第 {part.page} 页{previewOnly?' · 页面预览；模型读取提取文字':' · 实际输入'}</figcaption>}{part.media_type==='text/plain'?<pre>{error||text}</pre>:<img src={client.attachmentContentUrl(workspace,session,id,part.artifact_id)} alt={`附件图像${part.page?`，第 ${part.page} 页`:''}`} loading="lazy"/>}</figure>
}
export function HistoryAttachments({client,workspace,session,ids}:{client:ApiClient;workspace:string;session:string;ids:string[]}){
  const [preview,setPreview]=useState<string|null>(null)
  return <div className="mt-2">{ids.map((id,i)=><button key={id} className="editor-button" onClick={()=>setPreview(id)}>查看附件 {i+1}</button>)}{preview&&<AttachmentPreview client={client} workspace={workspace} session={session} id={preview} onClose={()=>setPreview(null)}/>}</div>
}
