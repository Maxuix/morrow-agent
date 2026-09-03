import { useEffect, useState } from 'react'

type Theme = 'light' | 'dark' | 'system'

const STATUS_LEGEND = [
  { name: 'running', dotClass: 'bg-running' },
  { name: 'completed', dotClass: 'bg-completed' },
  { name: 'failed', dotClass: 'bg-failed' },
  { name: 'blocked', dotClass: 'bg-blocked' },
  { name: 'paused', dotClass: 'bg-paused' },
  // queued is outline-only per the design language
  { name: 'queued', dotClass: 'border-2 border-queued' },
] as const

/**
 * Placeholder shell for the Stage 8 GUI. Demonstrates the Warm Paper token
 * system (theme toggle, font roles, status legend). Real views slot in under
 * `src/views/` in later subplans.
 */
export default function App() {
  const [theme, setTheme] = useState<Theme>('system')

  useEffect(() => {
    if (theme === 'system') {
      delete document.documentElement.dataset.theme
    } else {
      document.documentElement.dataset.theme = theme
    }
  }, [theme])

  const nextTheme: Record<Theme, Theme> = {
    system: 'light',
    light: 'dark',
    dark: 'system',
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-2xl flex-col gap-8 px-6 py-12">
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-semibold">Morrow</h1>
          <p className="mt-1 text-sm text-secondary">
            Warm Paper design tokens — scaffold placeholder
          </p>
        </div>
        <button
          type="button"
          onClick={() => setTheme(nextTheme[theme])}
          className="rounded-[10px] border border-subtle bg-raised px-3 py-1.5 text-sm text-primary transition-colors duration-150 hover:border-accent"
        >
          Theme: {theme}
        </button>
      </header>

      <section className="rounded-[10px] border border-subtle bg-raised p-5">
        <h2 className="font-serif text-xl">Font roles</h2>
        <p className="mt-2 font-serif text-base leading-relaxed">
          Serif (Newsreader) is for reading material: chat messages, artifact
          content, and page titles like this one.
        </p>
        <p className="mt-2 text-sm text-secondary">
          Sans (Inter) is for all UI chrome: buttons, labels, navigation, and
          status bars.
        </p>
        <p className="mt-2 font-mono text-sm text-accent">
          $ morrow run ./workflow.toml --budget 120000
        </p>
      </section>

      <section className="rounded-[10px] border border-subtle bg-raised p-5">
        <h2 className="font-serif text-xl">Run status legend</h2>
        <ul className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          {STATUS_LEGEND.map(({ name, dotClass }) => (
            <li key={name} className="flex items-center gap-2 text-sm">
              <span
                className={`inline-block size-2.5 rounded-full ${dotClass}`}
              />
              <span className="text-secondary">{name}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
