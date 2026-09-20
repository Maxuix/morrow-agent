import { describe, expect, it, vi } from 'vitest'
import { ChatStore, boundedItems, DraftStorage, OutboxStorage, type ChatSocket } from './chat'
import { ApiClient } from '../api/client'
import type { ChatSnapshot, StageFrame, TimelineItem } from '../api/chat'
const item = (n: number): TimelineItem => ({item_id:`i${n}`,kind:'assistant_message',workspace_id:'w',session_id:'s',order_key:[n,0,0,`i${n}`],source:{},revision:1,content:'hello',content_ref:null})
const snapshot = (sequence=0): ChatSnapshot => ({stream_epoch:'e',sequence,draft:null,timeline:{items:[item(1)],next_cursor:'old',has_more:true,bytes:0},queue:{revision:1,paused:false,active_agent_run_id:null,items:[]},event_cursor:0})
const stageFrame = (sequence:number, nodeRunId='nrun_1', stage='awaiting_model') => ({
  workspace_id:'w',session_id:'s',stream_epoch:'e',sequence,type:'stage',message_id:null,
  payload:{workflow_run_id:'wrun_1',node_run_id:nodeRunId,node_id:'gamma',stage,ts:'2026-01-01T00:00:00Z'},
})
function fixture() {
  const client = {chatSnapshot:vi.fn().mockResolvedValue(snapshot()),chatHistory:vi.fn(),chatSocketUrl:()=>'/stream'}
  const socket: ChatSocket = {onopen:null,onmessage:null,onclose:null,send:vi.fn(),close:vi.fn()}
  const store = new ChatStore(client as unknown as ApiClient,'w','s',()=>socket)
  return {client,socket,store}
}
const tick = async () => {for(let i=0;i<10;i++)await Promise.resolve()}
describe('Chat synchronization',()=>{
  it('subscribes at snapshot watermark and deduplicates scoped deltas and durable replacement',async()=>{
    const {store,socket,client}=fixture();await store.start();socket.onopen?.()
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({type:'subscribe',stream_epoch:'e',after_sequence:0}))
    const frame={workspace_id:'w',session_id:'s',stream_epoch:'e',sequence:1,type:'reply_delta',message_id:'i2',payload:{text:'visible'}}
    socket.onmessage?.({data:JSON.stringify({...frame,session_id:'other'})});expect(store.getState().snapshot?.draft).toBeNull()
    socket.onmessage?.({data:JSON.stringify(frame)});socket.onmessage?.({data:JSON.stringify(frame)})
    expect(store.getState().snapshot?.draft?.text).toBe('visible')
    client.chatSnapshot.mockResolvedValue({...snapshot(2),timeline:{...snapshot().timeline,items:[item(1),item(2)]}})
    socket.onmessage?.({data:JSON.stringify({...frame,sequence:2,type:'reply_committed'})});await tick()
    expect(store.getState().items.map(i=>i.item_id)).toEqual(['i1','i2']);expect(store.getState().snapshot?.draft).toBeNull();store.stop()
  })
  it('drops late HTTP responses and releases listeners on a scope leaving',async()=>{
    const {store,client,socket}=fixture();let resolve!: (value:ChatSnapshot)=>void
    client.chatSnapshot.mockImplementation(()=>new Promise(r=>{resolve=r}))
    const start=store.start();store.stop();resolve(snapshot());await start
    expect(store.getState().snapshot).toBeNull();expect(socket.send).not.toHaveBeenCalled()
  })
  it('resyncs sequence gaps without replaying input',async()=>{
    vi.useFakeTimers();const {store,socket}=fixture();await store.start()
    socket.onmessage?.({data:JSON.stringify({workspace_id:'w',session_id:'s',stream_epoch:'e',sequence:9,type:'reply_delta'})})
    expect(store.getState().connection).toBe('reconnecting');expect(socket.close).toHaveBeenCalled();store.stop();vi.useRealTimers()
  })
  it('does not rewind deltas when a refresh response races streaming',async()=>{
    const {store,client,socket}=fixture();await store.start();let resolve!: (v:ChatSnapshot)=>void
    client.chatSnapshot.mockImplementation(()=>new Promise(r=>{resolve=r}));const pending=store.refresh()
    socket.onmessage?.({data:JSON.stringify({workspace_id:'w',session_id:'s',stream_epoch:'e',sequence:1,type:'reply_delta',message_id:'i2',payload:{text:'latest'}})})
    resolve(snapshot());await pending;expect(store.getState().snapshot?.draft?.text).toBe('latest');store.stop()
  })
  it('pages before cursor and bounds memory while retaining stable IDs',async()=>{
    const {store,client}=fixture();await store.start();client.chatHistory.mockResolvedValue({items:[item(0),item(1)],next_cursor:null})
    await store.older();expect(store.getState().items.map(i=>i.item_id)).toEqual(['i0','i1']);expect(store.getState().cursor).toBeNull();store.stop()
    const long=Array.from({length:10000},(_,i)=>item(i));expect(boundedItems(long)).toHaveLength(500);expect(boundedItems(long,true)[0].item_id).toBe('i0')
    expect(boundedItems(long.map(i=>({...i,content:'x'.repeat(20000)})))).toHaveLength(207)
  })
  it('updates node stages locally from stage frames without pulling history',async()=>{
    const {store,client,socket}=fixture();await store.start();socket.onopen?.()
    socket.onmessage?.({data:JSON.stringify(stageFrame(1))})
    socket.onmessage?.({data:JSON.stringify(stageFrame(2,'nrun_1','tool_preparing'))})
    socket.onmessage?.({data:JSON.stringify(stageFrame(3,'nrun_2','awaiting_model'))})
    expect(store.getState().snapshot?.stages?.map(s=>[s.node_run_id,s.stage])).toEqual([['nrun_1','tool_preparing'],['nrun_2','awaiting_model']])
    expect(client.chatSnapshot).toHaveBeenCalledTimes(1);store.stop()
  })
  it('protects streamed stages and draft from an older snapshot response',async()=>{
    const {store,client,socket}=fixture();await store.start()
    socket.onmessage?.({data:JSON.stringify(stageFrame(1,'nrun_1','tool_preparing'))})
    client.chatSnapshot.mockResolvedValue(snapshot())
    await store.refresh()
    expect(store.getState().snapshot?.sequence).toBe(1)
    expect(store.getState().snapshot?.stages?.[0]?.stage).toBe('tool_preparing')
    store.stop()
  })
  it('reconciles stages from the server snapshot on reconnect',async()=>{
    const {store,client}=fixture()
    const stages: StageFrame[] = [{workflow_run_id:'wrun_1',node_run_id:'nrun_9',node_id:'alpha',stage:'awaiting_model',ts:null}]
    client.chatSnapshot.mockResolvedValue({...snapshot(7),stages})
    await store.start()
    expect(store.getState().snapshot?.stages).toEqual(stages);store.stop()
  })
})
function storage() {const values=new Map<string,string>();return {getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value)},removeItem:(key:string)=>{values.delete(key)}}}
describe('draft and outbox retention',()=>{
  it('keeps 20 scoped drafts, refuses silent eviction and offers explicit clearing',()=>{
    const drafts=new DraftStorage(storage());for(let i=0;i<20;i++)drafts.write('w',`s${i}`,'中文草稿')
    expect(()=>drafts.write('other','s0','new')).toThrow('20');expect(drafts.read('w','s0')).toBe('中文草稿')
    drafts.remove(drafts.key('w','s0'));drafts.write('other','s0','new');expect(drafts.read('other','s0')).toBe('new')
    expect(()=>drafts.write('other','s0','x'.repeat(4097))).toThrow('4096')
  })
  it('retains immutable id and intent through reload until explicitly reconciled',()=>{
    const memory=storage();const outbox=new OutboxStorage(memory);const input={client_message_id:'same',text:'hello',intent:'follow_up' as const,target_agent_run_id:'r'}
    outbox.write('w','a',input);expect(new OutboxStorage(memory).read('w','a')).toEqual(input);expect(outbox.read('w','b')).toBeNull()
    outbox.write('w','a',null);expect(outbox.read('w','a')).toBeNull()
  })
})

