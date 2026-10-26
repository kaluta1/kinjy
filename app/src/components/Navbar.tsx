import { useEffect, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router'
import { ChevronDown, Globe, Menu, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { LANGUAGES, setLanguage } from '@/i18n'
import { useAuth } from '@/hooks/useAuth'
import { isRouteAvailable } from '@/lib/features'
import { useMyProfile } from '@/hooks/useMyProfile'
import MemberAvatar from '@/components/social/MemberAvatar'
import ThemeMenu from './ThemeMenu'

/**
 * Ten top-level links read as a site map rather than a way in. Five carry the
 * bar; the rest live under "More" — still one click away, no longer competing
 * for the same glance. The mobile drawer keeps showing everything flat, where
 * vertical space is not the constraint.
 *
 * A page whose feature is switched off in lib/features.ts drops out of both
 * lists, and so out of the drawer and the "More" menu built from them.
 */
const PRIMARY_LINKS = [
  { to: '/platform', key: 'nav.platform', fallback: 'Platform' },
  // '/feeds' is the marketing page about feeds; a signed-in member who
  // clicks "Feeds" wants their actual feed. Swapped below rather than
  // listed twice, so the label and translation stay in one place.
  { to: '/feeds', key: 'nav.feeds', fallback: 'Feeds', appTo: '/hub' },
  { to: '/family', key: 'nav.family', fallback: 'Family' },
  { to: '/creators', key: 'nav.creators', fallback: 'Creators' },
  { to: '/pricing', key: 'nav.pricing', fallback: 'Pricing' },
].filter((l) => isRouteAvailable(l.to))

const SECONDARY_LINKS = [
  { to: '/memorials', key: 'nav.memorials', fallback: 'Memorials' },
  { to: '/commerce', key: 'nav.commerce', fallback: 'Commerce' },
  { to: '/payments', key: 'nav.payments', fallback: 'Payments' },
  { to: '/safety', key: 'nav.safety', fallback: 'Safety' },
  { to: '/developers', key: 'nav.developers', fallback: 'Developers' },
].filter((l) => isRouteAvailable(l.to))

/** Every marketing route, in order — used by the mobile drawer and the footer. */
const NAV_LINKS = [...PRIMARY_LINKS, ...SECONDARY_LINKS]

/** Marketing Navbar (§7.1) — fixed 72px overlay nav; transparent at top, glass after 24px scroll. */
export default function Navbar() {
  const { t, i18n } = useTranslation()
  const { user } = useAuth()
  const me = useMyProfile()
  const [scrolled, setScrolled] = useState(false)
  const [langOpen, setLangOpen] = useState(false)
  const [moreOpen, setMoreOpen] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()
  const secondaryActive = SECONDARY_LINKS.some((l) => location.pathname.startsWith(l.to))

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Every menu closes when the page changes. Done during render, not in an
  // effect: an effect would paint the new page with the menu still open first.
  const [menusPath, setMenusPath] = useState(location.pathname)
  if (menusPath !== location.pathname) {
    setMenusPath(location.pathname)
    setDrawerOpen(false)
    setLangOpen(false)
    setMoreOpen(false)
  }

  // The page's language and direction follow i18n, however the language was
  // changed (this menu, the footer, the app's own switcher), not only here.
  useEffect(() => {
    const code = i18n.language.slice(0, 2)
    document.documentElement.lang = code
    document.documentElement.dir = LANGUAGES.find((l) => l.code === code)?.dir ?? 'ltr'
  }, [i18n.language])

  const switchLanguage = (code: string) => {
    // Through the shared helper, so this menu and the landing page's switcher
    // agree and the choice survives a reload. They used to disagree: this one
    // changed the language for the visit and forgot it.
    setLanguage(code)
    setLangOpen(false)
  }

  return (
    <>
      <header
        className={cn(
          'fixed top-0 inset-x-0 z-50 h-[72px]',
          scrolled ? 'cloud-glass bg-ink/60 shadow-cloud' : 'bg-transparent',
        )}
      >
        <div className="mx-auto flex h-full max-w-container items-center justify-between gap-4 px-6">
          {/* Brand. The public site's home, signed in or not: this is the
              marketing chrome, and its logo means this site. The app's own
              logo (components/app/AppChrome) goes to the feed instead - the
              home of the thing you are actually in. */}
          <Link to="/" className="flex items-center gap-2.5 shrink-0" aria-label="Kinjy home">
            <img src="/logo.svg" alt="" className="h-8 w-8" />
            <span className="font-display text-xl font-medium tracking-tight">Kinjy</span>
          </Link>

          {/* Center links */}
          <nav className="hidden lg:flex items-center gap-1" aria-label="Primary">
            {PRIMARY_LINKS.map((l) => (
              <NavLink
                key={l.to}
                // A signed-in member clicking "Feeds" wants their feed, not the
                // page explaining what feeds are.
                to={user && 'appTo' in l && l.appTo ? l.appTo : l.to}
                className={({ isActive }) =>
                  cn(
                    'relative rounded-full px-3 py-2 text-sm font-medium',
                    isActive ? 'text-gold-soft' : 'text-text-mid hover:text-text-hi',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {t(l.key, { defaultValue: l.fallback })}
                    {isActive && (
                      <span
                        className="absolute -bottom-0.5 left-3 right-3 h-0.5 rounded-full"
                        style={{ background: 'var(--grad-arc)' }}
                      />
                    )}
                  </>
                )}
              </NavLink>
            ))}

            {/* More — the remaining sections, one click away */}
            <div
              className="relative"
              onMouseEnter={() => setMoreOpen(true)}
              onMouseLeave={() => setMoreOpen(false)}
            >
              <button
                type="button"
                onClick={() => setMoreOpen((v) => !v)}
                aria-expanded={moreOpen}
                className={cn(
                  'flex items-center gap-1 rounded-full px-3 py-2 text-sm font-medium',
                  secondaryActive ? 'text-gold-soft' : 'text-text-mid hover:text-text-hi',
                )}
              >
                {t('nav.more', { defaultValue: 'More' })}
                <ChevronDown size={14} className={cn(moreOpen && 'rotate-180')} />
              </button>
              {moreOpen && (
                  <ul
                    className="absolute start-0 top-11 w-48 rounded-card-md cloud-glass bg-ink-2/95 p-2 shadow-cloud"
                  >
                    {SECONDARY_LINKS.map((l) => (
                      <li key={l.to}>
                        <NavLink
                          to={l.to}
                          className={({ isActive }) =>
                            cn(
                              'block rounded-card-sm px-3 py-2 text-sm',
                              isActive
                                ? 'text-gold-soft bg-gold/10'
                                : 'text-text-mid hover:text-text-hi hover:bg-white/5',
                            )
                          }
                        >
                          {t(l.key, { defaultValue: l.fallback })}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                )}
            </div>
          </nav>

          {/* Right cluster */}
          <div className="flex items-center gap-2">
            {/* Language switcher */}
            <div className="relative">
              <button
                type="button"
                onClick={() => setLangOpen((v) => !v)}
                aria-expanded={langOpen}
                aria-label="Change language"
                className="flex h-10 w-10 items-center justify-center rounded-full cloud-glass text-text-mid hover:text-gold-soft"
              >
                <Globe size={17} />
              </button>
              {langOpen && (
                  <ul
                    className="absolute end-0 top-12 w-44 rounded-card-md cloud-glass bg-ink-2/95 p-2 shadow-cloud"
                  >
                    {LANGUAGES.map((l) => (
                      <li key={l.code}>
                        <button
                          type="button"
                          onClick={() => switchLanguage(l.code)}
                          className={cn(
                            'w-full rounded-card-sm px-3 py-2 text-start text-sm',
                            i18n.language === l.code ? 'text-gold-soft bg-gold/10' : 'text-text-mid hover:text-text-hi hover:bg-white/5',
                          )}
                        >
                          {l.label}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
            </div>

            {/* Light, Dark or System: the same setting as the app's switcher. */}
            <ThemeMenu />

            {user ? (
              <>
              <Link
                to="/hub"
                className="hidden md:inline-flex items-center rounded-full px-4 py-2.5 text-sm font-semibold cloud-glass text-text-hi hover:border-gold/40 hover:text-gold-soft"
              >
                {t('nav.hub', { defaultValue: 'Feed' })}
              </Link>
              <Link
                to="/dashboard"
                className="hidden md:inline-flex items-center gap-2 rounded-full px-4 py-2.5 text-sm font-semibold cloud-glass text-text-hi hover:border-gold/40 hover:text-gold-soft"
              >
                <MemberAvatar displayName={user.display_name} avatarUrl={me?.avatar_url} size={24} />
                {t('nav.dashboard', { defaultValue: 'Dashboard' })}
              </Link>
              </>
            ) : (
              <>
                <Link
                  to="/join?mode=signin"
                  className="hidden md:inline-flex items-center rounded-full px-5 py-2.5 text-sm font-semibold cloud-glass text-text-hi hover:border-gold/40 hover:text-gold-soft"
                >
                  {t('nav.signIn', { defaultValue: 'Sign in' })}
                </Link>
                <Link
                  to="/join?mode=signup"
                  className="hidden md:inline-flex items-center rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.35)] hover:brightness-110"
                >
                  {t('nav.join', { defaultValue: 'Join Kinjy' })}
                </Link>
              </>
            )}

            {/* Mobile hamburger */}
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open menu"
              className="flex lg:hidden h-10 w-10 items-center justify-center rounded-full cloud-glass text-text-hi"
            >
              <Menu size={18} />
            </button>
          </div>
        </div>
      </header>

      {/* Mobile full-screen glass drawer */}
      {drawerOpen && (
          <div
            className="fixed inset-0 z-[60] cloud-glass bg-ink/85 backdrop-blur-2xl lg:hidden"
            role="dialog"
            aria-label="Menu"
          >
            <div className="flex h-[72px] items-center justify-between px-6">
              <span className="flex items-center gap-2.5">
                <img src="/logo.svg" alt="" className="h-8 w-8" />
                <span className="font-display text-xl font-medium">Kinjy</span>
              </span>
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                aria-label="Close menu"
                className="flex h-10 w-10 items-center justify-center rounded-full cloud-glass text-text-hi"
              >
                <X size={18} />
              </button>
            </div>
            <nav className="flex flex-col gap-1 px-8 pt-8" aria-label="Mobile">
              {([
                { to: '/', label: 'Home' },
                ...NAV_LINKS.map((l) => ({
                  // Signed in, "Feeds" means the member's own feed.
                  to: 'appTo' in l && l.appTo && user ? l.appTo : l.to,
                  label: t(l.key, { defaultValue: l.fallback }),
                })),
                { to: '/assistant', label: 'Kinjy Assistant' },
                // Only for the people it is for: the dashboard refuses anybody else.
                ...(user?.role === 'admin' || user?.role === 'superadmin' ? [{ to: '/admin', label: 'Admin Console' }] : []),
                { to: '/app', label: 'The App' },
                user
                  ? { to: '/dashboard', label: t('nav.dashboard', { defaultValue: 'Dashboard' }) }
                  : { to: '/join?mode=signup', label: t('nav.join', { defaultValue: 'Join Kinjy' }) },
                // Typed explicitly: the entries have different shapes, and
                // without this TypeScript widens the union to {} and the list
                // loses both `to` and `label`.
              ] as Array<{ to: string; label: string }>).filter((l) => isRouteAvailable(l.to)).map((l) => (
                <div key={l.to}>
                  <NavLink
                    to={l.to}
                    className={({ isActive }) =>
                      cn(
                        'block rounded-card-md px-4 py-3 font-display text-2xl',
                        isActive ? 'text-gold-soft' : 'text-text-hi hover:text-gold-soft',
                      )
                    }
                  >
                    {l.label}
                  </NavLink>
                </div>
              ))}
              <div className="mt-6 flex flex-wrap gap-2 px-4">
                {LANGUAGES.map((l) => (
                  <button
                    key={l.code}
                    type="button"
                    onClick={() => switchLanguage(l.code)}
                    className={cn(
                      'rounded-full px-3 py-1.5 text-sm cloud-glass',
                      i18n.language === l.code ? 'text-gold-soft border-gold/40' : 'text-text-mid',
                    )}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            </nav>
          </div>
        )}
    </>
  )
}
