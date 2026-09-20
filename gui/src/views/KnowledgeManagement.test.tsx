import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiClient } from '../api/client'
import { SkillOperations } from './SkillOperations'
import { PreferenceBatch } from './PreferenceBatch'
import { learningRowIdentity } from './LearningOperations'
import { knowledgeCommands } from './knowledgeCommands'

describe('knowledge workbench transport and entry points',()=>{
  it('preserves scope, page, exact revision and stable mutation payloads',async()=>{
    const calls:{url:string;auth:string|null;body:unknown}[]=[]
    const client=new ApiClient({baseUrl:'',token:'fixture',fetchImpl:async(url,init)=>{calls.push({url:String(url),auth:new Headers(init?.headers).get('authorization'),body:init?.body?JSON.parse(String(init.body)):null});return new Response('{}',{status:200})}})
    await client.knowledgeQuery('knowledge',{identity:'knw_a',revision:502,include_deleted:true,cursor:'50'})
    expect(calls[0].url).toBe('/v1/knowledge-management/knowledge?identity=knw_a&revision=502&include_deleted=true&cursor=50')
    await client.skillQuery('usage',{identity:'skill_a',version_id:'skv_exact',page:2})
    expect(calls[1].url).toContain('version_id=skv_exact&page=2')
    const command={action:'install',scope:'global',command_id:'cmd_fixed',expected_tree_digest:'a'.repeat(64),path:'/tmp/fixture',confirmed:true}
    await client.skillAction(command);await client.skillAction(command)
    expect(calls[2]).toEqual(calls[3]);expect(calls[2].auth).toBe('Bearer fixture')
  })
  it('exposes preview-confirm installation and ordered preference batching as forms',()=>{
    const client=new ApiClient({baseUrl:'',token:'fixture'})
    const html=renderToStaticMarkup(<SkillOperations client={client} scope="global" mutate={async()=>true} onChanged={()=>{}}/>)
    expect(html).toContain('校验目录与预览');expect(html).not.toContain('生成包（仅校验');expect(html).not.toContain('按 ID 检查')
    expect(html).not.toContain('确认安装此版本')
    const batch=renderToStaticMarkup(<PreferenceBatch scope="workspace" revision={4} entries={[]} mutate={async()=>true}/>)
    expect(batch).toContain('批量');expect(batch).toContain('add')
  })
  it('keeps activation identity separate from its originating promotion',()=>{
    expect(learningRowIdentity('activations',{activation_id:'act_current',operation_id:'pop_source',status:'active',row_version:1})).toBe('act_current')
  })
  it('recognizes current commands and deep commands without shell dispatch',()=>{
    expect(knowledgeCommands['/preferences replace'].label).toContain('行为偏好')
    expect(knowledgeCommands['/preference replace']).toBeUndefined()
    expect(knowledgeCommands['/learn promotions abort'].label).toContain('推广')
    expect(knowledgeCommands['/workspace reset'].label).toContain('项目画像')
    expect(knowledgeCommands['/memory selection show'].label).toContain('知识库')
    expect(knowledgeCommands['/config']).toBeUndefined()
    expect(knowledgeCommands['/shell']).toBeUndefined()
  })
})
