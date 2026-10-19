import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { useTranslation } from 'react-i18next'
import {
  ChevronDown, Clapperboard, Cloudy, Globe, Home, LayoutGrid,
  Monitor, Pin, Search, Sun,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { MODULE_ICONS } from '@/components/appdemo/Chrome'
import { useAppTheme } from '@/components/appdemo/theme'
import type { AppLang, ChromeKey, DisplayMode } from '@/components/appdemo/theme'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useAuth } from '@/hooks/useAuth'
import { useMyProfile } from '@/hooks/useMyProfile'
import { kaluta } from '@/lib/api'
import NotificationBell from './NotificationBell'
import UnreadBadge from './UnreadBadge'
import { useUnreadMessages } from '@/hooks/useUnreadMessages'
import { cn } from '@/lib/utils'
import { EXTRA_NAV, NAV_DESTINATIONS, ROUTE_FOR, activeKeyFor } from './navigation'

const LANG_LABELS: Record<AppLang, string> = {
  en: 'English',
  sw: 'Kiswahili',
  fr: 'Français',
  ar: 'العربية',
  zh: '中文',
}
const MODE_ICONS: Record<DisplayMode, LucideIcon> = {
  cloud: Cloudy,
  light: Sun,
  system: Monitor,
}
const MODE_LABELS: Record<DisplayMode, string> = {
  cloud: 'Cloud',
  light: 'Light',
  system: 'System',
}

/**
 * The live top bar — the designed chrome from /app, wired to real routing,
 * the real member and the real preferences endpoint.
 *
 * The display-mode switcher is the platform's signature (blueprint §16), so it
 * belongs here rather than in a settings page: Cloud is the default identity and
 * the member should be able to leave it in one click.
 */
