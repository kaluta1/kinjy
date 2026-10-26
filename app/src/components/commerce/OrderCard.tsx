import { Link } from 'react-router'
import { CheckCircle2, ChevronRight, Clock, XCircle } from 'lucide-react'
import OrderActions from '@/components/commerce/OrderActions'
import OrderStatusChip from '@/components/commerce/OrderStatusChip'
import { ProductImage } from '@/components/commerce/ProductGallery'
import { KindBadge } from '@/components/commerce/ProductCard'
import {
  acceptanceText,
  CANCEL_REASON_LABEL,
  dateLabel,
  dateTimeSecondsLabel,
  isService,
  nextStepHint,
  refundInfo,
  settlementInfo,
  TONE_CLASS,
} from '@/components/commerce/orderStatus'
import type { Order } from '@/lib/api'
import { cn } from '@/lib/utils'

function counterpartName(order: Order): string {
  const c = order.counterpart
  const name = c?.display_name || ''
  return c?.handle ? `${name}${name ? ' ' : ''}@${c.handle}` : name
}

/**
 * The verification record both sides read: accepted or still awaited, when (to
 * the second, the server's clock), the reference, what the seller may do with
 * the goods, and the settlement on its own line. Cancelled orders show the
 * cancellation and the refund instead.
 */
export function AcceptanceRecord({ order, compact = false }: { order: Order; compact?: boolean }) {
  const seller = order.role === 'seller'

  if (order.cancellation) {
    const refund = refundInfo(order)
    const c = order.cancellation
    return (
      <div className="rounded-2xl border border-white/10 bg-ink-2/60 p-3 text-sm">
        <p className="flex items-center gap-2 font-semibold text-text-hi">
          <XCircle size={15} className="shrink-0 text-text-mid" aria-hidden="true" />
          Cancelled {dateLabel(c.cancelled_at)}
        </p>
        <p className="mt-1 text-text-mid">{CANCEL_REASON_LABEL[c.reason] ?? c.reason}</p>
        {c.note && <p className="mt-1 whitespace-pre-line break-words text-text-mid">“{c.note}”</p>}
        {refund && (
          <p className={cn('mt-2 inline-flex rounded-full px-3 py-1 text-xs font-semibold', TONE_CLASS[refund.tone])}>
            {seller ? refund.label.replace('your Kinjy balance', 'the buyer’s Kinjy balance') : refund.label}
          </p>
        )}
      </div>
    )
  }

  // Nothing to verify before payment.
  if (order.status === 'pending') return null

  const a = order.acceptance
  const accepted = Boolean(a?.accepted)
  const settlement = settlementInfo(order)

  return (
    <div
      className={cn(
        'rounded-2xl border p-3 text-sm',
        accepted ? 'border-emerald-400/30 bg-emerald-400/[0.06]' : 'border-amber-400/30 bg-amber-400/[0.06]',
      )}
    >
      <p className={cn('flex items-center gap-2 font-semibold', accepted ? 'text-emerald-200' : 'text-amber-200')}>
        {accepted ? (
          <CheckCircle2 size={15} className="shrink-0" aria-hidden="true" />
        ) : (
          <Clock size={15} className="shrink-0" aria-hidden="true" />
        )}
        {acceptanceText(order)}
      </p>

      {accepted && (
        <dl className={cn('mt-2 grid gap-x-4 gap-y-1', !compact && 'sm:grid-cols-2')}>
          <div className="min-w-0">
            <dt className="caption">Accepted at</dt>
            <dd className="mono-data text-text-hi">{dateTimeSecondsLabel(a.accepted_at)}</dd>
          </div>
          <div className="min-w-0">
            <dt className="caption">Reference</dt>
            <dd className="mono-data select-all break-all text-text-hi">{a.reference ?? '—'}</dd>
          </div>
        </dl>
      )}

      {seller && order.handover && (
        <p className={cn('mt-2 font-semibold', accepted ? 'text-emerald-100' : 'text-amber-100')}>{order.handover}</p>
      )}

      {settlement && (
        <p className={cn('mt-2 inline-flex rounded-full px-3 py-1 text-xs font-semibold', TONE_CLASS[settlement.tone])}>
          {settlement.label}
        </p>
      )}

      {seller && !compact && (
        <p className="caption mt-2">Couriers: check this order on your own account before telling the courier to hand over.</p>
      )}
    </div>
  )
}

export function OrderCardSkeleton() {
  return (
    <li className="cloud-card animate-pulse p-4" aria-hidden="true">
      <div className="flex gap-3">
        <div className="h-[72px] w-[72px] shrink-0 rounded-xl bg-white/5" />
        <div className="flex-1 space-y-2">
          <div className="h-4 w-3/4 rounded bg-white/5" />
          <div className="h-3 w-1/2 rounded bg-white/5" />
          <div className="h-3 w-1/3 rounded bg-white/5" />
        </div>
      </div>
      <div className="mt-3 h-8 rounded-full bg-white/5" />
    </li>
  )
}

/** The whole card opens the order; the title link stretches over it and the actions sit above that layer. */
export default function OrderCard({
  order,
  onChanged,
  onNote,
}: {
  order: Order
  onChanged: () => void
  onNote?: (message: string) => void
}) {
  const product = order.product
  const title = product?.title ?? 'Listing no longer available'
  const who = counterpartName(order)
  const whoLabel = order.role === 'seller' ? 'Buyer' : 'Seller'

  return (
    <li className="cloud-card relative p-4 transition-colors hover:border-gold/30">
      <div className="flex gap-3">
        <ProductImage
          src={product?.images?.[0]}
          alt=""
          className="h-[72px] w-[72px] shrink-0 rounded-xl"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <Link
              to={`/market/orders/${order.id}`}
              className="line-clamp-2 text-sm font-semibold text-text-hi after:absolute after:inset-0 after:content-['']"
            >
              {title}
            </Link>
            <ChevronRight size={16} className="mt-0.5 shrink-0 text-text-low" aria-hidden="true" />
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
            {product && <KindBadge kind={product.kind} />}
            <span className="caption">Qty {order.quantity}</span>
            <span className="mono-data text-sm text-gold-soft">${order.customer_price}</span>
          </div>
        </div>
      </div>

      <p className="caption mt-3">
        {who && `${whoLabel}: ${who} · `}
        {dateLabel(order.created_at)}
        {Number(order.refunded_amount) > 0 && ` · $${order.refunded_amount} refunded`}
      </p>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <OrderStatusChip status={order.status} service={isService(order)} />
        <span className="caption min-w-0 flex-1">{nextStepHint(order)}</span>
      </div>

      <div className="relative z-10 mt-3 empty:hidden">
        <AcceptanceRecord order={order} compact />
      </div>

      <div className="relative z-10 mt-3 empty:hidden">
        <OrderActions order={order} onChanged={onChanged} onNote={onNote} />
      </div>
    </li>
  )
}
