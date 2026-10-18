import { Link } from 'react-router'
import { ChevronRight } from 'lucide-react'
import OrderActions from '@/components/commerce/OrderActions'
import OrderStatusChip from '@/components/commerce/OrderStatusChip'
import { ProductImage } from '@/components/commerce/ProductGallery'
import { KindBadge } from '@/components/commerce/ProductCard'
import { dateLabel, nextStepHint } from '@/components/commerce/orderStatus'
import type { Order } from '@/lib/api'

export function counterpartName(order: Order): string {
  const c = order.counterpart
  return c?.display_name || ''
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
        <OrderStatusChip status={order.status} />
        <span className="caption min-w-0 flex-1">{nextStepHint(order)}</span>
      </div>

      <div className="relative z-10 mt-3 empty:hidden">
        <OrderActions order={order} onChanged={onChanged} onNote={onNote} />
      </div>
    </li>
  )
}