describe('history recovery boundaries',()=>{
  it('keeps trimmed live history reachable after the oldest cursor was exhausted',async()=>{
    const {store,client}=fixture()
    client.chatSnapshot.mockResolvedValue({...snapshot(),timeline:{items:[item(1)],next_cursor:null,has_more:false,bytes:0}})
    await store.start()
    for(let n=2;n<=501;n++) {
      client.chatSnapshot.mockResolvedValue({...snapshot(n),timeline:{items:[item(n)],next_cursor:`before${n}`,has_more:true,bytes:0}})
      await store.refresh()
    }
    expect(store.getState().items).toHaveLength(500)
    expect(store.getState().trimmed).toBe(true)
    expect(store.getState().cursor).not.toBeNull()
    client.chatHistory.mockResolvedValue({items:[item(1)],next_cursor:null})
    await store.older()
    expect(store.getState().items[0].item_id).toBe('i1')
    store.stop()
  })
  it('restarts from a contiguous snapshot after more than one page arrived offline',async()=>{
    const {store,client}=fixture();await store.start()
    client.chatSnapshot.mockResolvedValue({...snapshot(120),timeline:{items:Array.from({length:50},(_,i)=>item(i+71)),next_cursor:'before71',has_more:true,bytes:0}})
    await store.start()
    expect(store.getState().items[0].item_id).toBe('i71')
    expect(store.getState().cursor).toBe('before71')
    client.chatHistory.mockResolvedValue({items:Array.from({length:50},(_,i)=>item(i+21)),next_cursor:'before21'})
    await store.older()
    expect(client.chatHistory).toHaveBeenCalledWith('w','s','before71')
    expect(store.getState().items).toHaveLength(100)
    store.stop()
  })
  it('releases history loading when a reconnect invalidates an in-flight page',async()=>{
    const {store,client}=fixture();await store.start()
    let resolve!: (value:unknown)=>void
    client.chatHistory.mockImplementation(()=>new Promise(r=>{resolve=r}))
    const older=store.older();expect(store.getState().loadingHistory).toBe(true)
    await store.start();resolve({items:[item(0)],next_cursor:null});await older
    expect(store.getState().loadingHistory).toBe(false)
    expect(store.getState().cursor).toBe('old')
    store.stop()
  })
})

