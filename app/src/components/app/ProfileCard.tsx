import { useEffect } from 'react'
import { Link } from 'react-router'
import { Award, Bookmark, Code2, Crown, Dices, LayoutGrid, LifeBuoy, MapPin, Medal, Pin, ShieldCheck, Tag, Users, Wallet } from 'lucide-react'
import { VerifiedBadge } from '@/components/ui-kit'
import { MODULE_ICONS } from '@/components/appdemo/Chrome'
import { ROUTE_FOR } from './navigation'
import type { ChromeKey } from '@/components/appdemo/theme'
import { useAppTheme } from '@/components/appdemo/theme'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { isStaff, kaluta } from '@/lib/api'
import { isRouteAvailable } from '@/lib/features'
import { onChange } from '@/lib/live'
import { useMyProfile } from '@/hooks/useMyProfile'
import { useUnreadMessages } from '@/hooks/useUnreadMessages'
import UnreadBadge from './UnreadBadge'
import { countryName } from '@/lib/profileOptions'
import { COMPANY } from '@/lib/company'
import { cn } from '@/lib/utils'

/**
 * The profile mini-card from the designed left rail: centred avatar, name with
 * its verification badge, handle, and place.
 */
/** The shortcuts /app pins. Destinations, not feed modes — and only open ones. */
const PINNED: ChromeKey[] = (['home', 'create', 'familyTree', 'messages', 'connections'] as ChromeKey[]).filter(
  (key) => isRouteAvailable(ROUTE_FOR[key]),
)

/**
 * The public pages worth reaching from inside the app.
 *
 * Deliberately short. The whole marketing navbar in a sidebar is a second
 * menu competing with the real one; these are the ones a member actually goes
 * looking for - what it costs, what the rules are, and where to get help.
 */
const PUBLIC_PAGES: Array<{ to: string; label: string; icon: typeof LifeBuoy }> = [
  { to: '/safety', label: 'Help & safety', icon: LifeBuoy },
  { to: '/platform', label: 'All modules', icon: LayoutGrid },
  { to: '/pricing', label: 'Pricing', icon: Tag },
  { to: '/payments', label: 'Payments', icon: Wallet },
  { to: '/developers', label: 'Developers', icon: Code2 },
]

