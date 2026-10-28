import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { NavLink, Navigate, useLocation } from 'react-router'
import { Compass, Home, MessageSquare, Plus, User } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { useAuth } from '@/hooks/useAuth'
import { primeSound } from '@/lib/chime'
import { cn } from '@/lib/utils'
import { AppChipBar, AppTopBar } from './AppChrome'
import ProfileCard from './ProfileCard'
import UnreadBadge from './UnreadBadge'
import { useUnreadMessages } from '@/hooks/useUnreadMessages'
import MessageToast from './MessageToast'
import WellbeingBar from './WellbeingBar'
import { pendingLink } from '@/lib/share'

/** Mobile bottom bar — the blueprint's five, Create in the middle. */
const MOBILE_NAV = [
  { to: '/hub', label: 'Home', icon: Home },
  { to: '/explore', label: 'Explore', icon: Compass },
  { to: '/hub?compose=1', label: 'Create', icon: Plus, primary: true },
  { to: '/messages', label: 'Messages', icon: MessageSquare },
  { to: '/dashboard', label: 'Profile', icon: User },
] as const

/**
 * The signed-in shell, built from the product design in /app rather than a
 * generic dashboard layout: top bar with search and the Cloud/Light/Dark/System
 * switcher, the universal chip bar, then a three-column body.
 *
 * Everything is painted through the theme tokens (`tok`), so switching display
 * mode restyles the whole app rather than one demo frame.
 */
