import { useState } from 'react'
import { Navigate, Link, useSearchParams } from 'react-router'
import { Check, Copy, Gift, KeyRound, Link2, Lock, LogOut, Settings, ShieldCheck, SlidersHorizontal, UserRound, Wallet, type LucideIcon } from 'lucide-react'
import { useAuth } from '@/hooks/useAuth'
import Earnings from '@/components/dashboard/Earnings'
import Security from '@/components/dashboard/Security'
import Verification from '@/components/dashboard/Verification'
import CloseAccount from '@/components/dashboard/CloseAccount'
import Experience from '@/components/dashboard/Experience'
import Privacy from '@/components/dashboard/Privacy'
import ProfileEditor from '@/components/dashboard/ProfileEditor'
import ShortLinks from '@/components/dashboard/ShortLinks'
import { Badge, Panel } from '@/components/dashboard/primitives'
import LinkQr from '@/components/ui-kit/LinkQr'
import { useShortLink } from '@/lib/shortLink'
import { copyLink } from '@/lib/share'
import AppShell from '@/components/app/AppShell'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useMyProfile } from '@/hooks/useMyProfile'
import { cn } from '@/lib/utils'

const TABS: ReadonlyArray<{ id: string; label: string; icon: LucideIcon }> = [
  { id: 'earnings', label: 'Earnings', icon: Wallet },
  { id: 'links', label: 'Short links', icon: Link2 },
  { id: 'profile', label: 'Profile', icon: UserRound },
  { id: 'verification', label: 'Verification', icon: ShieldCheck },
  { id: 'privacy', label: 'Privacy', icon: Lock },
  { id: 'experience', label: 'Feed & experience', icon: SlidersHorizontal },
  { id: 'security', label: 'Security', icon: KeyRound },
  { id: 'account', label: 'Account', icon: Settings },
]

type TabId = 'earnings' | 'links' | 'profile' | 'verification' | 'privacy' | 'experience' | 'security' | 'account'

/**
 * The two pages an invitation can open, as short links (kinjy.com/s/<token>).
 *
 * The sign-up link opens the form in sign-up mode; the home-page link shows
 * what Kinjy is before asking anything. Both credit the member: the server
 * keeps whose link it is, so the address carries no code. Links already sent
 * the old way (/join?ref=CODE, /?ref=CODE) keep working.
 */
const INVITE_LINKS = [
  { id: 'signup', label: 'Sign-up link', hint: 'Opens straight on the sign-up form.', destination: '/join?mode=signup' },
  { id: 'home', label: 'Home page link', hint: 'Shows what Kinjy is first. Still credited to you.', destination: '/' },
] as const