export default function ProfileCard() {
  const { user } = useAuth()
  const { t, tok, lang } = useAppTheme()
  // Real circles, not the demo's four fixed names: this rail is a switch into
  // the member's own audiences, and inventing "Business" for someone who has
  // no such circle would make it a decoration.
  const circles = useApi(() => kaluta.circles.list(), [])
  // Shared with the top bar and the composer, and refreshed whenever the
  // profile changes (an edit, or following someone changing a count here).
  const data = useMyProfile()
  // Only on this card, which nobody but the member sees: how many connections
  // someone has — a teenager especially — is not for strangers on /u/:handle.
  const connections = useApi(() => kaluta.connections.list(), [])
  useEffect(() => onChange('connections', connections.reload), [connections.reload])
  const unread = useUnreadMessages()

  // A circle made, renamed or deleted anywhere in the app changes this rail.
  // (The profile's own counts are refreshed by useMyProfile.)
  useEffect(() => onChange('circles', circles.reload), [circles.reload])
  // The groups the member pinned, refreshed when a pin changes anywhere.
  const pinned = useApi(() => kaluta.communities.pinned(), [])
  useEffect(() => onChange('pins', pinned.reload), [pinned.reload])

  // The profile is what the editor changes; the account copy only stands in
  // until it has loaded.
  const displayName = data?.display_name ?? user?.display_name ?? ''
  const place = [data?.city, data?.country && countryName(data.country, lang)].filter(Boolean).join(', ')

  return (
    <div className="space-y-3">
      <div className={cn('rounded-card-lg p-4 text-center', tok.card)}>
        <div className="flex justify-center">
          <MemberAvatar handle={user?.handle} displayName={displayName} avatarUrl={data?.avatar_url} size={80} ring />
        </div>

        {/* The name, with the handle on hover. A person is their name; the
            handle is an address, and it was drawn here as a gold badge -
            the most prominent thing on the card after the name itself. */}
        <p
          className={cn('mt-3 flex items-center justify-center gap-1.5 text-sm font-bold', tok.text)}
          title={user?.handle ? `@${user.handle}` : undefined}
        >
          {displayName}
          {user?.kyc_verified && <VerifiedBadge size={15} />}
        </p>
        <div className="mt-1 flex flex-col items-center gap-1.5">
          {place && (
            <span className={cn('flex items-center gap-1 text-[0.7rem]', tok.low)}>
              <MapPin size={10} aria-hidden="true" />
              {place}
            </span>
          )}
        </div>

        <dl className={cn('mt-3 flex justify-center gap-5 border-t pt-3', tok.divider, 'border-t-current/10')}>
          <div>
            <dt className={cn('text-[0.65rem]', tok.low)}>Followers</dt>
            <dd className="mono-data text-sm font-semibold text-gold-soft">
              {data?.followers_count ?? 0}
            </dd>
          </div>
          <div>
            <dt className={cn('text-[0.65rem]', tok.low)}>Following</dt>
            <dd className="mono-data text-sm font-semibold text-gold-soft">
              {data?.following_count ?? 0}
            </dd>
          </div>
          <div>
            <dt className={cn('text-[0.65rem]', tok.low)}>
              <Link to="/connections" className="hover:text-gold-soft">
                {t('connections')}
              </Link>
            </dt>
            <dd className="mono-data text-sm font-semibold text-gold-soft">
              {/* The label is the link for assistive tech; the number is a bigger target for the pointer. */}
              <Link to="/connections" tabIndex={-1} aria-hidden="true">
                {connections.data ? connections.data.accepted.length : '–'}
              </Link>
            </dd>
          </div>
        </dl>
      </div>

      {/* Pinned modules */}
      <div className={cn('rounded-card-lg p-3.5', tok.card)}>
        <p className={cn('mb-2 text-[0.65rem] font-bold uppercase tracking-wider', tok.low)}>
          {t('pinned')}
        </p>
        <ul className="space-y-1">
          {PINNED.map((key) => {
            const Icon = MODULE_ICONS[key] ?? Users
            return (
              <li key={key}>
                <Link
                  to={ROUTE_FOR[key]}
                  aria-label={key === 'messages' && unread > 0 ? `${t(key)}, ${unread} unread` : undefined}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold',
                    tok.mid,
                    tok.hoverBg,
                  )}
                >
                  <Icon size={12} className="text-gold" aria-hidden="true" />
                  {t(key)}
                  {key === 'messages' && <UnreadBadge count={unread} className="ms-auto" />}
                </Link>
              </li>
            )
          })}
        </ul>
      </div>

      {/* Pinned groups. Only when there are some: an empty box saying
          "nothing pinned" is furniture. Pinning is done from a group's page. */}
      {(pinned.data?.items ?? []).length > 0 && (
        <div className={cn('rounded-card-lg p-3.5', tok.card)}>
          <p className={cn('mb-2 text-xs font-bold', tok.low)}>Pinned communities</p>
          <ul className="space-y-1">
            {(pinned.data?.items ?? []).map((group) => (
              <li key={group.id}>
                <Link
                  to={`/communities/${group.slug}`}
                  className={cn(
                    'flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold',
                    tok.mid,
                    tok.hoverBg,
                  )}
                >
                  <Pin size={12} className="shrink-0 text-gold" aria-hidden="true" />
                  <span className="truncate">{group.name}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The public pages, from inside the app.

          The app chrome replaces the marketing navbar, which left a signed-in
          member with no route to the terms they accepted, what a plan costs,
          or where to ask for help - short of signing out or typing a URL. */}
      <div className={cn('rounded-card-lg p-3.5', tok.card)}>
        <p className={cn('mb-2 text-[0.65rem] font-bold uppercase tracking-wider', tok.low)}>
          Spaces &amp; help
        </p>
        <ul className="space-y-1">
          {/* Staff get their way into the console here; nobody else sees it. */}
          {isStaff(user?.role) && (
            <li>
              <Link
                to="/trust-safety"
                className={cn('flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold', tok.mid, tok.hoverBg)}
              >
                <ShieldCheck size={12} className="text-gold" aria-hidden="true" />
                Review console
              </Link>
            </li>
          )}
          {user && (
            <li>
              <Link
                to="/saved"
                className={cn('flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold', tok.mid, tok.hoverBg)}
              >
                <Bookmark size={12} className="text-gold" aria-hidden="true" />
                Saved
              </Link>
            </li>
          )}
          {(user?.role === 'admin' || user?.role === 'superadmin') && (
            <li>
              <Link
                to="/admin"
                className={cn('flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold', tok.mid, tok.hoverBg)}
              >
                <LayoutGrid size={12} className="text-gold" aria-hidden="true" />
                Admin dashboard
              </Link>
            </li>
          )}
          {(user?.role === 'admin' || user?.role === 'superadmin') && (
            <li>
              <Link
                to="/admin/payouts"
                className={cn('flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold', tok.mid, tok.hoverBg)}
              >
                <Wallet size={12} className="text-gold" aria-hidden="true" />
                Payouts
              </Link>
            </li>
          )}
          {(user?.role === 'admin' || user?.role === 'superadmin') && (
            <li>
              <Link
                to="/admin/referral-pool"
                className={cn('flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold', tok.mid, tok.hoverBg)}
              >
                <Dices size={12} className="text-gold" aria-hidden="true" />
                Referral pool
              </Link>
            </li>
          )}
          {isStaff(user?.role) && (
            <li>
              <Link
                to="/team"
                className={cn('flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold', tok.mid, tok.hoverBg)}
              >
                <Users size={12} className="text-gold" aria-hidden="true" />
                Team
              </Link>
            </li>
          )}
          {PUBLIC_PAGES.filter((page) => isRouteAvailable(page.to)).map((page) => (
            <li key={page.to}>
              <Link
                to={page.to}
                className={cn(
                  'flex w-full items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold',
                  tok.mid,
                  tok.hoverBg,
                )}
              >
                <page.icon size={12} className="text-gold" aria-hidden="true" />
                {page.label}
              </Link>
            </li>
          ))}
        </ul>
        {/* Signed in, there is no site footer - so the registered office it
            carries is said here, under the pages it would sit beside. */}
        <address className={cn('mt-3 border-t border-[var(--cloud-border)] px-3 pt-2.5 text-[0.65rem] not-italic leading-snug', tok.low)}>
          {COMPANY.name} · Registered office
          <br />
          {COMPANY.address}
        </address>
      </div>

      {/* Circles quick-switch */}
      <div className={cn('rounded-card-lg p-3.5', tok.card)}>
        <p className={cn('mb-2 text-[0.65rem] font-bold uppercase tracking-wider', tok.low)}>
          {t('circles')}
        </p>
        {(circles.data ?? []).length === 0 ? (
          <Link to="/circles" className={cn('text-[0.7rem] hover:text-gold-soft', tok.low)}>
            No circles yet — create one
          </Link>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {(circles.data ?? []).slice(0, 6).map((circle, index) => (
              <Link
                key={circle.id}
                to={`/circles?open=${circle.id}`}
                className={cn(
                  'rounded-full px-2.5 py-1 text-[0.68rem] font-semibold',
                  index === 0
                    ? cn('bg-gold/15 ring-1 ring-gold/40', tok.text)
                    : cn(tok.subtleBg, tok.mid, tok.hoverBg),
                )}
              >
                {circle.name}
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Badges. Earned, not decorative: each one is shown only when the
          account actually holds it, so an empty row is the honest state for a
          new member rather than three grey trophies. */}
      {(user?.kyc_verified || (data?.followers_count ?? 0) > 0) && (
        <div className={cn('flex items-center justify-around rounded-card-lg p-3', tok.card)}>
          {[
            { icon: Medal, label: 'Member', show: true },
            { icon: Award, label: 'Verified', show: Boolean(user?.kyc_verified) },
            { icon: Crown, label: 'Pool contributor', show: (data?.followers_count ?? 0) >= 10 },
          ]
            .filter((badge) => badge.show)
            .map((badge) => (
              <span
                key={badge.label}
                title={badge.label}
                className="flex h-9 w-9 items-center justify-center rounded-full bg-gold/10 text-gold ring-1 ring-gold/30"
              >
                <badge.icon size={15} aria-hidden="true" />
              </span>
            ))}
        </div>
      )}
    </div>
  )
}