export default function AppShell({
  title,
  subtitle,
  action,
  aside,
  rail = true,
  full = false,
  children,
}: {
  /**
   * Omit on pages whose own content already says where you are — the feed's
   * mode selector is its heading, and a "Your feed, your rules." banner above it
   * only pushes the posts down.
   */
  title?: string
  subtitle?: string
  action?: ReactNode
  /** Right-hand context rail. Omitted on pages that don't need one. */
  aside?: ReactNode
  /** The left rail (profile, pinned modules, circles). A page that is a workspace of its own turns it off. */
  rail?: boolean
  /**
   * Give the page the whole window.
   *
   * For a workspace that is already a full screen of its own - the
   * conversations, which are a list and a thread side by side. Nested under
   * the chip row and inside the page's own padding, the thread was a panel
   * inside a page inside a frame, and the messages had whatever height was
   * left over. This drops the chip row and the page padding; the top bar
   * stays, because a screen with no way out of it is not a feature.
   */
  full?: boolean
  children: ReactNode
}) {
  const { user, loading } = useAuth()
  const { tok, frameStyle, rtl, resolved } = useAppTheme()
  const location = useLocation()
  const unread = useUnreadMessages()

  // Browsers refuse to start audio that no gesture asked for, so the chime
  // needs one click - any click - before it can ever be heard. It used to be
  // primed only by opening a conversation, which meant a member who had not
  // opened one yet got silence for exactly the messages they most needed
  // telling about. The first interaction anywhere does it, once.
  useEffect(() => {
    const unlock = () => primeSound()
    const options = { once: true, passive: true } as const
    window.addEventListener('pointerdown', unlock, options)
    window.addEventListener('keydown', unlock, options)
    return () => {
      window.removeEventListener('pointerdown', unlock)
      window.removeEventListener('keydown', unlock)
    }
  }, [])

  if (loading) {
    return (
      <div className="flex min-h-[60svh] items-center justify-center" role="status" aria-label="Loading">
        <div className="h-12 w-12 rounded-full animate-orb-breathe" style={{ background: 'var(--grad-orb)' }} />
      </div>
    )
  }
  if (!user) {
    // Carry the destination so a link somebody was sent opens after they sign
    // in, rather than dropping them on their dashboard wondering what they
    // clicked.
    // Arriving on somebody's invitation link is almost always a new visitor:
    // the form opens on "Create your account", with the link's own token left
    // off the way back. The token is either still on the address (?via=) or
    // already held by ReferralWatch, which tidies the address while the
    // page's code is loading.
    const params = new URLSearchParams(location.search)
    const invited = params.has('via') || Boolean(pendingLink())
    params.delete('via')
    const rest = params.toString()
    const next = encodeURIComponent(location.pathname + (rest ? `?${rest}` : ''))
    return <Navigate to={`/join?mode=${invited ? 'signup' : 'signin'}&next=${next}`} replace />
  }

  // `noise-overlay` is the project's dithering: a very dark gradient across a
  // whole viewport has too few 8-bit steps to be smooth, and the grain breaks
  // the steps up. The marketing sections have always used it; the app frame —
  // the largest gradient in the product — never did.
  return (
    <div
      className={cn(
        'app-shell relative noise-overlay',
        // Exactly the window, and nothing may spill out of it: a workspace
        // that is already a full screen must not also make the page scroll.
        // Hard-coding "the window minus 60px of chrome" was the first attempt
        // and it was wrong the moment the wellbeing bar had something to say -
        // the page then scrolled by however tall that was. A column that
        // measures itself cannot drift from whatever the chrome turns out to
        // be.
        full ? 'flex h-[100dvh] flex-col overflow-hidden' : 'min-h-[100dvh]',
      )}
      style={
        resolved === 'light'
          ? { background: 'radial-gradient(circle at 70% -20%, #fff 0, #f8f5ef 48%, #f3efe7 100%)' }
          : frameStyle
      }
      dir={rtl ? 'rtl' : 'ltr'}
    >
      {/* Chrome: the app owns the top of the page here — the marketing navbar
          is suppressed on these routes, so this sticks to 0 rather than 72. */}
      <div className={cn('sticky top-0 z-30', full && 'shrink-0')}>
        <WellbeingBar />
        <AppTopBar />
        {!full && <AppChipBar pathname={location.pathname} search={location.search} />}
      </div>

      <div
        className={cn(
          full
            // Whatever the chrome left. The phone's tab bar floats over the
            // bottom, so the content keeps clear of it there and nowhere else.
            ? 'grid min-h-0 w-full flex-1 pb-[86px] lg:pb-0'
            : 'mx-auto grid w-full max-w-[1320px] gap-[26px] px-4 pb-24 pt-6 md:px-6 lg:pb-16 lg:pt-9',
          !rail
            ? 'lg:grid-cols-[minmax(0,1fr)]'
            : aside
              ? 'lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[220px_minmax(0,1fr)_300px]'
              : 'lg:grid-cols-[220px_minmax(0,1fr)]',
        )}
      >
        {/* Left rail — identity and shortcuts */}
        {rail && (
          <div className="hidden lg:block">
            <div className="sticky top-[156px] max-h-[calc(100svh-176px)] overflow-y-auto overscroll-contain">
              <ProfileCard />
            </div>
          </div>
        )}

        {/* Centre column */}
        {/* `min-h-0` matters only in full mode, and it matters a lot: a grid
            item's default `min-height: auto` refuses to shrink below its
            content, so a long conversation list pushed this past the bottom
            of the window instead of letting the list scroll inside it. The
            list then had nothing to scroll - it was already as tall as
            everything in it. */}
        <main className={cn('min-w-0', full && 'min-h-0')}>
          {(title || action) && (
            <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
              {title ? (
                <div>
                  <h1 className={cn('text-xl font-semibold md:text-2xl', tok.text)}>{title}</h1>
                  {/* tok.mid, not tok.low: the page's one-line description is read, not
                      glanced at, and tok.low sits at 3.2–3.9:1 on the dark modes. */}
                  {subtitle && <p className={cn('mt-1 max-w-xl text-xs', tok.mid)}>{subtitle}</p>}
                </div>
              ) : (
                <span />
              )}
              {action}
            </header>
          )}
          {children}
        </main>

        {/* Right rail */}
        {aside && (
          <aside className="hidden xl:block">
            {/* Scrolls on its own. Sticky alone meant a rail taller than the
                window had its bottom permanently out of reach - which is what
                happens to anybody who zooms, and to anybody on a short laptop
                screen. No visible scrollbar (the base layer hides them), but
                wheel, trackpad, touch and keyboard all still scroll it. */}
            <div className="sticky top-[156px] max-h-[calc(100svh-176px)] space-y-3 overflow-y-auto overscroll-contain">
              {aside}
            </div>
          </aside>
        )}
      </div>

      {/* Mobile bottom bar */}
      <nav
        className={cn('fixed inset-x-0 bottom-0 z-40 lg:hidden', tok.cardSolid)}
        aria-label="Kinjy modules"
      >
        <ul className="mx-auto flex max-w-md items-center justify-around px-2 py-2">
          {MOBILE_NAV.map((item) => {
            const active =
              location.pathname === item.to.split('?')[0] &&
              (!item.to.includes('?') || location.search.includes(item.to.split('?')[1]))
            return (
              <li key={item.label}>
                <NavLink
                  to={item.to}
                  aria-label={item.to === '/messages' && unread > 0 ? `${item.label}, ${unread} unread` : item.label}
                  className={cn(
                    'flex flex-col items-center gap-1 rounded-full px-3 py-1.5 text-[0.65rem] font-medium',
                    'primary' in item && item.primary ? 'text-ink' : active ? 'text-gold-soft' : tok.mid,
                  )}
                >
                  <span
                    className={cn(
                      'relative flex h-9 w-9 items-center justify-center rounded-full',
                      'primary' in item &&
                        item.primary &&
                        '-mt-6 h-12 w-12 border-[5px] border-[#f7f4ee] bg-gradient-to-br from-gold-soft to-gold shadow-[0_6px_16px_-4px_rgba(170,124,60,0.45)]',
                    )}
                  >
                    <item.icon size={18} aria-hidden="true" />
                    {item.to === '/messages' && <UnreadBadge count={unread} className="absolute -end-1 -top-1" />}
                  </span>
                  <span className={cn('primary' in item && item.primary && tok.mid)}>{item.label}</span>
                </NavLink>
              </li>
            )
          })}
        </ul>
      </nav>

      {/* Mounted on the shell, so a message that arrives while you are reading
          the feed or looking at a profile still shows itself. It takes itself
          off on the Messages page, where the thread is the better answer. */}
      <MessageToast />
    </div>
  )
}

/** A right-rail card, themed like the rest of the shell. */
export function RailCard({ title, children }: { title: string; children: ReactNode }) {
  const { tok } = useAppTheme()
  return (
    <section className={cn('rounded-[20px] p-5', tok.card)}>
      <h2 className={cn('mb-3.5 text-[0.95rem] font-bold tracking-[-0.01em]', tok.text)}>{title}</h2>
      {children}
    </section>
  )
}
