import { useState } from 'react'
import { Dices, Minus, Plus } from 'lucide-react'
import InPagePayment from '@/components/payments/InPagePayment'
import { PanelState, Stat } from '@/components/dashboard/primitives'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type ReferralPoolState } from '@/lib/api'
import { inPage } from '@/lib/payrail'

const usd = (value: string | number) =>
  `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

/**
 * Buying referral pool units, and where the member's units stand.
 *
 * One component for every place the pool is sold - the Earn page and the
 * pool's own service page on the market - so the price, the quantity rules
 * and the payment flow are the same wherever somebody buys.
 */
export default function ReferralPoolBuy({
  onChanged,
  returnPath = '/earn',
}: {
  /** After a purchase starts or settles, so the page can refresh what depends on it. */
  onChanged?: () => void
  /** Where the payment page sends the member back to. */
  returnPath?: string
}) {
  const [tick, setTick] = useState(0)
  const pool = useApi<ReferralPoolState>(() => kaluta.referralPool.mine(), [tick])
  const [units, setUnits] = useState(1)
  const [joining, setJoining] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [payment, setPayment] = useState<{ payment_id: string; amount: string; checkout_url?: string } | null>(null)

  const refresh = () => {
    setTick((n) => n + 1)
    onChanged?.()
  }

  const join = async () => {
    setJoining(true)
    setError(null)
    setNote(null)
    try {
      const started = await kaluta.referralPool.join(units)
      if (inPage(started.rail)) {
        // Paid right here, on Kinjy's page.
        setPayment(started)
        return
      }
      if (started.checkout_url) {
        window.location.href = started.checkout_url
        return
      }
      setNote(
        `Payment ${started.payment_id.slice(0, 14)} created for ${usd(started.amount)}. Your units are added once it settles.`,
      )
      refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the payment')
    } finally {
      setJoining(false)
    }
  }

  return (
    <PanelState loading={pool.loading && !pool.data} error={pool.error}>
      {pool.data && (
        <PoolPanel
          data={pool.data}
          units={units}
          setUnits={setUnits}
          joining={joining}
          onBuy={join}
          payment={payment}
          returnPath={returnPath}
          onPaid={() => {
            setPayment(null)
            setNote('Payment received. Your units are being added.')
            refresh()
          }}
          note={note}
          error={error}
        />
      )}
    </PanelState>
  )
}

/**
 * The pool, for one member: what a unit costs, how many to buy, and where
 * their units stand - all of them, the ones in the round being dealt, and
 * the ones that join the next round.
 */
function PoolPanel({
  data,
  units,
  setUnits,
  joining,
  onBuy,
  payment,
  onPaid,
  returnPath,
  note,
  error,
}: {
  data: ReferralPoolState
  units: number
  setUnits: (n: number) => void
  joining: boolean
  onBuy: () => void
  payment: { payment_id: string; amount: string; checkout_url?: string } | null
  onPaid: () => void
  returnPath: string
  note: string | null
  error: string | null
}) {
  const mine = data.my_seat
  const max = Math.max(1, data.max_units_per_purchase)
  const clamp = (n: number) => Math.max(1, Math.min(max, Math.floor(Number.isFinite(n) ? n : 1)))
  const total = Number(data.unit_price) * units
  const stepper =
    'grid h-10 w-10 place-items-center rounded-full border border-[var(--cloud-border)] text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'

  return (
    <>
      <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
        <Stat label="Unit price" value={usd(data.unit_price)} tone="gold" />
        <Stat label="Your active units" value={String(mine?.total_active_units ?? 0)} />
        <Stat
          label="In the current round"
          value={String(mine?.units_in_current_round ?? 0)}
          tone="muted"
          hint={mine && mine.units_in_current_round > 0 ? `${mine.round_quota_remaining} still to receive` : undefined}
        />
        <Stat label="Referrals received" value={String(mine?.referrals_received ?? 0)} tone="muted" />
      </div>
      {mine && mine.units_awaiting_next_round > 0 && (
        <p className="mt-4 rounded-2xl bg-text-hi/[0.05] px-4 py-3 text-sm text-text-mid">
          {mine.units_awaiting_next_round} unit{mine.units_awaiting_next_round === 1 ? '' : 's'} join
          {mine.units_awaiting_next_round === 1 ? 's' : ''} the next round.
          {mine.units_under_review > 0 && ` ${mine.units_under_review} under review by Kinjy.`}
        </p>
      )}
      {mine?.status && mine.status !== 'active' && (
        <p className="mt-4 rounded-2xl bg-warning/10 px-4 py-3 text-sm text-text-hi">
          Your place in the pool is {mine.status}: it is not receiving referrals at the moment.
        </p>
      )}

      {payment ? (
        <div className="mt-5 rounded-2xl border border-[var(--cloud-border)] p-4">
          <InPagePayment
            paymentId={payment.payment_id}
            fallbackUrl={payment.checkout_url}
            amount={payment.amount}
            returnPath={returnPath}
            onPaid={onPaid}
          />
        </div>
      ) : data.can_buy ? (
        <div className="mt-5 flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="pool-units" className="text-sm font-semibold text-text-hi">
              Units to buy
            </label>
            <div className="mt-2 flex items-center gap-2">
              <button type="button" className={stepper} onClick={() => setUnits(clamp(units - 1))} disabled={units <= 1}
                aria-label="One unit fewer">
                <Minus size={14} aria-hidden="true" />
              </button>
              <input
                id="pool-units"
                type="number"
                inputMode="numeric"
                min={1}
                max={max}
                step={1}
                value={units}
                onChange={(e) => setUnits(clamp(e.currentTarget.valueAsNumber))}
                className="h-10 w-20 rounded-full border border-transparent bg-text-hi/[0.07] text-center text-sm font-bold text-text-hi focus:border-gold/50 focus:outline-none"
              />
              <button type="button" className={stepper} onClick={() => setUnits(clamp(units + 1))} disabled={units >= max}
                aria-label="One unit more">
                <Plus size={14} aria-hidden="true" />
              </button>
            </div>
          </div>
          <button
            type="button"
            onClick={onBuy}
            disabled={joining}
            className="inline-flex items-center gap-2 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 py-3 text-sm font-bold text-ink disabled:opacity-40"
          >
            <Dices size={14} aria-hidden="true" />
            {joining ? 'Starting…' : `Buy ${units} unit${units === 1 ? '' : 's'} — ${usd(total)}`}
          </button>
          <p className="caption w-full">
            {units} × {usd(data.unit_price)} = {usd(total)}. Up to {data.max_units_per_purchase} units per payment; you
            can buy more later.
          </p>
        </div>
      ) : (
        <p className="mt-5 text-sm leading-relaxed text-text-mid">
          The pool is full at {data.cap.toLocaleString()} members and closed to new members. Members already in keep
          receiving referrals and can buy more units.
        </p>
      )}

      {note && <p className="mt-3 text-sm text-gold-soft">{note}</p>}
      {error && (
        <p role="alert" className="mt-3 rounded-xl bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </p>
      )}

      <p className="mt-5 text-sm leading-relaxed text-text-mid">
        More units give you a proportionally larger share of eligible pool referrals. Allocation depends on eligible
        registrations and does not guarantee a specific number of referrals within a particular period.
      </p>
      <p className="mt-2 text-sm leading-relaxed text-text-low">{data.rotation_note}</p>
    </>
  )
}
