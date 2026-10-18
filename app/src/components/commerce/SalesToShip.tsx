import { useState } from 'react'
import { Link } from 'react-router'
import { Check, Copy, Gavel, MapPin, Truck } from 'lucide-react'
import DeliverySheet from '@/components/commerce/DeliverySheet'
import { ghostBtn } from '@/components/commerce/ActionSheet'
import { addressLines } from '@/components/commerce/shippingText'
import { useApi } from '@/hooks/useApi'
import { kaluta, type Dispute, type Sale } from '@/lib/api'

function SaleCard({ sale, disputed, onShip }: { sale: Sale; disputed: boolean; onShip?: () => void }) {
  const [copied, setCopied] = useState(false)
  const lines = sale.shipping ? addressLines(sale.shipping) : []

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
          <p className="line-clamp-2 text-sm font-semibold text-text-hi">{sale.product_title ?? 'Listing'}</p>
          <p className="caption mt-1">
            {sale.buyer.display_name ?? 'Buyer'} · qty {sale.quantity}
          </p>
        </div>
        <p className="mono-data shrink-0 text-sm text-text-hi">${sale.vendor_price}</p>
      </div>

      {lines.length > 0 && (
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

      {sale.delivered_at && (sale.carrier || sale.tracking_number) && (
        <p className="caption mt-2">
          {sale.carrier} {sale.tracking_number && <span className="mono-data">{sale.tracking_number}</span>}
        </p>
      )}

      {onShip && (
        <button
          type="button"
          onClick={onShip}
          className="mt-3 inline-flex min-h-[48px] w-full items-center justify-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink"
        >
          <Truck size={15} aria-hidden="true" />
          Mark as delivered
        </button>
      )}

      <Link
        to={`/market/orders/${sale.id}/dispute`}
        className="mt-3 inline-flex min-h-11 items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
      >
        <Gavel size={14} aria-hidden="true" />
        {disputed ? 'View dispute' : 'Open dispute'}
      </Link>
    </li>
  )
}

export default function SalesToShip() {
  const inEscrow = useApi<{ items: Sale[] }>(() => kaluta.market.sales('in_escrow'), [])
  const delivered = useApi<{ items: Sale[] }>(() => kaluta.market.sales('delivered'), [])
  // /commerce/sales carries no dispute_id; one call for the whole list beats one per card.
  const disputes = useApi<{ items: Dispute[] }>(() => kaluta.market.myDisputes(), [])
  const [shipping, setShipping] = useState<Sale | null>(null)
  const [note, setNote] = useState<string | null>(null)

  if (inEscrow.loading && !inEscrow.data) return <p className="text-sm text-text-low">Loading your sales…</p>
  if (inEscrow.error) return <p role="alert" className="text-sm text-red-200">{inEscrow.error}</p>

  const toShip = (inEscrow.data?.items ?? []).filter((s) => s.to_ship)
  const waiting = delivered.data?.items ?? []
  const disputedOrders = new Set((disputes.data?.items ?? []).map((d) => d.order_id))

  return (
    <section className="space-y-6">
      {note && <p className="text-sm text-gold-soft">{note}</p>}

      <div>
        <h2 className="mb-2 text-sm font-semibold text-text-hi">To ship ({toShip.length})</h2>
        {toShip.length === 0 ? (
          <p className="text-sm text-text-low">Nothing to ship right now.</p>
        ) : (
          <ul className="space-y-3">
            {toShip.map((sale) => (
              <SaleCard key={sale.id} sale={sale} disputed={disputedOrders.has(sale.id)} onShip={() => setShipping(sale)} />
            ))}
          </ul>
        )}
      </div>

      {waiting.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-text-hi">Delivered — waiting for the buyer ({waiting.length})</h2>
          <ul className="space-y-3">
            {waiting.map((sale) => (
              <SaleCard key={sale.id} sale={sale} disputed={disputedOrders.has(sale.id)} />
            ))}
          </ul>
        </div>
      )}

      {shipping && (
        <DeliverySheet
          open
          orderId={shipping.id}
          productId={shipping.product_id}
          onClose={() => setShipping(null)}
          onDone={() => {
            setShipping(null)
            setNote('Marked as delivered. The money is released when the buyer confirms receipt.')
            inEscrow.reload()
            delivered.reload()
          }}
        />
      )}
    </section>
  )
}
