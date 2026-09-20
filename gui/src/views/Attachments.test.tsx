import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import { fileMediaType, type AttachmentWire } from '../api/attachments'
import { AttachmentDraftStorage } from '../state/attachments'
import { ChatComposer } from './ChatComposer'

describe('Attachment inputs',()=>{
  it('attachment-only input is sendable while an empty message remains disabled',()=>{
    const props={text:'',onText:()=>{},onSend:()=>{},active:false,pending:false,ready:true,
      capabilities:{workspace_id:'ws_1',interaction_protocol_version:1,execution_ready:true,features:{},limits:{text_chars:4096}},commands:[],onCommand:()=>{},onStop:()=>{}}
    expect(renderToStaticMarkup(<ChatComposer {...props} hasAttachments/>)).not.toMatch(/disabled=""[^>]*aria-label="发送"/)
    expect(renderToStaticMarkup(<ChatComposer {...props}/>)).toMatch(/disabled=""[^>]*aria-label="发送"/)
  })
  it('keeps attachment draft references scoped and bounded, recovers invalid browser storage',()=>{
    let raw='null';const storage={getItem:()=>raw,setItem:(_key:string,value:string)=>{raw=value}}
    const drafts=new AttachmentDraftStorage(storage)
    expect(drafts.read('ws_a','ses_a')).toEqual([])
    drafts.write('ws_a','ses_a',[{key:'draft.a',name:'source.py'}])
    expect(drafts.read('ws_b','ses_a')).toEqual([])
    expect(drafts.read('ws_a','ses_a')[0].name).toBe('source.py')
    expect(()=>drafts.write('ws_a','ses_a',Array.from({length:9},(_,i)=>({key:`d${i}`,name:'source'})))).toThrow('8')
    drafts.write('ws_a','ses_a',[]);expect(drafts.read('ws_a','ses_a')).toEqual([])
  })
  it('uses a narrow binary endpoint without JSON encoding files or exposing auth in URLs',async()=>{
    const calls:{url:string;body:BodyInit|null|undefined;headers:HeadersInit|undefined}[]=[]
    const client=new ApiClient({baseUrl:'',token:'test-token',fetchImpl:async(url,init)=>{calls.push({url:String(url),body:init?.body,headers:init?.headers});return new Response('{}')}})
    const file=new File(['source'],'example.py',{type:'text/plain'})
    await client.uploadAttachment('ws_1','ses_1',{attachment_id:'att_1',revision:2} as AttachmentWire,file,'text/plain')
    expect(calls[0].body).toBe(file);expect(calls[0].url).toContain('/attachments/att_1/content?session_id=ses_1&revision=2')
    expect(calls[0].url).not.toContain('test-token')
    expect(new Headers(calls[0].headers).get('content-type')).toBe('text/plain')
    expect(fileMediaType({name:'foo.webp',type:''})).toBe('image/webp')
    expect(()=>fileMediaType({name:'foo.svg',type:'image/svg+xml'})).toThrow('仅支持')
  })
})
