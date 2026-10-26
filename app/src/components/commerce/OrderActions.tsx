import { useEffect, useState } from 'react'
import { Check, CreditCard, PackageCheck, Star, Truck, XCircle } from 'lucide-react'
import ActionSheet, { fieldClass, primaryBtn } from '@/components/commerce/ActionSheet'
import DeliverySheet from '@/components/commerce/DeliverySheet'
import PaymentPanel from '@/components/commerce/PaymentPanel'
import ReviewForm from '@/components/commerce/ReviewForm'
import {
  dateTimeSecondsLabel,
  isService,
  loadMarketplacePolicy,
  policyVersionFor,
} from '@/components/commerce/orderStatus'
import { ApiError, kaluta, type MarketplacePolicy, type Order } from '@/lib/api'

const primary =
  'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-xs font-bold text-ink disabled:opacity-40'
const outline =
  'inline-flex min-h-11 items-center justify-center gap-1.5 rounded-full border border-white/12 px-4 py-2 text-xs font-semibold text-text-mid transition-colors hover:border-gold/40 hover:text-gold-soft disabled:opacity-40'

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.message : 'That did not go through'
}

/**
 * The deliberate last step of a purchase: the buyer sees exactly what they are
 * making final and ticks that it is in front of them before the button wakes up.
 */
