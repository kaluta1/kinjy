import { Link } from 'react-router'
import { BadgeCheck, Dices } from 'lucide-react'
import { KindBadge } from '@/components/commerce/ProductCard'

/** Words a search for the pool might use. */
const POOL_WORDS = ['referral', 'pool', 'unit', 'units', 'sponsor', 'invite', 'kinjy', 'parrain', 'filleul']

/** Whether the pool's card belongs in the market list for this search and tab. */
export function poolServiceMatches(search: string, kind: string): boolean {
  if (kind && kind !== 'service') return false
  const q = search.trim().toLowerCase()
  return !q || POOL_WORDS.some((word) => word.startsWith(q) || q.includes(word))
}

/**
 * Kinjy's own service in the market: referral pool units.
 *
 * Sold by Kinjy itself, so it is not a listing like the others - no seller,
 * no 20% agency margin, no delivery escrow. It opens the pool's own page,
 * where units are bought at their price through the pool's payment.
 */
export default function KinjyServiceCard({ unitPrice }: { unitPrice: string | null }) {
  return (
    <article className="cloud-card flex flex-col overflow-hidden ring-1 ring-gold/40">
      <Link to="/market/services/referral-pool" className="relative block flex-1">
        <div
          aria-hidden="true"
          className="grid aspect-[4/3] w-full place-items-center bg-gradient-to-br from-[#F0C878] via-[#E9A86B] to-[#C77A3C]"
        >
          <Dices size={56} strokeWidth={1.6} className="text-ink/80" />
        </div>
        <span className="absolute start-2 top-2 inline-flex items-center gap-1 rounded-full bg-ink/85 px-2.5 py-1 text-[11px] font-bold text-gold-soft shadow">
          <BadgeCheck size={11} aria-hidden="true" />
          Official · Kinjy
        </span>
        <div className="p-4 pb-2">
          <div className="flex items-start justify-between gap-2">
            <h2 className="line-clamp-2 text-sm font-semibold text-text-hi">Referral pool units</h2>
            <KindBadge kind="service" />
          </div>
          <p className="mt-1 line-clamp-2 text-xs text-text-low">
            Receive a share of the members who join Kinjy without an invitation, in proportion to your units.
          </p>
          <p className="mt-2 text-base font-bold text-gold-soft">
            {unitPrice ? `$${Number(unitPrice).toFixed(2)}` : '…'}
            <span className="ms-1 text-xs font-medium text-text-low">per unit</span>
          </p>
        </div>
      </Link>
      <div className="px-4 pb-4">
        <Link
          to="/market/services/referral-pool"
          className="inline-flex w-full items-center justify-center rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink"
        >
          See the service
        </Link>
      </div>
    </article>
  )
}
