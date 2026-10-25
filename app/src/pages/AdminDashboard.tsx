import type { ReactNode } from 'react'
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router'
import {
  Activity,
  ArrowLeft,
  Dices,
  Gavel,
  LayoutDashboard,
  Layers,
  Radar,
  ScrollText,
  ShieldCheck,
  Trophy,
  UserCog,
  Users,
  UsersRound,
  Wallet,
  type LucideIcon,
} from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { useAuth } from '@/hooks/useAuth'
import {
  Disputes,
  Fraud,
  Kyc,
  Leaders,
  Ledger,
  Members,
  Moderation,
  Overview,
  Payouts,
  System,
  Team,
  CommunitiesSettings,
} from '@/components/adminDash/sections'
import PoolAdmin from '@/pages/app/PoolAdmin'
import { cn } from '@/lib/utils'

type Section = { id: string; label: string; icon: LucideIcon; render: (go: (id: string) => void) => ReactNode }

const SECTIONS: Section[] = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard, render: (go) => <Overview go={go} /> },
  { id: 'members', label: 'Members', icon: Users, render: () => <Members /> },
  { id: 'ledger', label: 'Ledger', icon: ScrollText, render: () => <Ledger /> },
  { id: 'kyc', label: 'KYC', icon: ShieldCheck, render: () => <Kyc /> },
  { id: 'fraud', label: 'Fraud', icon: Radar, render: () => <Fraud /> },
  { id: 'leaders', label: 'Kinjy Leaders', icon: Trophy, render: () => <Leaders /> },
  { id: 'referral-pool', label: 'Referral pool', icon: Dices, render: () => <PoolAdmin /> },
  { id: 'payouts', label: 'Payouts', icon: Wallet, render: () => <Payouts /> },
  { id: 'disputes', label: 'Disputes', icon: Gavel, render: () => <Disputes /> },
  { id: 'moderation', label: 'Moderation', icon: Layers, render: () => <Moderation /> },
  { id: 'team', label: 'Team', icon: UserCog, render: () => <Team /> },
  { id: 'communities', label: 'Communities', icon: UsersRound, render: () => <CommunitiesSettings /> },
  { id: 'system', label: 'System', icon: Activity, render: () => <System /> },
]
/** Older addresses that now open a section. */
const ALIASES: Record<string, string> = { wallets: 'payouts' }

/**
 * The admin dashboard: /admin and /admin/<section>.
 *
 * One menu for everything an administrator runs - the ledger, KYC, fraud
 * signals, the Leaders month, the referral pool, payouts, disputes,
 * moderation, the team and the services. Every figure is read from the
 * services as they are now; a section whose service does not answer says so
 * instead of showing a number. The server checks the role on every call; the
 * page only decides what to show.
 */
export default function AdminDashboard() {
  const { user, loading } = useAuth()
  const { frameStyle, resolved, rtl } = useAppTheme()
  const location = useLocation()
  const { section: raw = 'overview' } = useParams()
  const navigate = useNavigate()
  const id = ALIASES[raw] ?? raw
  const current = SECTIONS.find((s) => s.id === id) ?? SECTIONS[0]
  const go = (next: string) => navigate(next === 'overview' ? '/admin' : `/admin/${next}`)
  const admin = user?.role === 'admin' || user?.role === 'superadmin'

  if (loading) {
    return (
      <div className="flex min-h-[60svh] items-center justify-center" role="status" aria-label="Loading">
        <div className="h-12 w-12 rounded-full animate-orb-breathe" style={{ background: 'var(--grad-orb)' }} />
      </div>
    )
  }
  if (!user) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/join?mode=signin&next=${next}`} replace />
  }

  // A workspace of its own: no app sidebar, no modules bar, no profile card.
  // Only the admin menu, the section, and a way back to Kinjy.
  return (
    <div
      className="min-h-[100dvh]"
      style={
        resolved === 'light'
          ? { background: 'radial-gradient(circle at 70% -20%, #fff 0, #f8f5ef 48%, #f3efe7 100%)' }
          : frameStyle
      }
      dir={rtl ? 'rtl' : 'ltr'}
    >
      <header className="sticky top-0 z-30 border-b border-[var(--cloud-border)] bg-[var(--cloud)]/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1500px] items-center gap-3 px-4 lg:px-6">
          <Link to="/admin" className="flex items-baseline gap-2">
            <span className="text-lg font-bold tracking-[-0.02em] text-text-hi">Kinjy</span>
            <span className="rounded-full bg-gold/20 px-2 py-0.5 text-xs font-bold text-gold-soft">Admin</span>
          </Link>
          <span className="ms-auto hidden truncate text-sm text-text-mid sm:block">
            {user.display_name} · {user.role}
          </span>
          <Link
            to="/hub"
            className="inline-flex min-h-[40px] items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-4 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi sm:ms-0 ms-auto"
          >
            <ArrowLeft size={14} aria-hidden="true" />
            Back to Kinjy
          </Link>
        </div>
      </header>

      {!admin ? (
        <p role="alert" className="mx-auto mt-8 max-w-xl rounded-2xl bg-warning/10 px-5 py-4 text-sm text-text-hi">
          This page is for Kinjy admins. Your account does not have access.
        </p>
      ) : (
        <div className="mx-auto max-w-[1500px] px-4 py-6 lg:grid lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-8 lg:px-6">
          <nav aria-label="Admin sections" className="mb-5 lg:mb-0">
            <ul
              className="flex gap-1 overflow-x-auto rounded-full bg-text-hi/[0.06] p-1 [scrollbar-width:none] lg:sticky lg:top-20 lg:flex-col lg:overflow-visible lg:rounded-2xl lg:bg-transparent lg:p-0 [&::-webkit-scrollbar]:hidden"
            >
              {SECTIONS.map((s) => (
                <li key={s.id} className="shrink-0">
                  <button
                    type="button"
                    onClick={() => go(s.id)}
                    aria-current={s.id === current.id ? 'page' : undefined}
                    className={cn(
                      'flex w-full items-center gap-2.5 rounded-full px-3.5 py-2 text-sm font-semibold transition-colors lg:rounded-xl',
                      s.id === current.id
                        ? 'bg-gold text-ink'
                        : 'text-text-mid hover:bg-text-hi/[0.06] hover:text-text-hi',
                    )}
                  >
                    <s.icon size={15} aria-hidden="true" />
                    {s.label}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
          <main className="min-w-0">
            <h1 className="mb-5 text-[clamp(28px,4vw,40px)] font-bold leading-[1.05] tracking-[-0.035em] text-text-hi">
              {current.label}
            </h1>
            {current.render(go)}
          </main>
        </div>
      )}
    </div>
  )
}