function InviteLinkRow({ label, hint, destination }: { label: string; hint: string; destination: string }) {
  const link = useShortLink(destination)
  const [copied, setCopied] = useState<'yes' | 'failed' | null>(null)

  const copy = async () => {
    if (!link.url) return
    // "Copied" only once the clipboard has it; otherwise the link stays
    // selectable in the box for a manual copy.
    const result = await copyLink(link.url)
    setCopied(result === 'copied' ? 'yes' : 'failed')
    if (result === 'copied') setTimeout(() => setCopied(null), 2000)
  }

  return (
    <div className="flex flex-col gap-4 rounded-2xl bg-text-hi/[0.04] p-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-text-hi">{label}</p>
        <p className="mt-0.5 text-xs text-text-low">{hint}</p>
        <div className="mt-3 flex gap-2">
          <input
            readOnly
            value={link.url ?? (link.error ? '' : 'Creating your link…')}
            onFocus={(e) => e.currentTarget.select()}
            aria-busy={link.loading}
            className="w-full min-w-0 rounded-full border border-transparent bg-text-hi/[0.07] px-4 py-2.5 text-sm text-text-mid focus:border-gold/50 focus:outline-none"
            aria-label={label}
          />
          <button
            type="button"
            onClick={copy}
            disabled={!link.url}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40"
          >
            {copied === 'yes' ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
            {copied === 'yes' ? 'Copied' : 'Copy'}
          </button>
        </div>
        {link.error && (
          <p role="alert" className="mt-2 text-xs text-red-200">
            {link.error}{' '}
            <button type="button" onClick={link.retry} className="underline">
              Try again
            </button>
          </p>
        )}
        {copied === 'failed' && (
          <p role="alert" className="mt-2 text-xs text-text-low">
            The browser did not allow copying. Select the link above and copy it.
          </p>
        )}
      </div>
      {/* For the person standing next to you, a poster or a slide - places
          where nobody is going to type an address. The same short link. */}
      {link.url ? (
        <LinkQr url={link.url} label={label.toLowerCase()} filename={`kinjy-invite-${label.split(' ')[0].toLowerCase()}`} size={112} />
      ) : (
        <span aria-hidden="true" className="h-[112px] w-[112px] shrink-0 animate-pulse rounded-xl bg-text-hi/[0.06]" />
      )}
    </div>
  )
}

function InviteCard({ code }: { code: string }) {
  return (
    <section className="cloud-card relative overflow-hidden !border-gold/50 p-6 md:p-7">
      <span aria-hidden="true" className="absolute -right-16 -top-16 h-52 w-52 rounded-full bg-gold/20 blur-3xl" />
      <div className="relative flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <span aria-hidden="true" className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl bg-gold/20 text-gold-soft">
            <Gift size={22} />
          </span>
          <div>
            <h2 className="text-[1.25rem] font-bold tracking-[-0.025em] text-text-hi">Invite</h2>
            <p className="mt-1 text-sm text-text-low">You earn 20% of Kinjy's revenue on everything they do here.</p>
          </div>
        </div>
        <div className="rounded-2xl bg-text-hi/[0.06] px-5 py-3 text-center">
          <p className="mono-data text-[0.65rem] font-bold uppercase tracking-[0.14em] text-text-low">Your referral code</p>
          <p className="mono-data mt-1 text-2xl font-bold tracking-[0.2em] text-gold-soft">{code}</p>
        </div>
      </div>

      <div className="relative mt-6 space-y-3">
        {INVITE_LINKS.map((link) => (
          <InviteLinkRow key={link.id} label={link.label} hint={link.hint} destination={link.destination} />
        ))}
      </div>

      <p className="relative mt-4 text-sm leading-relaxed text-text-low">
        One level, and it is yours for good: nobody above you earns on your members, and you are
        paid out of Kinjy's own margin rather than out of what anyone pays a seller.
      </p>
    </section>
  )
}

/** The member's own space: earnings, verification, devices, account lifecycle. */
export default function Dashboard() {
  const { user, loading, signOut } = useAuth()
  // Seeded from the URL so a link can point at one tab — the wellbeing notice
  // sends the member straight to the setting it is about.
  const [params] = useSearchParams()
  const profile = useMyProfile()
  const requested = params.get('tab')
  const [tab, setTab] = useState<TabId>(
    TABS.some((t) => t.id === requested) ? (requested as TabId) : 'earnings',
  )

  if (loading) {
    return (
      <div className="flex min-h-[60svh] items-center justify-center" role="status" aria-label="Loading">
        <div className="h-12 w-12 rounded-full animate-orb-breathe" style={{ background: 'var(--grad-orb)' }} />
      </div>
    )
  }

  // Route guard: an unauthenticated visitor is sent to sign in, not shown an
  // empty dashboard that 401s panel by panel.
  if (!user) return <Navigate to="/join?mode=signin" replace />

  const isAdmin = user.role === 'admin' || user.role === 'superadmin'
  const ghost =
    'inline-flex items-center gap-2 rounded-full border border-[var(--cloud-border)] bg-[var(--cloud)] px-4 py-2 text-sm font-semibold text-text-mid transition-colors hover:border-gold/50 hover:text-text-hi'

  return (
    <AppShell>
      {/* Who this space belongs to, and the two things you do from it */}
      <section className="cloud-card mb-5 overflow-hidden">
        <div aria-hidden="true" className="h-28 bg-gradient-to-r from-[#F0C878] via-[#F2B8A2] to-[#C9CDF5]" />
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 px-6 pb-6 md:px-8">
          <div className="-mt-12 flex min-w-0 items-end gap-5">
            <MemberAvatar
              handle={user.handle}
              displayName={profile?.display_name ?? user.display_name}
              avatarUrl={profile?.avatar_url}
              size={96}
              ring
              className="shrink-0 border-4 border-[var(--cloud)]"
            />
            <div className="min-w-0 pb-1 pt-[3.4rem]">
              <p className="mono-data text-[0.72rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Your space</p>
              <h1 className="mt-1 truncate text-[clamp(28px,3.6vw,42px)] font-bold leading-[1.05] tracking-[-0.04em] text-text-hi">
                {user.display_name}
              </h1>
            </div>
          </div>
          <div className="flex items-center gap-2 pb-1">
            {isAdmin && (
              <Link to="/admin" className={ghost}>
                Admin console
              </Link>
            )}
            <button type="button" onClick={() => void signOut()} className={ghost}>
              <LogOut size={14} aria-hidden="true" />
              Sign out
            </button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-[var(--cloud-border)] px-6 py-4 md:px-8">
          <Badge tone={user.kyc_verified ? 'good' : 'neutral'}>
            {user.kyc_verified ? (
              <>
                <ShieldCheck size={12} className="mr-1 inline" aria-hidden="true" />
                Verified
              </>
            ) : (
              'Unverified'
            )}
          </Badge>
          {user.role !== 'member' && <Badge tone="warn">{user.role}</Badge>}
          {user.status !== 'active' && <Badge tone="bad">{user.status}</Badge>}
        </div>
      </section>

      <nav
        className="flex gap-1 overflow-x-auto rounded-full bg-text-hi/[0.06] p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        aria-label="Dashboard sections"
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id as TabId)}
            aria-current={tab === t.id ? 'page' : undefined}
            className={cn(
              'inline-flex shrink-0 items-center gap-2 rounded-full px-4 py-2.5 text-sm font-semibold transition-colors',
              tab === t.id
                ? 'bg-gradient-to-br from-gold-soft to-gold text-ink shadow-[0_8px_18px_-10px_rgba(169,118,28,.6)]'
                : 'text-text-mid hover:text-text-hi',
            )}
          >
            <t.icon size={15} aria-hidden="true" />
            {t.label}
          </button>
        ))}
      </nav>

      <div className="mt-6">
        {tab === 'earnings' && (
          <div className="space-y-5">
            <Earnings />
            <InviteCard code={user.referral_code} />
          </div>
        )}
        {tab === 'links' && <ShortLinks />}
        {tab === 'profile' && <ProfileEditor />}
        {tab === 'verification' && <Verification />}
        {tab === 'privacy' && <Privacy />}
        {tab === 'experience' && <Experience />}
        {tab === 'security' && <Security />}
        {tab === 'account' && (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
            <Panel
              title="Account details"
              subtitle={
                <>
                  Your name and language are edited in{' '}
                  <button
                    type="button"
                    onClick={() => setTab('profile')}
                    className="font-semibold text-gold-soft underline-offset-2 hover:underline"
                  >
                    Profile
                  </button>
                  .
                </>
              }
            >
              <dl className="divide-y divide-[var(--cloud-border)] text-sm">
                {[
                  ['Display name', user.display_name],
                  ['Handle', `@${user.handle}`],
                  ['Email', user.email],
                  ['Language', user.lang],
                  ['Member id', user.id],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4 py-3 first:pt-0 last:pb-0">
                    <dt className="text-text-low">{label}</dt>
                    <dd className="truncate font-medium text-text-hi">{value}</dd>
                  </div>
                ))}
              </dl>
            </Panel>
            <CloseAccount />
          </div>
        )}
      </div>
    </AppShell>
  )
}