export function AppTopBar() {
  const { t, tok, lang, setLang, mode, setMode, rtl } = useAppTheme()
  const { user } = useAuth()
  const { i18n } = useTranslation()
  const navigate = useNavigate()
  const [langOpen, setLangOpen] = useState(false)
  const [query, setQuery] = useState('')

  // The app language and the site language are one setting, not two.
  useEffect(() => {
    if (i18n.language.slice(0, 2) !== lang) void i18n.changeLanguage(lang)
    document.documentElement.dir = rtl ? 'rtl' : 'ltr'
    document.documentElement.lang = lang
  }, [lang, rtl, i18n])

  // Persist the choice server-side too, so it follows the member to another device.
  useEffect(() => {
    if (!user) return
    const timer = setTimeout(() => {
      void kaluta.account.setPreferences({ display_mode: mode }).catch(() => undefined)
    }, 600)
    return () => clearTimeout(timer)
  }, [mode, user])

  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault()
    if (query.trim().length < 2) return
    navigate(`/explore?q=${encodeURIComponent(query.trim())}`)
  }

  const me = useMyProfile()

  return (
    // relative z-20: the notifications and language menus open from this bar
    // and hang over the modules bar below it. Both bars blur what is behind
    // them, which gives each its own layer, and the second one in the page
    // was painted over the first one's menus - everywhere but Messages, which
    // has no modules bar.
    <div className={cn('relative z-20 border-x-0 border-t-0 border-b', tok.card)}>
    <div className="mx-auto flex h-[64px] w-full max-w-[1320px] items-center gap-3 px-4 md:h-[72px] md:gap-6 md:px-6">
      {/* Home, which inside the app means the feed. The logo used to leave for
          the marketing site, so the one thing every app puts a member back on
          their timeline instead took them out of the app. */}
      <Link
        to="/hub"
        className="flex shrink-0 items-center gap-2"
        aria-label="Go to your feed"
      >
        <img src="/logo.svg" alt="" className="h-8 w-8" />
        <span className={cn('hidden font-display text-[22px] font-bold lg:block', tok.text)}>Kinjy</span>
      </Link>

      <form onSubmit={submitSearch} className="mx-1 min-w-0 flex-1">
        <label className={cn('relative flex items-center rounded-full px-3.5 py-2', tok.input)}>
          <Search size={15} className={cn('shrink-0', tok.low)} aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('search')}
            aria-label={t('search')}
            className={cn('min-w-0 flex-1 bg-transparent px-2.5 text-sm outline-none placeholder:opacity-70', tok.text)}
          />
        </label>
      </form>

      {/* language */}
      <div className="relative">
        <button
          type="button"
          onClick={() => setLangOpen((v) => !v)}
          aria-expanded={langOpen}
          aria-label="Change language"
          className={cn('flex h-9 w-9 items-center justify-center rounded-full', tok.hoverBg, tok.mid)}
        >
          <Globe size={16} />
        </button>
        {langOpen && (
            <ul
              className={cn('absolute end-0 top-11 z-50 w-40 rounded-card-md p-1.5 shadow-cloud', tok.cardSolid)}
            >
              {(Object.keys(LANG_LABELS) as AppLang[]).map((l) => (
                <li key={l}>
                  <button
                    type="button"
                    onClick={() => {
                      setLang(l)
                      setLangOpen(false)
                    }}
                    className={cn(
                      'w-full rounded-card-sm px-3 py-1.5 text-start text-sm',
                      lang === l ? 'bg-gold/15 text-gold-soft' : cn(tok.mid, tok.hoverBg),
                    )}
                  >
                    {LANG_LABELS[l]}
                  </button>
                </li>
              ))}
            </ul>
          )}
      </div>

      {/* display mode — the signature control */}
      <div
        className={cn('hidden items-center gap-0.5 rounded-full p-[3px] md:flex', tok.input)}
        role="radiogroup"
        aria-label="Display mode"
      >
        {(Object.keys(MODE_ICONS) as DisplayMode[]).map((m) => {
          const Icon = MODE_ICONS[m]
          const active = mode === m
          return (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={active}
              title={MODE_LABELS[m]}
              onClick={() => setMode(m)}
              className={cn(
                'flex h-7 w-7 items-center justify-center rounded-full',
                active ? 'bg-gold/20 text-gold-soft' : cn(tok.low, tok.hoverBg),
              )}
            >
              <Icon size={13} />
            </button>
          )
        })}
      </div>

      {/* On a phone the three buttons do not fit: one button, showing the
          current mode, that moves to the next. Without it a member on a phone
          could only change the display mode before signing in. */}
      {(() => {
        const order = Object.keys(MODE_ICONS) as DisplayMode[]
        const next = order[(order.indexOf(mode) + 1) % order.length]
        const Icon = MODE_ICONS[mode]
        return (
          <button
            type="button"
            onClick={() => setMode(next)}
            aria-label={`Display mode: ${MODE_LABELS[mode]}. Switch to ${MODE_LABELS[next]}`}
            title={`${MODE_LABELS[mode]} - tap for ${MODE_LABELS[next]}`}
            className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-full md:hidden', tok.input, tok.mid)}
          >
            <Icon size={16} aria-hidden="true" />
          </button>
        )
      })()}

      <NotificationBell userId={user?.id} />

      <Link to="/dashboard" aria-label="Your profile" className="shrink-0 rounded-full">
        <MemberAvatar displayName={user?.display_name} avatarUrl={me?.avatar_url} size={36} ring />
      </Link>
    </div>
    </div>
  )
}

const PIN_KEY = 'kaluta.pinned_modules'

/** What sits on the bar for a new member: the everyday five. */
const DEFAULT_PINS = ['home', 'create', 'messages', 'familyTree']

/**
 * The universal chip bar.
 *
 * Fifteen modules laid out at once read as a wall, not a menu — so the bar shows
 * only what the member pinned and everything else lives one click away under
 * "All modules". That is what the blueprint's "users pin favorite modules" is
 * for: pinning decides what is on the bar, rather than merely reordering an
 * exhaustive list nobody can scan.
 */
