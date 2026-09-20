export interface AttachmentRef {
  version: 1; attachment_id: string; content_digest: string
  representation_id: string; representation_digest: string
}
export interface AttachmentPart {artifact_id: string; digest: string; media_type: string; page: number | null; chars: number}
export interface AttachmentWire {
  attachment_id: string; session_id: string; name: string; media_type: string; byte_size: number
  state: 'reserved'|'uploading'|'processing'|'ready'|'submitted'|'failed'|'released'
  revision: number; reason: string | null; reference: AttachmentRef | null
  representation?: {name: string; source_path?:string|null; reading: string; parts: AttachmentPart[]; previews: AttachmentPart[]; pages: number; omitted_chars: number}
}
export interface AttachmentLimits {file_bytes: number; message_files: number; text_chars: number; pdf_pages: number; media_types: string[]}
export interface FileSearch {files: {path: string; name: string; byte_size: number}[]; truncated: boolean; scanned: number; limit: number}
export const attachmentPath = (workspace: string, id?: string) => `/v1/workspaces/${encodeURIComponent(workspace)}/attachments${id ? `/${encodeURIComponent(id)}` : ''}`
export function fileMediaType(file: Pick<File,'type'|'name'>): string {
  if (['image/png','image/jpeg','image/webp','application/pdf'].includes(file.type)) return file.type
  if (file.type.startsWith('image/') || file.type.startsWith('audio/') || file.type.startsWith('video/')) throw new Error('仅支持文本/源码、PNG、JPEG、WebP 和 PDF。')
  if (/\.(png|jpe?g|webp|pdf)$/i.test(file.name)) return ({png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',webp:'image/webp',pdf:'application/pdf'} as Record<string,string>)[file.name.split('.').pop()!.toLowerCase()]
  return 'text/plain'
}
