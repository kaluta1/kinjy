import { useEffect, useRef, useState } from 'react'
import { Check, Cloudy, Monitor, Sun, type LucideIcon } from 'lucide-react'
import { useAppTheme, type DisplayMode } from '@/components/appdemo/theme'
import { cn } from '@/lib/utils'

// Cloud is this platform's dark page, so it is offered as "Dark" here.
const OPTIONS: Array<{ id: DisplayMode; label: string; icon: LucideIcon }> = [
  { id: 'light', label: 'Light', icon: Sun },
  { id: 'cloud', label: 'Dark', icon: Cloudy },
  { id: 'system', label: 'System', icon: Monitor },
]

/**
 * Light, Dark or System, for the public header.
 *
 * The same setting as the app's own switcher (AppChrome) and the landing
 * page's toggle - `kaluta.display_mode`, kept in this browser - so it works
 * signed out and carries into the app after signing in.
 */
export default function ThemeMenu() {
  const { mode, setMode } = useAppTheme()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const items = useRef<Array<HTMLButtonElement | null>>([])
  const current = OPTIONS.find((o) => o.id === mode) ?? OPTIONS[2]

  useEffect(() => {
    if (!open) return
    // Focus the chosen option, as a menu does when it opens.
    items.current[OPTIONS.findIndex((o) => o.id === current.id)]?.focus()
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open, current.id])

  const close = () => {
    setOpen(false)
    button.current?.focus()
  }

  const onKey = (e: React.KeyboardEvent, index: number) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const next = (index + (e.key === 'ArrowDown' ? 1 : OPTIONS.length - 1)) % OPTIONS.length
      items.current[next]?.focus()
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  return (
    <div ref={wrap} className="relative">
      <button
        ref={button}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Theme: ${current.label}. Change theme`}
        title={`Theme: ${current.label}`}
        className="flex h-10 w-10 items-center justify-center rounded-full cloud-glass text-text-mid hover:text-gold-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gold"
      >
        <current.icon size={17} aria-hidden="true" />
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Theme"
          className="absolute end-0 top-12 z-50 w-40 rounded-card-md cloud-glass bg-ink-2/95 p-2 shadow-cloud"
        >
          {OPTIONS.map((o, index) => {
            const active = o.id === mode
            return (
              <button
                key={o.id}
                ref={(el) => {
                  items.current[index] = el
                }}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => {
                  setMode(o.id)
                  close()
                }}
                onKeyDown={(e) => onKey(e, index)}
                className={cn(
                  'flex w-full items-center gap-2.5 rounded-card-sm px-3 py-2 text-start text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-gold',
                  active ? 'bg-gold/10 text-gold-soft' : 'text-text-mid hover:bg-white/5 hover:text-text-hi',
                )}
              >
                <o.icon size={15} aria-hidden="true" />
                <span className="flex-1">{o.label}</span>
                {active && <Check size={14} aria-hidden="true" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