describe('unified history retention (P10.4)',()=>{
  it('bounds mixed user/assistant/fact records by count and bytes while keeping the newest tail',()=>{
    const mixed: TimelineItem[] = Array.from({length: 600},(_,i)=>i%3===0
      ? {...item(i),kind:'user_message' as const,source:{turn_id:`t${i}`}}
      : i%3===1
        ? {...item(i),kind:'tool_activity' as const,content:{tool_name:'read',status:'succeeded'}}
        : item(i))
    const bounded = boundedItems(mixed)
    expect(bounded).toHaveLength(500)
    expect(bounded.at(-1)?.item_id).toBe('i599')
    expect(bounded.some(i=>i.kind==='tool_activity')).toBe(true)
    // Byte cap: 4 MiB ceiling holds even with heavy contents.
    const heavy = boundedItems(mixed.map(i=>({...i,content:'x'.repeat(20000)})))
    const bytesOf = (items: TimelineItem[]) => items.reduce((n,i)=>n+new TextEncoder().encode(JSON.stringify(i)).length,0)
    expect(bytesOf(heavy)).toBeLessThanOrEqual(4*1024*1024)
    expect(heavy.at(-1)?.item_id).toBe('i599')
  })
  it('keeps durable history reachable when a new epoch replaces the client window',async()=>{
    vi.useFakeTimers()
    const {store,client}=fixture();await store.start()
    // Core restart: a new stream epoch serves a fresh snapshot window.
    client.chatSnapshot.mockResolvedValue({...snapshot(5),stream_epoch:'epoch-2',timeline:{items:[item(501)],next_cursor:'before501',has_more:true,bytes:0}})
    await store.start()
    expect(store.getState().snapshot?.stream_epoch).toBe('epoch-2')
    expect(store.getState().items.map(i=>i.item_id)).toEqual(['i501'])
    // The durable past is not gone: the cursor still pages it back.
    client.chatHistory.mockResolvedValue({items:[item(500)],next_cursor:'before500'})
    await store.older()
    expect(store.getState().items.map(i=>i.item_id)).toEqual(['i500','i501'])
    store.stop();vi.useRealTimers()
  })
})
