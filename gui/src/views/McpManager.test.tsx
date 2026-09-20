import { renderToStaticMarkup } from 'react-dom/server'
import { expect,it } from 'vitest'
import { ApiClient } from '../api/client'
import { McpEditor } from './McpManager'
import { knowledgeCommands } from './knowledgeCommands'

it('keeps MCP configuration separate from launching, enabling and write-only credentials',()=>{
  const html=renderToStaticMarkup(<McpEditor scope="workspace" base={{scope:'workspace',revision:3,digest:'a'.repeat(64)}} initial={null} busy={false} onSave={async()=>true} onClose={()=>{}}/>)
  expect(html).toContain('保存为停用配置')
  expect(html).toContain('stdio')
  expect(html).toContain('MCP凭据变量名')
  expect(html).not.toContain('type="password"')
  expect(html).not.toContain('value="http"')
  expect(knowledgeCommands['/mcp'].label).toContain('MCP')
})
it('sends secrets only to the dedicated authenticated endpoint',async()=>{
  const calls:{url:string;body:unknown;auth:string|null}[]=[]
  const client=new ApiClient({baseUrl:'',token:'fixture',fetchImpl:async(url,init)=>{calls.push({url:String(url),body:init?.body?JSON.parse(String(init.body)):null,auth:new Headers(init?.headers).get('authorization')});return new Response('{}',{status:200})}})
  await client.mcpQuery({scope:'global',identity:'mcp_one',page:1})
  await client.mcpCredential({scope:'workspace',server_id:'mcp_one',environment_name:'SERVICE_TOKEN',expected_server_revision:3,secret:'synthetic-test-value'})
  expect(calls[0]).toMatchObject({url:'/v1/mcp-management?scope=global&identity=mcp_one&page=1',body:null})
  expect(calls[1]).toMatchObject({url:'/v1/mcp-credentials',auth:'Bearer fixture'})
  expect(calls[1].url).not.toContain('synthetic')
})
