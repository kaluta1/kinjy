import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { ghostBtn } from '@/components/commerce/ActionSheet'
import OrderCard, { OrderCardSkeleton } from '@/components/commerce/OrderCard'
import { timeSecondsLabel, useOrdersLive } from '@/components/commerce/orderStatus'
import { useApi } from '@/hooks/useApi'
import { kaluta, type Order } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * A member's orders (as buyer and as seller) with the actions that fit each status.
 * The card, the status wording and the actions are shared with the My orders page.
 */
export default function EscrowOrders() {
  const orders = useApi<{ items: Order[] }>(() => kaluta.market.myOrders(), [])
  const [note, setNote] = useState<string | null>(null)

  const items = orders.data?.items ?? []
  const live = useOrdersLive(orders.data, items, orders.reload)

  if (orders.loading && !orders.data) {
    return (
      <ul className="space-y-3" aria-busy="true" role="status">
        <OrderCardSkeleton />
      </ul>
    )
  }
  if (items.length === 0) {
    return <p className="cloud-card px-6 py-10 text-center text-sm text-text-low">No orders yet.</p>
  }

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <p className="caption flex-1">
          Last checked <span className="mono-data">{timeSecondsLabel(live.lastChecked)}</span>
        </p>
        <button type="button" onClick={live.refresh} disabled={orders.loading} className={ghostBtn}>
          <RefreshCw size={13} className={cn(orders.loading && 'animate-spin')} aria-hidden="true" />
          Refresh
        </button>
      </div>
      {note && <p className="mb-4 rounded-2xl bg-gold/10 px-5 py-3 text-sm text-text-hi">{note}</p>}
      <ul className="space-y-3">
        {items.map((order) => (
          <OrderCard key={order.id} order={order} onChanged={orders.reload} onNote={setNote} />
        ))}
      </ul>
    </section>
  )
}
