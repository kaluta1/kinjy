import { Clock3, HandCoins, Landmark, Sparkles, Wallet as WalletIcon } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { kaluta, type CommissionsPage, type Wallet } from '@/lib/api'
import Payout from './Payout'
import { KpiCard, Panel, PanelState } from './primitives'

const usd = (value: string | number) =>
  `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const SOURCE_LABELS: Record<string, string> = {
  marketplace_order: 'Marketplace',
  service: 'Services',
  ad_purchase: 'Advertising',
  creator_revenue: 'Creator revenue',
  referral_pool_entry: 'Referral pool',
  kyc_fee: 'Verification fees',
}

/** What the member did, for one line of the list. */
const SOURCE_DONE: Record<string, string> = {
  kyc_fee: 'verified their account',
  referral_pool_entry: 'took a seat in the referral pool',
  ad_purchase: 'bought advertising',
  marketplace_order: 'completed an order',
  creator_revenue: 'earned creator revenue',
  service: 'paid for a service',
}

/**
 * Earnings — the wallet, where the commission came from, and getting paid.
 *
 * The payout conditions are shown together and always (components/dashboard/
 * Payout.tsx), not only when they fail: a member who cannot be paid should
 * know exactly which one is missing rather than watching a balance sit still.
 */
export default function Earnings() {
  const wallet = useApi<Wallet>(() => kaluta.account.wallet(), [])
  const commissions = useApi<CommissionsPage>(() => kaluta.account.commissions({ limit: 10 }), [])
  // Commission has one rate and one level, so there is nothing to break down
  // by depth any more. What a member actually wants to know is which part of
  // the platform their sponsees are spending in.
  const bySource = commissions.data?.by_source ?? {}
  const sources = Object.entries(bySource).sort((a, b) => Number(b[1]) - Number(a[1]))
  const maxSource = Math.max(1, ...sources.map(([, amount]) => Number(amount)))

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
      <section className="lg:col-span-2" aria-label="Wallet">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4">
          <h2 className="text-[1.25rem] font-bold tracking-[-0.025em] text-text-hi">Wallet</h2>
          <p className="text-sm text-text-low">Balances reconcile line-by-line to the immutable ledger.</p>
        </div>
        <PanelState loading={wallet.loading} error={wallet.error}>
          {wallet.data && (
            <div className="space-y-4">
              <div className="grid grid-cols-[repeat(2,minmax(0,1fr))] gap-3 sm:gap-4 lg:grid-cols-4">
                <KpiCard icon={WalletIcon} tone="gold" featured label="Available" value={usd(wallet.data.available)} />
                <KpiCard icon={Clock3} tone="sky" label="Pending" value={usd(wallet.data.pending)} hint="In a dispute window" />
                <KpiCard icon={Sparkles} tone="emerald" label="Lifetime earned" value={usd(wallet.data.lifetime_earned)} />
                <KpiCard icon={Landmark} tone="coral" label="Lifetime paid out" value={usd(wallet.data.lifetime_paid)} />
              </div>
            </div>
          )}
        </PanelState>
      </section>

      <Panel
        title="Where your commission came from"
        subtitle="20% of Kinjy's revenue on everything the members you sponsored do."
      >
        <PanelState
          loading={commissions.loading}
          error={commissions.error}
          empty={commissions.data?.total === 0}
          emptyLabel="No commission yet. It appears as the people you sponsored transact."
          emptyNode={
            <div className="flex flex-col items-center py-8 text-center">
              <span aria-hidden="true" className="grid h-12 w-12 place-items-center rounded-2xl bg-sky/15 text-sky">
                <HandCoins size={22} />
              </span>
              <p className="mt-3 font-bold text-text-hi">No commission yet</p>
              <p className="mt-1 max-w-xs text-sm text-text-low">It appears as the people you sponsored transact.</p>
            </div>
          }
        >
          <div className="space-y-2">
            {sources.map(([source, amount]) => (
              <div key={source} className="flex items-center gap-3">
                <span className="w-32 shrink-0 truncate text-sm capitalize text-text-low">
                  {SOURCE_LABELS[source] ?? source.replace(/_/g, ' ')}
                </span>
                <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-text-hi/10">
                  <div
                    className="h-full rounded-full bg-sky/70"
                    style={{ width: `${(Number(amount) / maxSource) * 100}%` }}
                  />
                </div>
                <span className="mono-data w-20 shrink-0 text-right text-sm text-text-mid">
                  {usd(amount)}
                </span>
              </div>
            ))}
          </div>
        </PanelState>
      </Panel>

      {/* Each commission, newest first, and whom it came from: what a
          sponsor wants to see after a member they invited pays. */}
      {(commissions.data?.items.length ?? 0) > 0 && (
        <Panel title="Your latest earnings" subtitle="Your 20% each time a member you invited pays on Kinjy." className="lg:col-span-2">
          <ul className="divide-y divide-[var(--cloud-border)]">
            {commissions.data!.items.map((item) => (
              <li key={item.id} className="flex items-center justify-between gap-3 py-2.5">
                <span className="min-w-0">
                  <span className="block truncate text-sm text-text-hi">
                    {item.role === 'creator'
                      ? 'Your content earned revenue'
                      : `${item.from_name || 'A member you invited'} ${SOURCE_DONE[item.source_kind] ?? 'paid on Kinjy'}`}
                  </span>
                  <span className="caption">{new Date(item.created_at).toLocaleString()}</span>
                </span>
                <span className="mono-data shrink-0 text-sm font-semibold text-emerald-300">+{usd(item.amount)}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Payout onChange={wallet.reload} />
    </div>
  )
}