function ConfirmReceiptSheet({
  order,
  onClose,
  onConfirmed,
}: {
  order: Order
  onClose: () => void
  onConfirmed: (accepted: Order) => void
}) {
  const [policy, setPolicy] = useState<MarketplacePolicy | null>(null)
  const [policyError, setPolicyError] = useState(false)
  const [checked, setChecked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<Order | null>(null)

  useEffect(() => {
    let live = true
    loadMarketplacePolicy()
      .then((p) => live && setPolicy(p))
      .catch(() => live && setPolicyError(true))
    return () => {
      live = false
    }
  }, [])

  const service = isService(order)
  const title = order.product?.title ?? 'This order'

  const confirm = async () => {
    if (!checked || busy) return
    setBusy(true)
    setError(null)
    try {
      const version = await policyVersionFor(order)
      const accepted = await kaluta.market.confirmDelivery(order.id, version)
      setDone(accepted)
      onConfirmed(accepted)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ActionSheet open onClose={onClose} title="Confirm receipt" description="This makes the purchase final.">
      {done ? (
        <div className="space-y-3" role="status">
          <p className="flex items-center gap-2 text-sm font-semibold text-emerald-200">
            <Check size={16} aria-hidden="true" />
            Receipt confirmed. The purchase is final.
          </p>
          <dl className="space-y-2 rounded-2xl bg-ink-2/60 p-3 text-sm">
            <div>
              <dt className="caption">Acceptance reference</dt>
              <dd className="mono-data select-all break-all text-text-hi">{done.acceptance.reference ?? '—'}</dd>
            </div>
            <div>
              <dt className="caption">Recorded by Kinjy at</dt>
              <dd className="mono-data text-text-hi">{dateTimeSecondsLabel(done.acceptance.accepted_at)}</dd>
            </div>
          </dl>
          <p className="caption">The seller sees the same reference and time on their side.</p>
          <button type="button" onClick={onClose} className={primaryBtn}>
            Done
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <dl className="space-y-1.5 rounded-2xl bg-ink-2/60 p-3 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-text-mid">Order</dt>
              <dd className="mono-data text-text-hi">{order.id.slice(0, 8)}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-text-mid">{service ? 'Service' : 'Product'}</dt>
              <dd className="min-w-0 text-right text-text-hi">{title}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="text-text-mid">Amount</dt>
              <dd className="mono-data text-gold-soft">${order.customer_price}</dd>
            </div>
          </dl>

          <div>
            <p className="text-sm font-semibold text-text-hi">What confirming means</p>
            {policy ? (
              <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm text-text-mid">
                {policy.acceptance.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : policyError ? (
              <p role="alert" className="mt-2 text-sm text-red-200">
                We could not load the marketplace policy. Try again in a moment.
              </p>
            ) : (
              <p className="caption mt-2">Loading…</p>
            )}
          </div>

          <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-2xl border border-white/10 p-3 text-sm text-text-hi">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => setChecked(e.target.checked)}
              className="mt-0.5 h-5 w-5 shrink-0 accent-gold"
            />
            <span>
              {service
                ? 'The service has been performed and I have checked it.'
                : 'I have inspected it and it is in front of me.'}
            </span>
          </label>

          {error && (
            <p role="alert" className="text-sm text-red-200">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={confirm}
            disabled={!checked || busy || !policy}
            aria-busy={busy}
            className={primaryBtn}
          >
            {busy ? 'Confirming…' : 'Confirm receipt — make it final'}
          </button>
          <button type="button" onClick={onClose} className={`${outline} w-full`}>
            Not yet
          </button>
        </div>
      )}
    </ActionSheet>
  )
}

const SELLER_REASONS = [
  { id: 'cannot_supply', label: 'I cannot supply it' },
  { id: 'refused_at_handover', label: 'The buyer refused it at handover' },
  { id: 'delivery_failed', label: 'Delivery failed' },
] as const

type SellerReason = (typeof SELLER_REASONS)[number]['id']

function SellerCancelSheet({
  order,
  onClose,
  onCancelled,
}: {
  order: Order
  onClose: () => void
  onCancelled: () => void
}) {
  const [reason, setReason] = useState<SellerReason>('cannot_supply')
  const [note, setNote] = useState('')
  const [step, setStep] = useState<'form' | 'confirm'>('form')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const paid = order.status !== 'pending'

  const cancel = async () => {
    setBusy(true)
    setError(null)
    try {
      await kaluta.market.cancelOrder(order.id, reason, note.trim())
      onCancelled()
    } catch (err) {
      setError(errorText(err))
      setStep('form')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ActionSheet open onClose={onClose} title="Cancel order" description={`Order ${order.id.slice(0, 8)}`}>
      {step === 'form' ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            setStep('confirm')
          }}
        >
          <div>
            <label htmlFor="cancel-reason" className="caption mb-1.5 block">
              Reason
            </label>
            <select
              id="cancel-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value as SellerReason)}
              className={fieldClass}
            >
              {SELLER_REASONS.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="cancel-note" className="caption mb-1.5 block">
              Note for the buyer (optional)
            </label>
            <textarea
              id="cancel-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              maxLength={500}
              className={`${fieldClass} resize-y`}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-red-200">
              {error}
            </p>
          )}
          <button type="submit" className={primaryBtn}>
            Continue
          </button>
        </form>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-text-mid">
            {paid
              ? 'The buyer’s payment goes back to their Kinjy balance. You are not paid for this order, and it cannot be undone.'
              : 'The buyer has not paid yet, so nothing is refunded. This cannot be undone.'}
          </p>
          <button type="button" onClick={cancel} disabled={busy} aria-busy={busy} className={primaryBtn}>
            {busy ? 'Cancelling…' : 'Yes, cancel this order'}
          </button>
          <button type="button" onClick={() => setStep('form')} disabled={busy} className={`${outline} w-full`}>
            Back
          </button>
        </div>
      )}
    </ActionSheet>
  )
}

/**
 * The buttons that fit an order's status, shared by the list, the detail page
 * and the home block so a rule such as "who may confirm" lives in one place.
 */
export default function OrderActions({
  order,
  onChanged,
  onNote,
}: {
  order: Order
  /** Called after anything that changes the order, so the caller can refetch. */
  onChanged: () => void
  onNote?: (message: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [paying, setPaying] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [delivering, setDelivering] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [buyerCancelAsk, setBuyerCancelAsk] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  // The server flag lags until the next fetch; this covers the moment after sending.
  const [justReviewed, setJustReviewed] = useState(false)

  const isBuyer = order.role !== 'seller'
  const service = isService(order)
  const final = Boolean(order.acceptance?.accepted)
  const open = !final && !order.cancellation && ['pending', 'in_escrow', 'delivered'].includes(order.status)
  // Only the detail endpoint knows; lists send people there to confirm.
  const canConfirm = isBuyer && order.can_confirm === true
  const canDeliver = !isBuyer && open && (order.status === 'in_escrow' || order.status === 'delivered')
  const canSellerCancel = !isBuyer && open
  const canBuyerCancel = isBuyer && order.status === 'pending' && !order.cancellation
  const reviewed = Boolean(order.reviewed) || justReviewed

  const cancelUnpaid = async () => {
    setBusy(true)
    setError(null)
    try {
      await kaluta.market.cancelOrder(order.id, 'unpaid')
      onNote?.('Order cancelled. Nothing was charged.')
      onChanged()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
      setBuyerCancelAsk(false)
    }
  }

  const hasAny =
    (isBuyer && order.status === 'pending') ||
    canConfirm ||
    canDeliver ||
    canSellerCancel ||
    (isBuyer && order.status === 'settled')
  // A refetch after confirming drops can_confirm; the sheet stays to show the reference.
  if (!hasAny && !confirming) return null

  return (
    <div>
      {error && (
        <p role="alert" className="mb-2 text-sm text-red-200">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {isBuyer && order.status === 'pending' && !order.cancellation && (
          <button type="button" onClick={() => setPaying(true)} className={primary}>
            <CreditCard size={13} aria-hidden="true" />
            Pay ${order.customer_price}
          </button>
        )}
        {canBuyerCancel &&
          (buyerCancelAsk ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <button type="button" disabled={busy} onClick={cancelUnpaid} className={outline}>
                {busy ? 'Cancelling…' : 'Yes, cancel it'}
              </button>
              <button type="button" disabled={busy} onClick={() => setBuyerCancelAsk(false)} className={outline}>
                Keep the order
              </button>
            </span>
          ) : (
            <button type="button" onClick={() => setBuyerCancelAsk(true)} className={outline}>
              <XCircle size={13} aria-hidden="true" />
              Cancel order
            </button>
          ))}
        {canConfirm && (
          <button type="button" onClick={() => setConfirming(true)} className={primary}>
            <Check size={13} aria-hidden="true" />
            Confirm receipt
          </button>
        )}
        {canDeliver && (
          <button
            type="button"
            disabled={order.status === 'delivered'}
            onClick={() => setDelivering(true)}
            className={order.status === 'delivered' ? outline : primary}
          >
            {order.status === 'delivered' ? (
              <PackageCheck size={13} aria-hidden="true" />
            ) : (
              <Truck size={13} aria-hidden="true" />
            )}
            {order.status === 'delivered'
              ? service
                ? 'Marked as performed'
                : 'Marked as shipped / ready'
              : service
                ? 'Mark the service as performed'
                : 'Mark as shipped / ready'}
          </button>
        )}
        {canSellerCancel && (
          <button type="button" onClick={() => setCancelling(true)} className={outline}>
            <XCircle size={13} aria-hidden="true" />
            Cancel order
          </button>
        )}
        {isBuyer &&
          order.status === 'settled' &&
          (reviewed ? (
            <span className="inline-flex min-h-11 items-center gap-1.5 text-xs font-semibold text-gold-soft">
              <Star size={13} className="fill-gold text-gold" aria-hidden="true" />
              Thanks for your review
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setReviewing(true)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-gold/40 px-4 py-2 text-xs font-semibold text-gold-soft"
            >
              <Star size={13} aria-hidden="true" />
              Rate this purchase
            </button>
          ))}
      </div>

      {paying && (
        <ActionSheet
          open
          onClose={() => setPaying(false)}
          title="Pay for your order"
          description={`Order ${order.id.slice(0, 12)}`}
        >
          <PaymentPanel
            orderId={order.id}
            amount={order.customer_price}
            onPaid={() => {
              onNote?.('Payment received. It is held until the purchase is final.')
              onChanged()
            }}
            onDone={() => setPaying(false)}
          />
        </ActionSheet>
      )}
      {confirming && (
        <ConfirmReceiptSheet
          order={order}
          onClose={() => setConfirming(false)}
          onConfirmed={(accepted) => {
            onNote?.(
              `Receipt confirmed. Reference ${accepted.acceptance.reference ?? ''}, recorded ${dateTimeSecondsLabel(accepted.acceptance.accepted_at)}.`,
            )
            onChanged()
          }}
        />
      )}
      {delivering && (
        <DeliverySheet
          open
          orderId={order.id}
          productId={order.product_id}
          service={service}
          onClose={() => setDelivering(false)}
          onDone={() => {
            setDelivering(false)
            onNote?.(
              service
                ? 'Marked as performed. The buyer now confirms receipt on Kinjy.'
                : 'Marked as shipped / ready. Release it only once the buyer has confirmed receipt.',
            )
            onChanged()
          }}
        />
      )}
      {cancelling && (
        <SellerCancelSheet
          order={order}
          onClose={() => setCancelling(false)}
          onCancelled={() => {
            setCancelling(false)
            onNote?.(
              order.status === 'pending'
                ? 'Order cancelled.'
                : 'Order cancelled. The buyer’s payment goes back to their Kinjy balance.',
            )
            onChanged()
          }}
        />
      )}
      {reviewing && (
        <ReviewForm
          open
          orderId={order.id}
          onClose={() => setReviewing(false)}
          onDone={(already) => {
            setJustReviewed(true)
            onNote?.(already ? 'You already reviewed this order.' : 'Thanks for your review')
            setReviewing(false)
            onChanged()
          }}
        />
      )}
    </div>
  )
}
