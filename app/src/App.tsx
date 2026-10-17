import { Suspense, lazy } from 'react'
import type { ReactNode } from 'react'
import { Navigate, Routes, Route, useLocation } from 'react-router'
import RouteSkeleton from './components/app/RouteSkeleton'
import ReferralWatch from './components/app/ReferralWatch'
import Layout from './components/Layout'
import Home from './pages/Home'
import { useAuth } from './hooks/useAuth'
import { FEATURES, type Feature } from './lib/features'

// Route-level code-splitting (C5 performance budget): every sub-page ships as
// its own chunk so the landing page no longer carries the full 1.5MB bundle.
const Platform = lazy(() => import('./pages/Platform'))
const Feeds = lazy(() => import('./pages/Feeds'))
const Family = lazy(() => import('./pages/Family'))
const Memorials = lazy(() => import('./pages/Memorials'))
const Creators = lazy(() => import('./pages/Creators'))
const Commerce = lazy(() => import('./pages/Commerce'))
const Pricing = lazy(() => import('./pages/Pricing'))
const Payments = lazy(() => import('./pages/Payments'))
const Assistant = lazy(() => import('./pages/Assistant'))
const Admin = lazy(() => import('./pages/Admin'))
const Safety = lazy(() => import('./pages/Safety'))
const Developers = lazy(() => import('./pages/Developers'))
const AppDemo = lazy(() => import('./pages/AppDemo'))
const SignIn = lazy(() => import('./pages/SignIn'))
const ResetPassword = lazy(() => import('./pages/ResetPassword'))
const PostPage = lazy(() => import('./pages/PostPage'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const SocialHub = lazy(() => import('./pages/SocialHub'))
const Circles = lazy(() => import('./pages/app/Circles'))
const Connections = lazy(() => import('./pages/app/Connections'))
const Communities = lazy(() => import('./pages/app/Communities'))
const Forums = lazy(() => import('./pages/app/Forums'))
const Messages = lazy(() => import('./pages/app/Messages'))
const Team = lazy(() => import('./pages/app/Team'))
const JoinGroup = lazy(() => import('./pages/app/JoinGroup'))
const FamilyTreeApp = lazy(() => import('./pages/app/FamilyTree'))
const Graveyard = lazy(() => import('./pages/app/Graveyard'))
const MemorialPublic = lazy(() => import('./pages/MemorialPublic'))
const Marketplace = lazy(() => import('./pages/app/Marketplace'))
const MarketOrders = lazy(() => import('./pages/app/MarketOrders'))
const MarketOrder = lazy(() => import('./pages/app/MarketOrder'))
const MarketDispute = lazy(() => import('./pages/app/MarketDispute'))
const MarketSell = lazy(() => import('./pages/app/MarketSell'))
const MarketSeller = lazy(() => import('./pages/app/MarketSeller'))
const MarketVendor = lazy(() => import('./pages/app/MarketVendor'))
const MarketProduct = lazy(() => import('./pages/app/MarketProduct'))
const Explore = lazy(() => import('./pages/app/Explore'))
const Live = lazy(() => import('./pages/app/Live'))
const Shorts = lazy(() => import('./pages/app/Shorts'))
const Earn = lazy(() => import('./pages/app/Earn'))
const Stats = lazy(() => import('./pages/app/Stats'))
const Supervision = lazy(() => import('./pages/app/Supervision'))
const TrustSafety = lazy(() => import('./pages/TrustSafety'))
const Moderation = lazy(() => import('./pages/app/Moderation'))
const Profile = lazy(() => import('./pages/app/Profile'))

/**
 * Shown while a route's chunk downloads.
 *
 * It used to be an orb centred in an empty box, which on the dark theme is a
 * black screen with a dot on it - and on a slow connection, a black screen for
 * several seconds every time somebody touches a menu item. The skeleton holds
 * the page's shape instead.
 *
 * Which shape depends on where you are going: the app routes sit in the
 * three-column shell, and the public pages do not, so giving those rails would
 * be a worse lie than the orb was.
 */
/**
 * The routes that render inside the app shell, so the fallback knows which
 * shape to hold. Listed rather than inferred: the public pages are the
 * exception and there are more of them, and a wrong guess here shows up as a
 * page that jumps sideways the moment it loads.
 */
const APP_ROUTE_PREFIXES = [
  '/hub', '/p', '/explore', '/messages', '/communities', '/forums', '/circles',
  '/connections', '/u', '/dashboard', '/market', '/tree', '/graveyard',
  '/memorial', '/live', '/shorts', '/earn', '/supervision', '/settings',
  '/moderation', '/trust-safety', '/admin', '/stats', '/team',
]

function PageFallback() {
  const { pathname } = useLocation()
  const inApp = APP_ROUTE_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  )
  return <RouteSkeleton shell={inApp} />
}

/**
 * A route whose feature is switched off (see lib/features.ts) sends the visitor
 * somewhere real instead of rendering the page: app routes to the feed, public
 * pages to the home page. The page's chunk is never even loaded.
 */
function gated(feature: Feature, page: ReactNode, fallback: string) {
  return FEATURES[feature] ? page : <Navigate to={fallback} replace />
}

/**
 * A shared post, shown where it belongs to whoever opened it.
 *
 * A member gets the feed with the post open over it, because a shared link
 * should land somewhere alive rather than on one post with nothing around it.
 * Somebody signed out gets the standalone page: the feed would only bounce
 * them to a sign-in, and a link that demands an account before showing
 * anything is a link nobody forwards. The public page is also the one that
 * carries the invitation and the way to join.
 */
function SharedPost() {
  const { user, loading } = useAuth()
  if (loading) return null
  return user ? <SocialHub /> : <PostPage />
}

export default function App() {
  return (
    <Layout>
      {/* The invitation follows the visitor from whichever page the link
          pointed at to wherever they sign up. Outside Suspense: it must not
          be unmounted and remounted while a route's chunk loads. */}
      <ReferralWatch />
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/platform" element={<Platform />} />
          <Route path="/feeds" element={<Feeds />} />
          <Route path="/family" element={gated('familyTree', <Family />, '/')} />
          <Route path="/memorials" element={<Memorials />} />
          <Route path="/creators" element={<Creators />} />
          <Route path="/commerce" element={gated('marketplace', <Commerce />, '/')} />
          <Route path="/pricing" element={<Pricing />} />
          <Route path="/payments" element={<Payments />} />
          <Route path="/assistant" element={gated('assistant', <Assistant />, '/')} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/safety" element={<Safety />} />
          <Route path="/developers" element={<Developers />} />
          <Route path="/app" element={<AppDemo />} />
          {/* /join is the public entry point; ?ref= carries the inviter's code. */}
          <Route path="/join" element={<SignIn />} />
          {/* Both halves of a forgotten password: no token asks for a link,
              ?token= from the mail sets the new password. */}
          <Route path="/reset-password" element={<ResetPassword />} />
          {/* Where a post shared outside Kinjy lands. Public to a visitor:
              a link that demands a sign-in before showing anything is a link
              nobody forwards. ?ref= carries the sharer's invitation. */}
          <Route path="/p/:postId" element={<SharedPost />} />
          <Route path="/dashboard" element={<Dashboard />} />
          {/* The member's real feed. /feeds stays the public marketing page. */}
          <Route path="/hub" element={<SocialHub />} />
          {/* Universal Navigation destinations, per the blueprint */}
          <Route path="/circles" element={<Circles />} />
          <Route path="/connections" element={<Connections />} />
          <Route path="/communities" element={<Communities />} />
          {/* One community, by slug. The directory and the community are the
              same page so a join updates both without a refetch dance. */}
          <Route path="/communities/:slug" element={<Communities />} />
          <Route path="/forums" element={<Forums />} />
          <Route path="/messages" element={<Messages />} />
          <Route path="/messages/join/:code" element={<JoinGroup />} />
          <Route path="/tree" element={gated('familyTreeApp', <FamilyTreeApp />, '/hub')} />
          <Route path="/graveyard" element={<Graveyard />} />
          {/* Where a memorial's QR code leads. Public: whoever scans a headstone
              usually has no account. */}
          <Route path="/memorial/:code" element={<MemorialPublic />} />
          <Route path="/market" element={gated('marketplace', <Marketplace />, '/hub')} />
          <Route path="/market/orders" element={gated('marketplace', <MarketOrders />, '/hub')} />
          <Route path="/market/orders/:id" element={gated('marketplace', <MarketOrder />, '/hub')} />
          <Route path="/market/orders/:id/dispute" element={gated('marketplace', <MarketDispute />, '/hub')} />
          <Route path="/market/products/:id" element={gated('marketplace', <MarketProduct />, '/hub')} />
          <Route path="/market/sell" element={gated('marketplace', <MarketSell />, '/hub')} />
          <Route path="/market/seller" element={gated('marketplace', <MarketSeller />, '/hub')} />
          <Route path="/market/vendors/:vendorId" element={gated('marketplace', <MarketVendor />, '/hub')} />
          <Route path="/explore" element={<Explore />} />
          <Route path="/live" element={gated('live', <Live />, '/hub')} />
          <Route path="/shorts" element={<Shorts />} />
          <Route path="/earn" element={<Earn />} />
          {/* A member's own numbers, and who joined on their link. */}
          <Route path="/stats" element={<Stats />} />
          {/* Notifications about supervision link here, so the path is fixed. */}
          <Route path="/supervision" element={<Supervision />} />
          <Route path="/settings/supervision" element={<Supervision />} />
          {/* Staff console. The service refuses every call behind it to
              anybody who is not staff, so the route itself is not the guard. */}
          <Route path="/trust-safety" element={<TrustSafety />} />
          <Route path="/team" element={<Team />} />
          {/* Where the appeal notification sends the member. */}
          <Route path="/moderation" element={<Moderation />} />
          {/* Every avatar in the app links here. */}
          <Route path="/u/:handle" element={<Profile />} />
          <Route path="*" element={<Home />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}
