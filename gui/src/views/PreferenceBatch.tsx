import type { Preference, Scope } from '../api/management'
import type { Mutate } from './management/types'
import { BATCH_LIMIT, BatchPanel } from './preferences/BatchPanel'

/**
 * Ordered same-scope batch (at most 8 items, one document revision). The page
 * renders this component; the wrapper keeps the established entry name.
 */
export function PreferenceBatch({ scope, revision, entries, mutate }: {
  scope: Scope
  revision: number
  entries: Preference[]
  mutate: Mutate
}) {
  return (
    <div className="pp-batch-wrap">
      <p className="pp-field-title">批量修改偏好（最多 {BATCH_LIMIT} 项）</p>
      <BatchPanel
        scope={scope}
        revision={revision}
        entries={entries}
        submit={(operations, expectedRevision) =>
          mutate(
            'preferences',
            { arguments: { scope, expected_revision: expectedRevision, operations } },
            `batch:${scope}`,
          )
        }
      />
    </div>
  )
}
