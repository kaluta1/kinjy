import { Suspense, lazy, useEffect } from 'react'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router'
import { useAuth } from '@/hooks/useAuth'
import BackToTop from './BackToTop'
import Navbar from './Navbar'
import Footer from './Footer'
import { FEATURES } from '@/lib/features'

/**
 * The assistant arrives after the page, not with it.
 *
 * It carries its own knowledge base - 148 kB of source, five languages of
 * answers - and it was imported here, in the shell, so every page downloaded
 * all of it before rendering anything. A member opening their feed paid for
 * the answers to questions about the marketing site.
 *
 * Lazy, with no fallback: the orb appearing a moment after the page is how
 * every floating helper behaves, and nothing on the page is waiting for it.
 */
const LiveAssistant = lazy(() => import('./assistant/LiveAssistant'))

// GSAP used to be registered here, which is what put the whole of gsap and
// ScrollTrigger into the entry bundle - downloaded by every visitor and every
// member on every page, including a feed that has no animation in it. Nothing
// in this shell uses it: the registration was for components on lazy routes,
// and every one of those registers the plugin itself. Removing it lets gsap
// travel with the pages that actually animate.

/**
 * Routes that render `AppShell`, which brings its own top bar, chip nav and
 * footerless full-height layout. The marketing navbar and footer are suppressed
 * there: stacking them produced two headers, one above the other.
 */
const APP_ROUTES = [
  '/hub',
  '/dashboard',
  '/circles',
  '/connections',
  '/communities',
  '/forums',
  '/messages',
  '/tree',
  '/graveyard',
  '/market',
  '/explore',
  '/earn',
  '/stats',
  '/live',
  '/shorts',
  '/supervision',
  '/trust-safety',
  '/team',
  '/admin',
  '/saved',
  '/moderation',
  // Covers /settings/supervision, which the notifications link to.
  '/settings',
  '/u',
]

/** Public pages already on the new design: they bring their own navigation and
 *  footer (components/landing/PublicShell), so the old marketing ones step aside. */
const OWN_CHROME_PAGES = ['/', '/memorials', '/platform', '/feeds', '/creators', '/pricing', '/payments', '/safety', '/developers', '/app', '/join', '/commerce', '/family', '/assistant']

/** Public pages with a dynamic part (/p/:postId, /memorial/:code) and the sign-in helpers. */
const OWN_CHROME_PREFIXES = ['/p/', '/memorial/', '/reset-password', '/payout-wallet/']

function hasOwnChrome(pathname: string): boolean {
  return OWN_CHROME_PAGES.includes(pathname) || OWN_CHROME_PREFIXES.some((prefix) => pathname.startsWith(prefix))
}

export function isAppRoute(pathname: string): boolean {
  return APP_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`))
}

/**
 * Shared layout (children pattern — App wraps <Routes> inside <Layout>).
 *
 * Marketing pages get the fixed 72px overlay nav (design §7.1) and the footer;
 * the signed-in app gets neither, because AppShell is the chrome there.
 */
export default function Layout({ children }: { children: ReactNode }) {
  const location = useLocation()
  const { user } = useAuth()
  // An app route brings its own chrome *when there is somebody signed in to
  // show it to*. AppShell's sidebar is a profile card, pinned modules and a
  // circles list - nothing a visitor has. The pages a visitor can actually
  // reach (a public community) would otherwise render with no header at all,
  // and no way back to the rest of the site.
  const inApp = isAppRoute(location.pathname) && Boolean(user)
  // The landing page brings its own navigation, footer and paper palette (the
  // "Kinjy Landing" design); the dark marketing chrome around it would frame
  // a light page in a second, different header.
  const ownChrome = inApp || hasOwnChrome(location.pathname)

  // Scroll behavior on route change: honor hash deep links (e.g.
  // /family#reunion-planner) after the lazy page mounts; otherwise scroll to top.
  useEffect(() => {
    if (!location.hash) {
      window.scrollTo(0, 0)
      return
    }
    const id = location.hash.slice(1)
    let attempts = 0
    const tryScroll = () => {
      const el = document.getElementById(id)
      if (el) {
        el.scrollIntoView({ behavior: 'auto', block: 'start' })
      } else if (attempts++ < 40) {
        // Lazy page chunks mount asynchronously — retry briefly.
        setTimeout(tryScroll, 100)
      } else {
        window.scrollTo(0, 0)
      }
    }
    tryScroll()
  }, [location.pathname, location.hash])

  // The App demo (/app) embeds its own Assistant orb inside the product shell,
  // so the global orb is suppressed there to avoid duplication.
  const showAssistant = FEATURES.assistant && !location.pathname.startsWith('/app')

  return (
    // overflow-x-clip is the mobile guard for the scroll-reveal animations.
    // Sections enter with a translateX, so until they scroll into view they sit
    // 40-60px right of where they belong. On a desktop container that slack is
    // absorbed; at 375px it became real page width, and the whole site could be
    // dragged sideways.
    //
    // clip, not hidden: hidden would make this a scroll container, which breaks
    // the sticky rails inside the app shell and lets the page trap scroll. clip
    // just cuts the overflow off and changes nothing else.
    <div className="min-h-[100dvh] overflow-x-clip bg-ink text-text-hi">
      {!ownChrome && <Navbar />}
      <main className={ownChrome ? undefined : 'pt-[72px]'}>{children}</main>
      {!ownChrome && <Footer />}
      {/* Every public page, which is where the tall ones are - and not inside
          the app, whose panels scroll themselves.

          Not `!ownChrome`: that was the first guard and it excluded exactly
          the pages this is for. The landing menu's pages - platform, feeds,
          creators, pricing, payments, safety, developers - are listed as
          having their own chrome because they draw their own nav, which is
          unrelated to how far down they go. */}
      {!inApp && <BackToTop />}
      {showAssistant && (
        <Suspense fallback={null}>
          <LiveAssistant />
        </Suspense>
      )}
      {/* Film grain belongs to the dark surfaces; on white paper it reads as dirt. */}
      {!hasOwnChrome(location.pathname) && <div className="global-grain" aria-hidden="true" />}
    </div>
  )
}
