import { Link } from 'react-router'
import { Info, Wallet } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { kaluta, type SellerFinances as Finances } from '@/lib/api'

function Figure({ label, amount, count, unit, tone }: { label: string; amount: string; count: number; unit: string; tone?: string }) {
  return (
    <div className="cloud-card p-4">
      <p className="caption">{label}</p>
      <p className={`mono-data mt-1 text-2xl font-semibold ${tone ?? 'text-text-hi'}`}>${amount}</p>
      <p className="caption mt-1">
        {count} {unit}
      </p>
    </div>
  )
}

/** The figures the finances endpoint gives, under names that say what they are. */
export default function SellerFinances() {
  const finances = useApi<Finances>(() => kaluta.market.sellerFinances(), [])

  if (finances.loading && !finances.data) return <p className="text-sm text-text-low">Loading your earnings…</p>
  if (finances.error || !finances.data) {
    return <p role="alert" className="text-sm text-red-200">{finances.error ?? 'Could not load your earnings'}</p>
  }
  const { counts } = finances.data

  return (
    <section className="space-y-3">
      <Figure
        label="Paid, not final yet"
        amount={finances.data.escrow_pending}
        count={counts.escrow_pending}
        unit={counts.escrow_pending === 1 ? 'order not final yet' : 'orders not final yet'}
        tone="text-sky"
      />
      <Figure
        label="Settled to you"
        amount={finances.data.released}
        count={counts.settled}
        unit={counts.settled === 1 ? 'settled order' : 'settled orders'}
        tone="text-gold-soft"
      />
      <Figure
        label="Refunded"
        amount={finances.data.refunded}
        count={counts.refunded}
        unit={counts.refunded === 1 ? 'refunded order' : 'refunded orders'}
      />

      <p className="caption">
        Orders that are final but whose settlement is still pending are not in these figures; each one shows its
        settlement status under Orders.
      </p>

      <div className="cloud-card p-4">
        <p className="flex items-center gap-2 text-sm font-semibold text-text-hi">
          <Info size={15} className="text-gold" aria-hidden="true" />
          When do you get paid?
        </p>
        <p className="caption mt-2 leading-relaxed">
          The buyer’s payment is held until the purchase is final: when the buyer confirms receipt, or for an ebook
          at its first successful download. Nothing is released automatically. Settlement to you starts after that
          and shows on each order as pending, then confirmed. You receive the price you set; Kinjy’s markup is added
          on top for the buyer.
        </p>
      </div>

      <Link
        to="/payments"
        className="inline-flex min-h-[48px] w-full items-center justify-center gap-2 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink"
      >
        <Wallet size={15} aria-hidden="true" />
        Withdraw earnings
      </Link>
      <p className="caption text-center">Identity verification (KYC) is required before you can withdraw.</p>
    </section>
  )
}
