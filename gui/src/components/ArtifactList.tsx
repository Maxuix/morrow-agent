import type { ArtifactWire } from '../api/types'
import { formatTimestamp, shortId } from '../views/lib/labels'
import { EmptyState } from './EmptyState'

const ARTIFACT_STATE_LABELS: Record<ArtifactWire['state'], string> = {
  staging: '暂存中',
  available: '可用',
  missing: '缺失',
  corrupt: '损坏',
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

/**
 * Artifact list shared by the Task workspace and the node detail block:
 * kind / output_slot / state / byte size, with the excerpt in the serif
 * reading font (design decision: 衬线用于 Artifact 内容).
 */
export function ArtifactList({
  artifacts,
  emptyTitle = '暂无 Artifact',
}: {
  artifacts: ArtifactWire[]
  emptyTitle?: string
}) {
  if (artifacts.length === 0) {
    return <EmptyState title={emptyTitle} />
  }
  return (
    <ul className="flex flex-col gap-3">
      {artifacts.map((artifact) => (
        <li
          key={artifact.artifact_id}
          className="rounded-[10px] border border-subtle bg-raised p-4"
        >
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="text-sm font-medium">{artifact.kind}</span>
            {artifact.output_slot !== null && (
              <span className="font-mono text-xs text-accent">{artifact.output_slot}</span>
            )}
            <span className="text-xs text-secondary">
              {ARTIFACT_STATE_LABELS[artifact.state]}
            </span>
            <span className="font-mono text-xs text-secondary">
              {formatBytes(artifact.byte_size)}
            </span>
            <span className="ml-auto font-mono text-xs text-secondary">
              {shortId(artifact.artifact_id)} · {formatTimestamp(artifact.created_at)}
            </span>
          </div>
          {artifact.excerpt !== '' && (
            <p className="mt-2 font-serif text-sm leading-relaxed whitespace-pre-wrap text-primary">
              {artifact.excerpt}
            </p>
          )}
        </li>
      ))}
    </ul>
  )
}
