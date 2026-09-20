import { INSPECTOR_KINDS, type InspectorKind } from '../../state/inspector'
import { INSPECTOR_META } from './meta'

const CARD_KINDS: readonly InspectorKind[] = INSPECTOR_KINDS

/** 四卡片空状态：足够宽 2×2，窄面板单列（CSS grid auto-fit）。 */
export function InspectorEmptyState({ onOpen }: { onOpen: (kind: InspectorKind) => void }) {
  return (
    <div className="inspector-empty">

      <div className="inspector-empty-grid">
        {CARD_KINDS.map(kind => {
          const meta = INSPECTOR_META[kind]
          return (
            <button key={kind} type="button" className="inspector-empty-card" onClick={() => onOpen(kind)}>
              <meta.Icon />
              <strong>{meta.label}</strong>

            </button>
          )
        })}
      </div>
    </div>
  )
}