export function AppChipBar({ pathname, search }: { pathname: string; search: string }) {
  const { t, tok } = useAppTheme()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [pinned, setPinned] = useState<string[]>(() => {
    try {
      const stored = localStorage.getItem(PIN_KEY)
      return stored ? JSON.parse(stored) : DEFAULT_PINS
    } catch {
      return DEFAULT_PINS
    }
  })

  const togglePin = (key: string) => {
    setPinned((current) => {
      const next = current.includes(key) ? current.filter((k) => k !== key) : [...current, key]
      localStorage.setItem(PIN_KEY, JSON.stringify(next))
      return next
    })
  }

  const active = activeKeyFor(pathname, search)
  const unread = useUnreadMessages()
  const unreadLabel = (key: string, label: string) =>
    key === 'messages' && unread > 0 ? `${label}, ${unread} unread` : undefined
  const all: Array<{ key: string; route: string; label: string; Icon: LucideIcon }> = [
    ...NAV_DESTINATIONS.map((key) => ({
      key,
      route: ROUTE_FOR[key],
      label: t(key as ChromeKey),
      Icon: MODULE_ICONS[key as ChromeKey] ?? Home,
    })),
    ...EXTRA_NAV.map((e) => ({ ...e, Icon: Clapperboard })),
  ]

  // The rail scrolls, and the chip for the page you are on can be past its
  // edge: on a phone the chips are 442px wide in a 267px rail, so "Messages"
  // sat half off-screen reading "Messa" while you were on Messages. Scrolled
  // into view whenever the active page changes, so the bar always shows where
  // you are.
  //
  // `nearest` rather than `center`: it moves the rail only as far as it has to,
  // so a chip already visible does not jump.
  /**
   * Bring the chip for the current page into the rail.
   *
   * The chips are wider than the rail on a phone - 442px of them in 267 - so
   * the one for the page you are on can sit past the edge: "Messages" read
   * "Messa" while you were on Messages.
   *
   * A callback ref rather than an effect, because the timing is the whole
   * problem. An effect on mount runs before the chips are laid out, and an
   * effect keyed on the route runs before the chip for that route exists; both
   * earlier attempts measured nothing and decided nothing was wrong. A
   * callback ref fires exactly when the active chip is attached, which is the
   * moment there is something to measure.
   *
   * Measured against the rail's own content box. offsetLeft is relative to the
   * nearest positioned ancestor, which here is a card further up, so it was
   * off by the card's padding - correct-looking and wrong.
   */
  // The chip for the page you are on, once it is on screen.
  const [activeChip, setActiveChip] = useState<HTMLDivElement | null>(null)

  /**
   * Keep the current page's chip inside the rail.
   *
   * The chips are wider than the rail on a phone - 442px of them in 267 - so
   * the chip for the page you are on can sit past the edge: "Messages" read
   * "Messa" while you were on Messages.
   *
   * Watched rather than done once, so the initial reflow and a rotation are
   * both covered: the rail is not its final size when the page first lays
   * out.
   *
   * Measured against the rail's own content box, because offsetLeft is
   * relative to the nearest positioned ancestor - a card further up - which
   * was off by its padding and looked right.
   */
  useEffect(() => {
    const rail = activeChip?.closest<HTMLElement>('[data-chip-rail]')
    if (!activeChip || !rail) return

    const bring = () => {
      const left =
        activeChip.getBoundingClientRect().left -
        rail.getBoundingClientRect().left +
        rail.scrollLeft
      const right = left + activeChip.offsetWidth
      // Only as far as it has to move, and only when it is actually outside:
      // a chip already in view must not jump.
      // behavior: 'auto', not 'smooth'. Smooth silently did nothing here -
      // measured ending at 0 where auto lands at 48 - which is what made this
      // look like the effect was never running. It is also the right answer on
      // its own merits: the bar should already be in the right place when the
      // page appears, not animate into it.
      if (right > rail.scrollLeft + rail.clientWidth) {
        rail.scrollTo({ left: right - rail.clientWidth + 12, behavior: 'auto' })
      } else if (left < rail.scrollLeft) {
        rail.scrollTo({ left: Math.max(0, left - 12), behavior: 'auto' })
      }
    }

    bring()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(bring)
    observer.observe(rail)
    return () => observer.disconnect()
  }, [activeChip, active])

  // The current page always has a chip, even when it is not pinned — otherwise
  // the bar would show no active state and the member loses their bearings.
  const onBar = all.filter((m) => pinned.includes(m.key) || active === m.key)

  return (
    <div className={cn('relative z-10 border-x-0 border-t-0 border-b', tok.card)}>
      <div className="mx-auto flex h-[56px] w-full max-w-[1320px] items-center gap-2 px-4 md:h-[60px] md:px-6">
        <div
          data-chip-rail=""
          className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
            {onBar.map((m) => {
              const isActive = active === m.key
              return (
                <div key={m.key} className="shrink-0" ref={isActive ? setActiveChip : undefined}>
                  <Link
                    to={m.route}
                    aria-current={isActive ? 'page' : undefined}
                    aria-label={unreadLabel(m.key, m.label)}
                    className={cn(
                      'flex h-9 items-center gap-2 whitespace-nowrap rounded-full px-4 text-[0.8rem] font-bold transition-colors',
                      isActive
                        ? 'bg-gradient-to-br from-gold-soft to-gold text-ink shadow-[0_6px_14px_-6px_rgba(166,120,57,0.5)]'
                        : cn(tok.mid, tok.hoverBg),
                    )}
                  >
                    <m.Icon size={13} aria-hidden="true" />
                    {m.label}
                    {m.key === 'messages' && <UnreadBadge count={unread} />}
                  </Link>
                </div>
              )
            })}
        </div>

        <button
          type="button"
          onClick={() => setDrawerOpen((v) => !v)}
          aria-expanded={drawerOpen}
          className={cn(
            'flex h-9 shrink-0 items-center gap-2 rounded-full border border-current/15 px-4 text-[0.8rem] font-semibold',
            drawerOpen ? 'bg-gold/15 text-gold-soft' : cn(tok.mid, tok.hoverBg),
          )}
        >
          <LayoutGrid size={13} aria-hidden="true" />
          <span className="hidden sm:inline">All modules</span>
          <ChevronDown size={12} className={cn(drawerOpen && 'rotate-180')} />
        </button>
      </div>

      {/* The full set, with the pin control where pinning is actually decided. */}
      {drawerOpen && (
          <div className="overflow-hidden">
            <div className={cn('grid gap-1 px-3 pb-3 sm:grid-cols-2 lg:grid-cols-4', tok.subtleBg)}>
              {all.map((m) => {
                const isPinned = pinned.includes(m.key)
                return (
                  <div key={m.key} className="flex items-center gap-1">
                    <Link
                      to={m.route}
                      onClick={() => setDrawerOpen(false)}
                      aria-label={unreadLabel(m.key, m.label)}
                      className={cn(
                        'flex min-w-0 flex-1 items-center gap-2 rounded-card-sm px-2.5 py-2 text-[0.8rem] font-medium',
                        active === m.key ? 'text-gold-soft' : cn(tok.mid, tok.hoverBg),
                      )}
                    >
                      <m.Icon size={14} className="shrink-0" aria-hidden="true" />
                      <span className="truncate">{m.label}</span>
                      {m.key === 'messages' && <UnreadBadge count={unread} className="ms-auto" />}
                    </Link>
                    <button
                      type="button"
                      onClick={() => togglePin(m.key)}
                      aria-pressed={isPinned}
                      aria-label={isPinned ? `Unpin ${m.label}` : `Pin ${m.label}`}
                      title={isPinned ? 'Remove from the bar' : 'Keep on the bar'}
                      className={cn(
                        'flex h-7 w-7 shrink-0 items-center justify-center rounded-full',
                        isPinned ? 'text-gold' : cn(tok.low, tok.hoverBg),
                      )}
                    >
                      <Pin size={12} fill={isPinned ? 'currentColor' : 'none'} aria-hidden="true" />
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        )}
    </div>
  )
}
