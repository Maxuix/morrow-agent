import { THEME_LABELS, THEME_VALUES, type Theme } from '../../state/theme'

/**
 * Light / dark / follow-system picker. The parent owns persistence and the
 * documentElement preview so the rest of the shell stays in sync.
 */
export function AppearanceSection({
  theme,
  onThemeChange,
}: {
  theme: Theme
  onThemeChange: (theme: Theme) => void
}) {
  return (
    <section className="settings-page" aria-label="外观">
      <div className="settings-content">
        <header className="settings-heading">
          <span className="settings-eyebrow">设置 / 外观</span>
          <h2>外观</h2>

        </header>
        <div className="theme-choices appearance-theme-choices" role="group" aria-label="外观主题">
          {THEME_VALUES.map(value => (
            <button
              key={value}
              type="button"
              aria-pressed={theme === value}
              className={theme === value ? 'is-active' : undefined}
              onClick={() => onThemeChange(value)}
            >
              {THEME_LABELS[value]}
            </button>
          ))}
        </div>

      </div>
    </section>
  )
}
