import { useState } from 'react'
import { Link } from 'react-router'
import { BadgeCheck, Crown, Dices, Info, TrendingUp, Users } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { Panel, PanelState, Stat } from '@/components/dashboard/primitives'
import { useApi } from '@/hooks/useApi'
import {
  ApiError,
  kaluta,
  type KycStatus,
  type LeaderBoard,
  type MyLeaderStanding,
  type ReferralPoolState,
} from '@/lib/api'
import { FEATURES } from '@/lib/features'
import InPagePayment from '@/components/payments/InPagePayment'
import { inPage } from '@/lib/payrail'

const usd = (value: string | number) =>
  `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

/**
 * Everything a member earns from, on one page.
 *
 * The three programmes are separate products but one question — "what am I
 * making here, and what would make it more?" — so splitting them across three
 * screens only makes people hunt.
 */
export default function Earn() {
  const standing = useApi<MyLeaderStanding>(() => kaluta.leaders.me(), [])
  // Commissions start with the member's own verification; until then they go to Kinjy.
  const kyc = useApi<KycStatus>(() => kaluta.account.kyc(), [])
  const board = useApi<LeaderBoard>(() => kaluta.leaders.standings(), [])
  const [poolTick, setPoolTick] = useState(0)
  const pool = useApi<ReferralPoolState>(() => kaluta.referralPool.mine(), [poolTick])
  const assignments = useApi(() => kaluta.referralPool.assignments(), [poolTick])

  const [joining, setJoining] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [seatPayment, setSeatPayment] = useState<{ payment_id: string; amount: string; checkout_url?: string } | null>(
    null,
  )

  const join = async () => {
    setJoining(true)
    setError(null)
    setNote(null)
    try {
      const payment = await kaluta.referralPool.join()
      if (inPage(payment.rail)) {
        // Paid right here, on Kinjy's page.
        setSeatPayment(payment)
        return
      }
      if (payment.checkout_url) {
        window.location.href = payment.checkout_url
        return
      }
      setNote(
        `Payment ${payment.payment_id.slice(0, 14)} created for ${usd(payment.amount)}. Your seat is granted once it settles.`,
      )
      setPoolTick((n) => n + 1)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the payment')
    } finally {
      setJoining(false)
    }
  }

  return (
    <AppShell>
      <header className="mb-6">
        <p className="mono-data text-[0.72rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Earning</p>
        <h1 className="mt-2 text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
          What you earn here
        </h1>
        <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          You are paid 20% of Kinjy's revenue on everything the members you sponsored do here. One level — nobody
          above you earns on them.
        </p>
      </header>

      {kyc.data && kyc.data.status !== 'verified' && (
        <div className="mb-5 flex flex-wrap items-center gap-4 rounded-card border border-gold/40 bg-gold/10 p-4">
          <BadgeCheck size={22} className="shrink-0 text-gold-soft" aria-hidden="true" />
          <p className="min-w-0 flex-1 text-sm leading-relaxed text-text-mid">
            <span className="font-semibold text-text-hi">Verify your account to start earning.</span> Until your
            verification is active, the commissions on the members you sponsored go to Kinjy.
          </p>
          <Link
            to="/dashboard?tab=verification"
            className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink"
          >
            {kyc.data.status === 'pending' ? 'See my verification' : 'Verify my account'}
          </Link>
        </div>
      )}

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
        {/* --- Kinjy Leaders ------------------------------------------- */}
        <Panel
          title="Kinjy Leaders"
          subtitle="5% of monthly company revenue, split between the 10,000 members who earned the most commission."
          className="lg:col-span-2"
        >
          <PanelState loading={standing.loading} error={standing.error}>
            {standing.data && (
              <>
                <div className="grid gap-6 sm:grid-cols-4">
                  <Stat
                    label="Your commission this month"
                    value={usd(standing.data.direct_commissions)}
                    tone="gold"
                  />
                  <Stat
                    label="Your rank"
                    value={standing.data.rank ? `#${standing.data.rank.toLocaleString()}` : '—'}
                    hint={
                      standing.data.qualifying
                        ? `of the top ${standing.data.pool_size.toLocaleString()}`
                        : 'not qualifying yet'
                    }
                  />
                  <Stat label="This month’s pool" value={usd(standing.data.pool_amount)} tone="muted" />
                  <Stat
                    label="Your projected share"
                    value={usd(standing.data.projected_payout)}
                    tone={standing.data.qualifying ? 'gold' : 'muted'}
                  />
                </div>

                <p className="mt-6 flex items-start gap-3 rounded-2xl bg-text-hi/[0.05] px-4 py-3 text-sm leading-relaxed text-text-mid">
                  <Info size={15} className="mt-0.5 shrink-0 text-gold-soft" aria-hidden="true" />
                  <span>
                    Ranked on commission earned, not on how many people you signed up — and your share is your
                    commission divided by the total of the qualifying 10,000, so it moves as the month goes on.
                    Commission that is later clawed back does not count.
                  </span>
                </p>
              </>
            )}
          </PanelState>
        </Panel>

        {/* --- the board ------------------------------------------------ */}
        <Panel
          title="This month’s leaders"
          subtitle="Live, recomputed on every load."
          action={
            board.data ? (
              <span className="mono-data text-xs text-text-low">
                {board.data.qualifying.toLocaleString()} qualifying
              </span>
            ) : undefined
          }
        >
          <PanelState
            loading={board.loading}
            error={board.error}
            empty={board.data?.items.length === 0}
            emptyLabel="No commission has been earned this month yet."
          >
            <ol className="space-y-1">
              {(board.data?.items ?? []).slice(0, 10).map((row) => (
                <li key={row.member_id} className="flex items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-text-hi/[0.05]">
                  <span className={`mono-data grid h-7 w-7 shrink-0 place-items-center rounded-full text-xs font-bold ${row.rank <= 3 ? "bg-gold/25 text-gold-soft" : "bg-text-hi/[0.07] text-text-low"}`}>{row.rank}</span>
                  <span className="min-w-0 flex-1 truncate text-sm text-text-mid">
                    {row.member_id === standing.data?.rank?.toString() ? 'You' : row.member_id.slice(0, 16)}
                  </span>
                  <span className="mono-data shrink-0 text-sm text-gold-soft">
                    {usd(row.direct_commissions)}
                  </span>
                </li>
              ))}
            </ol>
          </PanelState>
        </Panel>

        {/* --- referral pool -------------------------------------------- */}
        <Panel
          title="Referral pool"
          subtitle="Buy a seat, and sign-ups who arrive with no invitation link are assigned to seats at random."
        >
          <PanelState loading={pool.loading} error={pool.error}>
            {pool.data && (
              <>
                <div className="grid gap-6 sm:grid-cols-3">
                  <Stat label="Seat price" value={usd(pool.data.entry_price)} tone="gold" />
                  <Stat
                    label="Seats taken"
                    value={`${pool.data.seats_taken.toLocaleString()} / ${pool.data.cap.toLocaleString()}`}
                  />
                  <Stat
                    label="Sent to you"
                    value={String(pool.data.my_seat?.assigned_count ?? 0)}
                    tone="muted"
                    hint="members assigned"
                  />
                </div>

                <div className="mt-5 h-2.5 overflow-hidden rounded-full bg-text-hi/10">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-gold-soft to-gold"
                    style={{ width: `${Math.min(100, (pool.data.seats_taken / pool.data.cap) * 100)}%` }}
                  />
                </div>

                {pool.data.my_seat ? (
                  <p className="mt-5 flex items-center gap-2 rounded-2xl bg-gold/15 px-4 py-3 text-sm font-semibold text-gold-soft">
                    <Crown size={15} aria-hidden="true" />
                    You hold seat #{pool.data.my_seat.seat_number.toLocaleString()} ({pool.data.my_seat.status}).
                  </p>
                ) : seatPayment ? (
                  <div className="mt-5 rounded-2xl border border-[var(--cloud-border)] p-4">
                    <InPagePayment
                      paymentId={seatPayment.payment_id}
                      fallbackUrl={seatPayment.checkout_url}
                      amount={seatPayment.amount}
                      returnPath="/earn"
                      onPaid={() => {
                        setSeatPayment(null)
                        setNote('Payment received. Your seat is being granted.')
                        setPoolTick((n) => n + 1)
                      }}
                    />
                  </div>
                ) : pool.data.open ? (
                  <button
                    type="button"
                    onClick={join}
                    disabled={joining}
                    className="mt-5 inline-flex items-center gap-2 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 py-3 text-sm font-bold text-ink shadow-[0_8px_20px_-10px_rgba(166,120,57,0.6)] disabled:opacity-40"
                  >
                    <Dices size={14} aria-hidden="true" />
                    {joining ? 'Starting…' : `Take a seat — ${usd(pool.data.entry_price)}`}
                  </button>
                ) : (
                  <p className="mt-5 text-sm leading-relaxed text-text-mid">
                    The pool is full at {pool.data.cap.toLocaleString()} seats and closed to new members. The seats
                    already held keep receiving assignments.
                  </p>
                )}

                {note && <p className="mt-3 text-sm text-gold-soft">{note}</p>}
                {error && (
                  <p role="alert" className="mt-3 rounded-xl bg-red-500/10 px-4 py-3 text-sm text-red-200">
                    {error}
                  </p>
                )}

                <p className="mt-5 text-sm leading-relaxed text-text-low">{pool.data.note}</p>
              </>
            )}
          </PanelState>
        </Panel>

        {/* --- who the pool sent you ------------------------------------ */}
        <Panel title="Sent to you by the pool" subtitle="Assignments are random, and recorded.">
          <PanelState
            loading={assignments.loading}
            error={assignments.error}
            empty={assignments.data?.items.length === 0}
            emptyLabel="Nobody yet. Assignments happen as uninvited members sign up."
          >
            <ul className="space-y-1">
              {(assignments.data?.items ?? []).map((row) => (
                <li key={row.member_id} className="flex items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-text-hi/[0.05]">
                  <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-text-hi/[0.07] text-text-low">
                    <Users size={14} aria-hidden="true" />
                  </span>
                  <span
                    className="min-w-0 flex-1 truncate text-sm text-text-mid"
                    title={row.handle ? `@${row.handle}` : undefined}
                  >
                    {row.display_name ?? 'Member'}
                  </span>
                  <span className="caption shrink-0">
                    {new Date(row.assigned_at).toLocaleDateString(undefined, {
                      day: 'numeric',
                      month: 'short',
                    })}
                  </span>
                </li>
              ))}
            </ul>
          </PanelState>
        </Panel>

        {/* --- how it works --------------------------------------------- */}
        <Panel title="How the commission works" className="lg:col-span-2">
          <ul className="grid gap-6 md:grid-cols-3">
            {[
              // The seller example is the marketplace's (lib/features.ts); without
              // it the same rule is shown on an ad, which is Kinjy revenue in full.
              FEATURES.marketplace
                ? {
                    icon: TrendingUp,
                    title: 'Paid out of Kinjy’s revenue, not the seller’s price',
                    body: 'When Kinjy connects a buyer to a seller, the seller keeps their asking price and Kinjy adds a 20% markup on top. Your commission is 20% of that markup — $4 on a $100 listing.',
                  }
                : {
                    icon: TrendingUp,
                    title: 'Paid out of Kinjy’s revenue',
                    body: 'Your commission is 20% of what Kinjy earns on the members you sponsored — $4 on a $20 ad they buy. It never comes out of what anyone else is paid.',
                  },
              {
                icon: Users,
                title: 'One level, for as long as they are here',
                body: 'You earn on everything the members you sponsored do, for as long as they do it. Nobody above you earns on them, and you earn nothing on the members they in turn sponsor.',
              },
              {
                icon: Crown,
                title: 'Commission feeds the Leaders pool too',
                body: 'Every commission-generating transaction also sets 5% of Kinjy’s revenue aside for Kinjy Leaders. It is real money on the balance sheet before the month ends, not a promise against next month.',
              },
            ].map((row) => (
              <li key={row.title}>
                <span className="grid h-11 w-11 place-items-center rounded-[14px] bg-gold/15 text-gold-soft">
                  <row.icon size={20} aria-hidden="true" />
                </span>
                <h3 className="mt-4 text-[1.05rem] font-bold leading-snug tracking-[-0.015em] text-text-hi">{row.title}</h3>
                <p className="mt-2 text-[0.92rem] leading-relaxed text-text-mid">{row.body}</p>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </AppShell>
  )
}
