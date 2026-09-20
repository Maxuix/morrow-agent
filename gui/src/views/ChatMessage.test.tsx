import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { ChatMessage, Markdown, safeLink } from './ChatMessage'
import { shouldSend } from './ChatComposer'
it('renders safe Markdown code and treats active source as escaped text',()=>{
  const html=renderToStaticMarkup(<Markdown text={'# Title\n**bold** and `code`\n- one\n```html\n<script>alert(1)</script>\n```\n[unsafe](javascript:alert)\n<img src=x onerror=alert(1)>\n[safe](https://example.com)'}/>)
  expect(html).toContain('aria-level="1"');expect(html).toContain('<strong>bold</strong>');expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');expect(html).not.toContain('<img ');expect(html).not.toContain('href="javascript:');expect(html).toContain('rel="noopener noreferrer"')
  for(const url of ['javascript:alert(1)','data:text/html,x','//example.com','file:///etc/passwd','https://a\n.com'])expect(safeLink(url)).toBeNull()
})
it('protects IME composition, legacy 229 and Shift+Enter',()=>{
  const event={key:'Enter',shiftKey:false,isComposing:false}
  expect(shouldSend(event,false)).toBe(true);expect(shouldSend(event,true)).toBe(false)
  expect(shouldSend({...event,isComposing:true},false)).toBe(false);expect(shouldSend({...event,keyCode:229},false)).toBe(false);expect(shouldSend({...event,shiftKey:true},false)).toBe(false)
})
it('renders the history attachment entry from top-level references',()=>{
  const ref = {version:1 as const,attachment_id:'att_hist1',content_digest:'a'.repeat(64),representation_id:'art_hist1',representation_digest:'b'.repeat(64)}
  const timelineItem = {item_id:'i9',kind:'user_message',workspace_id:'w',session_id:'s',order_key:[9,0,0,'i9'] as [number,number,number,string],revision:1,source:{origin_session_id:'s',record_id:'rec_1'},content:'带附件',content_ref:null,attachments:[ref]}
  const html = renderToStaticMarkup(<ChatMessage item={timelineItem} client={{} as never} onTask={()=>{}} expanded={false} onExpand={()=>{}}/>)
  expect(html).toContain('查看附件 1')
  expect(html).not.toContain('attachments&quot;:[{')
})
it('renders no attachment entry without top-level references',()=>{
  const timelineItem = {item_id:'i10',kind:'user_message',workspace_id:'w',session_id:'s',order_key:[10,0,0,'i10'] as [number,number,number,string],revision:1,source:{origin_session_id:'s',record_id:'rec_2'},content:'无附件',content_ref:null}
  const html = renderToStaticMarkup(<ChatMessage item={timelineItem} client={{} as never} onTask={()=>{}} expanded={false} onExpand={()=>{}}/>)
  expect(html).not.toContain('查看附件')
})
it('renders user message bubble without "你" remark header',()=>{
  const timelineItem = {item_id:'u1',kind:'user_message',workspace_id:'w',session_id:'s',order_key:[1,0,0,'u1'] as [number,number,number,string],revision:1,source:{origin_session_id:'s',record_id:'rec_1'},content:'用户消息内容',content_ref:null}
  const html = renderToStaticMarkup(<ChatMessage item={timelineItem} client={{} as never} onTask={()=>{}} expanded={false} onExpand={()=>{}}/>)
  expect(html).toContain('用户消息内容')
  expect(html).not.toContain('你')
  expect(html).not.toContain('message-meta')
})
it('renders assistant message with "Morrow" meta header',()=>{
  const timelineItem = {item_id:'a1',kind:'assistant_message',workspace_id:'w',session_id:'s',order_key:[2,0,0,'a1'] as [number,number,number,string],revision:1,source:{origin_session_id:'s',record_id:'rec_2'},content:'助手回复',content_ref:null}
  const html = renderToStaticMarkup(<ChatMessage item={timelineItem} client={{} as never} onTask={()=>{}} expanded={false} onExpand={()=>{}}/>)
  expect(html).toContain('助手回复')
  expect(html).toContain('<strong>Morrow</strong>')
  expect(html).toContain('message-meta')
})
it('turns a local Markdown link into a panel action, never a navigation',()=>{
  const opened: unknown[] = []
  const html=renderToStaticMarkup(<Markdown text={'看 [说明](docs/我的%20文件.md:12) 与 [站点](https://example.com/x) 与 [脚本](javascript:alert(1))'} onOpenFile={target=>opened.push(target)}/>)
  expect(html).toContain('chat-file-link')
  expect(html).toContain('href="https://example.com/x"')
  expect(html).not.toContain('href="docs/')
  expect(html).not.toContain('href="javascript:')
  expect(html).toContain('脚本')
})
it('keeps a local link inert when no file opener is wired',()=>{
  const html=renderToStaticMarkup(<Markdown text={'[a](docs/a.md)'}/>)
  expect(html).not.toContain('chat-file-link')
  expect(html).toContain('>a</code>')
})
it('supports GFM tables, task lists and strikethrough through the shared renderer',()=>{
  const html=renderToStaticMarkup(<Markdown text={'| 名称 | 状态 |\n| --- | --- |\n| 预览 | 通过 |\n\n- [x] 已登记\n- [ ] 待登记\n\n~~旧结论~~'} onOpenFile={()=>{}}/>)
  expect(html).toContain('<table>')
  expect(html).toContain('<th>名称</th>')
  expect(html).toContain('<td>通过</td>')
  expect(html).toContain('type="checkbox"')
  expect(html).toContain('<del>旧结论</del>')
})
it('never auto-loads a remote image',()=>{
  const html=renderToStaticMarkup(<Markdown text={'![截图](https://example.com/a.png)'}/>)
  expect(html).not.toContain('<img ')
  expect(html).toContain('远程图片未自动加载')
})
