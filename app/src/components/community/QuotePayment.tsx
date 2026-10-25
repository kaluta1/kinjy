import { useState } from 'react'
import InPagePayment from '@/components/payments/InPagePayment'
import { ApiError, kaluta, type CommunityQuote } from '@/lib/api'
import { inPage } from '@/lib/payrail'

const usd = (value: string | number) => `$${Number(value).toFixed(2)}`

/**
 * Paying a community quote (a membership or a ticket) with the same payment
 * flow as every other Kinjy payment. The quote is the server's price: this only
 * shows how it splits and starts the payment for exactly that total.
 */
export default function QuotePayment({
  quote,
  returnPath,
  onPaid,
  onCancel,
}: {
  quote: CommunityQuote
  returnPath: string
  onPaid: () => void
  onCancel: () => void
}) {
  const [payment, setPayment] = useState<{ payment_id: string; amount: string; checkout_url?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (quote.credited) {
    return <p className="text-sm text-emerald-300">Free with your code: you are in.</p>
  }

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const started = await kaluta.communityPaid.checkout(quote.quote_id)
      if (inPage(started.rail)) {
        setPayment(started)
        return
      }
      if (started.checkout_url) {
        window.location.href = started.checkout_url
        return
      }
      setError('The payment was created; it is credited once the payment service confirms it.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the payment')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3 rounded-2xl border border-[var(--cloud-border)] p-4">
      <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
        <dt className="text-text-mid">Price set by the community</dt>
        <dd className="text-end text-text-hi">{usd(quote.owner_price)}</dd>
        <dt className="text-text-mid">Kinjy fee ({Number(quote.markup_pct)}%)</dt>
        <dd className="text-end text-text-hi">{usd(quote.markup)}</dd>
        <dt className="font-semibold text-text-hi">Total</dt>
        <dd className="text-end font-bold text-gold-soft">{usd(quote.total)}</dd>
      </dl>
      {payment ? (
        <InPagePayment paymentId={payment.payment_id} fallbackUrl={payment.checkout_url} amount={payment.amount}
          returnPath={returnPath} onPaid={onPaid} />
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => void start()} disabled={busy}
            className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-40">
            {busy ? 'Starting…' : `Pay ${usd(quote.total)}`}
          </button>
          <button type="button" onClick={onCancel}
            className="rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid">
            Back
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
      <p className="caption">This price holds for 30 minutes.</p>
    </div>
  )
}
