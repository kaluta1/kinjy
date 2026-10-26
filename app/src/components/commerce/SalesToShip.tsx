import { useState } from 'react'
import { Link } from 'react-router'
import { Check, ChevronDown, ChevronRight, Copy, MapPin, RefreshCw } from 'lucide-react'
import { ghostBtn } from '@/components/commerce/ActionSheet'
import OrderActions from '@/components/commerce/OrderActions'
import { AcceptanceRecord } from '@/components/commerce/OrderCard'
import OrderStatusChip from '@/components/commerce/OrderStatusChip'
import { dateLabel, isService, timeSecondsLabel, useOrdersLive } from '@/components/commerce/orderStatus'
import { addressLines } from '@/components/commerce/shippingText'
import { useApi } from '@/hooks/useApi'
import { kaluta, type Order } from '@/lib/api'
import { cn } from '@/lib/utils'

function SaleCard({ order, onChanged, onNote }: { order: Order; onChanged: () => void; onNote: (m: string) => void }) {
  const [copied, setCopied] = useState(false)
  const ebook = Boolean(order.product?.ebook)
  const lines = order.shipping && !ebook ? addressLines(order.shipping) : []
  const buyer = order.counterpart

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(lines.join('\n'))
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard can be blocked; the address is on screen to copy by hand.
    }
  }

  return (
    <li className="cloud-card p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="line-clamp-2 text-sm font-semibold text-text-hi">{order.product?.title ?? 'Listing'}</p>
          <p className="caption mt-1">
            Order <span className="mono-data select-all">{order.id.slice(0, 8)}</span> · {dateLabel(order.created_at)} ·
            qty {order.quantity}
          </p>
          <p className="mt-1 text-sm text-text-mid">
            Buyer: {buyer?.display_name || 'Buyer'}
            {buyer?.handle && <span className="text-text-low"> @{buyer.handle}</span>}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="mono-data text-sm text-text-hi">${order.vendor_price}</p>
          <OrderStatusChip status={order.status} service={isService(order)} className="mt-1" />
        </div>
      </div>

      <div className="mt-3 empty:hidden">
        <AcceptanceRecord order={order} compact />
      </div>

      {lines.length > 0 && !order.cancellation && (
        <div className="mt-3 rounded-2xl bg-ink-2/60 p-3">
          <p className="caption mb-1 flex items-center gap-1.5">
            <MapPin size={12} className="text-gold" aria-hidden="true" />
            Deliver to
          </p>
          <address className="text-sm not-italic leading-snug text-text-hi">
            {lines.map((line) => (
              <span key={line} className="block">
                {line}
              </span>
            ))}
          </address>
          <button type="button" onClick={copy} className={`${ghostBtn} mt-2`}>
            {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
            {copied ? 'Copied' : 'Copy address'}
          </button>
        </div>
      )}

      {order.delivered_at && (order.carrier || order.tracking_number) && (
        <p className="caption mt-2">
          {order.carrier} {order.tracking_number && <span className="mono-data">{order.tracking_number}</span>}
        </p>
      )}

      <div className="mt-3 empty:hidden">
        <OrderActions order={order} onChanged={onChanged} onNote={onNote} />
      </div>

      <Link
        to={`/market/orders/${order.id}`}
        className="mt-2 inline-flex min-h-11 items-center gap-1 text-sm font-semibold text-text-mid hover:text-gold-soft"
      >
        Open the order
        <ChevronRight size={14} aria-hidden="true" />
      </Link>
    </li>
  )
}

function Group({
  title,
  orders,
  empty,
  folded = false,
  onChanged,
  onNote,
}: {
  title: string
  orders: Order[]
  empty?: string
  folded?: boolean
  onChanged: () => void
  onNote: (m: string) => void
}) {
  if (orders.length === 0 && !empty) return null
  const list =
    orders.length === 0 ? (
      <p className="text-sm text-text-low">{empty}</p>
    ) : (
      <ul className="space-y-3">
        {orders.map((o) => (
          <SaleCard key={o.id} order={o} onChanged={onChanged} onNote={onNote} />
        ))}
      </ul>
    )
  if (folded) {
    return (
      <details className="group">
        <summary className="mb-2 flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-semibold text-text-hi [&::-webkit-details-marker]:hidden">
          <span className="flex-1">
            {title} ({orders.length})
          </span>
          <ChevronDown size={16} className="text-text-low transition-transform group-open:rotate-180" aria-hidden="true" />
        </summary>
        {list}
      </details>
    )
  }
  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-text-hi">
        {title} ({orders.length})
      </h2>
      {list}
    </div>
  )
}

/** The seller's back office: what to hand over, what is awaited, what is final and how settlement stands. */
export default function SalesToShip() {
  const orders = useApi<{ items: Order[] }>(() => kaluta.market.myOrders({ role: 'seller' }), [])
  const [note, setNote] = useState<string | null>(null)
  const items = orders.data?.items ?? []
  const live = useOrdersLive(orders.data, items, orders.reload)

  if (orders.loading && !orders.data) return <p className="text-sm text-text-low">Loading your orders…</p>
  if (orders.error && !orders.data) return <p role="alert" className="text-sm text-red-200">{orders.error}</p>

  const open = items.filter((o) => !o.cancellation && !o.acceptance?.accepted)
  const toHandOver = open.filter((o) => o.status === 'in_escrow' && !o.delivered_at && !o.product?.ebook)
  const awaiting = open.filter((o) => o.status === 'delivered' || (o.status === 'in_escrow' && o.product?.ebook))
  const unpaid = open.filter((o) => o.status === 'pending')
  const final = items.filter((o) => o.acceptance?.accepted && o.settlement?.status !== 'confirmed')
  const settled = items.filter((o) => o.acceptance?.accepted && o.settlement?.status === 'confirmed')
  const cancelled = items.filter((o) => o.cancellation || ['refunded', 'refund_pending', 'cancelled'].includes(o.status))
  const legacy = items.filter((o) => ['disputed', 'part_refunded'].includes(o.status) && !o.acceptance?.accepted)

  const groupProps = { onChanged: orders.reload, onNote: setNote }

  return (
    <section className="space-y-6">
      <div className="flex flex-wrap items-center gap-2">
        <p className="caption flex-1">
          Last checked <span className="mono-data">{timeSecondsLabel(live.lastChecked)}</span>
          {live.polling && ' · checking every 15 s'}
        </p>
        <button
          type="button"
          onClick={live.refresh}
          disabled={orders.loading}
          className={ghostBtn}
        >
          <RefreshCw size={13} className={cn(orders.loading && 'animate-spin')} aria-hidden="true" />
          Refresh
        </button>
      </div>
      <p className="caption">Couriers: check this order on your own account before telling the courier to hand over.</p>

      {note && (
        <p role="status" className="text-sm text-gold-soft">
          {note}
        </p>
      )}

      <Group title="To ship or perform" orders={toHandOver} empty="Nothing to ship right now." {...groupProps} />
      <Group title="Awaiting buyer confirmation" orders={awaiting} {...groupProps} />
      <Group title="Final — settlement" orders={final} {...groupProps} />
      <Group title="Awaiting payment" orders={unpaid} folded {...groupProps} />
      <Group title="Settled" orders={settled} folded {...groupProps} />
      <Group title="Cancelled or refunded" orders={cancelled} folded {...groupProps} />
      <Group title="Older orders" orders={legacy} folded {...groupProps} />
    </section>
  )
}
