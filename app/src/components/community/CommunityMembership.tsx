import { useState } from 'react'
import { Check } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type CommunityEntitlement, type CommunityQuote, type CommunityTier } from '@/lib/api'
import QuotePayment from './QuotePayment'

const PERIOD: Record<string, string> = { monthly: '/ month', annual: '/ year', one_time: 'once' }
const STATUS: Record<CommunityEntitlement['status'], string> = {
  trial: 'Trial',
  active: 'Active',
  grace: 'Payment due',
  expired: 'Ended',
  cancelled: 'Cancelled',
  refunded: 'Refunded',
}
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '')

/**
 * Membership of a paid (or mixed) community, for a member: the tiers with what
 * they cost, the member's own access, renewals by hand, cancellation, receipts.
 * Nothing renews by itself: a reminder comes before the end, then the grace days.
 */
export default function CommunityMembership({
  communityId,
  slug,
  onChanged,
}: {
  communityId: string
  slug: string
  onChanged: () => void
}) {
  const tiers = useApi(() => kaluta.communityPaid.tiers(communityId), [communityId])
  const billing = useApi(() => kaluta.communityPaid.billing(communityId).catch(() => null), [communityId])
  const [quote, setQuote] = useState<CommunityQuote | null>(null)
  const [coupon, setCoupon] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [showReceipts, setShowReceipts] = useState(false)

  const refresh = () => {
    tiers.reload()
    billing.reload()
    onChanged()
  }
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true)
    setNote(null)
    try {
      await fn()
      setNote({ ok: true, text: ok })
      refresh()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    } finally {
      setBusy(false)
    }
  }
  const choose = async (tier: CommunityTier) => {
    setBusy(true)
    setNote(null)
    try {
      const q = await kaluta.communityPaid.quote(communityId, { tier_id: tier.id, coupon: coupon.trim() || undefined })
      setQuote(q)
      if (q.credited) refresh()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'Could not get a price' })
    } finally {
      setBusy(false)
    }
  }

  const data = tiers.data
  if (!data || !data.paid_enabled || (data.access_model === 'free' && data.tiers.length === 0)) return null
  const mine = data.my_entitlement
  const active = data.tiers.filter((t) => t.active)

  return (
    <section aria-label="Membership" className="cloud-card space-y-4 p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-bold text-text-hi">Membership</h2>
        <p className="caption">
          {data.access_model === 'paid' ? 'Joining needs a membership.' : 'Some spaces are for paying members.'}
        </p>
      </div>

      {mine && (
        <div className="rounded-2xl bg-text-hi/[0.05] p-4 text-sm">
          <p className="font-semibold text-text-hi">
            {STATUS[mine.status]}
            {mine.status === 'grace' && mine.grace_until && ` - access kept until ${day(mine.grace_until)}`}
            {mine.has_access && mine.period_end && mine.status !== 'grace' &&
              ` - ${mine.cancel_at_period_end ? 'ends' : 'renew by'} ${day(mine.period_end)}`}
          </p>
          <p className="mt-1 text-text-low">
            Renewals are paid by hand: you get a reminder a few days before the end, then {data.grace_days} days of grace.
          </p>
          {mine.has_access && mine.period_end && (
            <button type="button" disabled={busy} className="mt-2 text-sm font-semibold text-gold-soft hover:underline"
              onClick={() => void act(
                () => (mine.cancel_at_period_end ? kaluta.communityPaid.resume(communityId) : kaluta.communityPaid.cancel(communityId)),
                mine.cancel_at_period_end ? 'You will keep being reminded to renew.' : 'Cancelled: access stays until the end of the period.',
              )}>
              {mine.cancel_at_period_end ? 'Keep my membership' : 'Cancel at the end of the period'}
            </button>
          )}
        </div>
      )}

      {quote && !quote.credited ? (
        <QuotePayment quote={quote} returnPath={`/communities/${slug}`} onCancel={() => setQuote(null)}
          onPaid={() => {
            setQuote(null)
            setNote({ ok: true, text: 'Payment received. Your access is being opened.' })
            refresh()
          }} />
      ) : (
        <>
          <ul className="grid gap-3 sm:grid-cols-2">
            {active.map((tier) => (
              <li key={tier.id} className="flex flex-col rounded-2xl border border-[var(--cloud-border)] p-4">
                <p className="font-semibold text-text-hi">{tier.name}</p>
                <p className="mt-1 text-xl font-bold text-gold-soft">
                  ${tier.price} <span className="text-xs font-medium text-text-low">{PERIOD[tier.period]}</span>
                </p>
                {tier.intro_price && <p className="caption">First period ${tier.intro_price}</p>}
                {tier.description && <p className="mt-2 text-sm text-text-mid">{tier.description}</p>}
                {tier.benefits.length > 0 && (
                  <ul className="mt-2 space-y-1 text-sm text-text-mid">
                    {tier.benefits.map((b) => (
                      <li key={b} className="flex gap-1.5"><Check size={14} className="mt-0.5 shrink-0 text-emerald-300" aria-hidden="true" />{b}</li>
                    ))}
                  </ul>
                )}
                <div className="mt-auto flex flex-wrap gap-2 pt-3">
                  <button type="button" disabled={busy} onClick={() => void choose(tier)}
                    className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink disabled:opacity-40">
                    {mine?.has_access ? 'Renew' : 'Choose'}
                  </button>
                  {tier.trial_days > 0 && !mine && (
                    <button type="button" disabled={busy}
                      onClick={() => void act(() => kaluta.communityPaid.trial(communityId, tier.id), `Your ${tier.trial_days}-day trial has started.`)}
                      className="rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid">
                      Try {tier.trial_days} days free
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {active.length > 0 && (
            <label className="block max-w-xs text-sm">
              <span className="text-text-mid">Code (first payment)</span>
              <input value={coupon} onChange={(e) => setCoupon(e.target.value)} maxLength={32}
                className="mt-1 w-full rounded-full border border-[var(--cloud-border)] bg-transparent px-3 py-1.5 text-sm text-text-hi focus:border-gold/50 focus:outline-none" />
            </label>
          )}
          <p className="caption">Prices include Kinjy's {Number(data.markup_pct)}% fee on top of the community's price.</p>
        </>
      )}

      {note && <p role={note.ok ? 'status' : 'alert'} className={note.ok ? 'text-sm text-emerald-300' : 'text-sm text-red-300'}>{note.text}</p>}

      {(billing.data?.payments.length ?? 0) > 0 && (
        <div>
          <button type="button" aria-expanded={showReceipts} onClick={() => setShowReceipts(!showReceipts)}
            className="text-sm font-semibold text-gold-soft hover:underline">
            {showReceipts ? 'Hide receipts' : `Receipts (${billing.data!.payments.length})`}
          </button>
          {showReceipts && (
            <ul className="mt-2 divide-y divide-[var(--cloud-border)] text-sm">
              {billing.data!.payments.map((p) => (
                <li key={p.id} className="flex flex-wrap justify-between gap-2 py-2">
                  <span className="text-text-mid">
                    {day(p.created_at)} · {p.kind === 'ticket' ? 'Ticket' : 'Membership'}
                    {p.period_to ? ` until ${day(p.period_to)}` : ''}
                    {p.coupon_code ? ` · code ${p.coupon_code}` : ''}
                  </span>
                  <span className="text-text-hi">
                    ${p.total} <span className="caption">(community ${p.owner_price} + Kinjy ${p.kinjy_fee})</span>
                    {Number(p.refunded) > 0 && <span className="ms-1 text-amber-300">refunded ${p.refunded}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  )
}
