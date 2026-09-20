import { useState } from 'react'
import type { ApiClient } from '../api/client'

/**
 * One declared Workflow output, as the durable bytes the server stored.
 *
 * The server returns a bounded, inert copy of the Artifact. TextResult
 * contracts used to be re-interpreted here by guessing JSON fields; the
 * readable answer now comes from the TaskOutcome result projection, so this
 * detail view never parses model or contract payloads on the client.
 */
export function WorkflowOutput({client,runId,artifactId,label}:{client:ApiClient;runId:string;artifactId:string;label:string}){
  const [preview,setPreview]=useState<{content:string;truncated:boolean}|null>(null)
  const [error,setError]=useState('');const [busy,setBusy]=useState(false)
  const open=async()=>{setBusy(true);setError('');try{setPreview(await client.workflowOutput(runId,artifactId))}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  return <div className="my-2 text-xs"><button className="editor-button" disabled={busy} onClick={()=>preview?setPreview(null):void open()}>{preview?'关闭输出':'查看输出'} · {label}</button>{preview&&<><pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words p-2" data-workflow-output="raw">{preview.content}</pre>{preview.truncated&&<p>预览为前 64 KiB。</p>}</>}{error&&<p role="alert">{error}</p>}</div>
}
