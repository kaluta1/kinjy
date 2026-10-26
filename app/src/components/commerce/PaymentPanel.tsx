import { useState } from 'react'
import { CheckCircle2, Copy, ExternalLink, FlaskConical, ShieldCheck } from 'lucide-react'
import { ghostBtn, primaryBtn } from '@/components/commerce/ActionSheet'
import { ApiError, kaluta, type OrderPayment } from '@/lib/api'
import InPagePayment from '@/components/payments/InPagePayment'
import { inPage } from '@/lib/payrail'

/**
 * Pays for one order, from "Pay" to "held until the purchase is final".
 *
 * Content only, so it can sit in the checkout dialog right after the order is
 * placed or in a bottom sheet opened from the orders list. The amount is the
 * order's price: the server refuses any other, so this never invents one.
 */
export default function PaymentPanel({
  orderId,
  amount,
  onPaid,
  onDone,
}: {
  orderId: string
  /** The order's customer price, as the server sent it ("120.00"). */
  amount: string
  /** The payment went through (or the buyer says it did): refresh the order. */
  onPaid: () => void
  /** Close whatever hosts this panel once the buyer has seen the result. */
  onDone?: () => void
}) {
  const [payment, setPayment] = useState<OrderPayment | null>(null)
  const [paid, setPaid] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      setPayment(await kaluta.market.payOrder(orderId, amount))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the payment')
    } finally {
      setBusy(false)
    }
  }

  const completeTest = async () => {
    if (!payment) return
    setBusy(true)
    setError(null)
    try {
      await kaluta.market.settleMockPayment(payment.payment_id)
      setPaid(true)
      onPaid()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The test payment did not go through')
    } finally {
      setBusy(false)
    }
  }

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      /* Clipboard can be blocked; the address stays selectable on screen. */
    }
  }

  if (paid) {
    return (
      <div className="py-2 text-center" role="status">
        <CheckCircle2 size={36} className="mx-auto text-gold-soft" aria-hidden="true" />
        <p className="mt-3 text-sm font-semibold text-text-hi">Payment received</p>
        <p className="mt-1 text-sm text-text-mid">
          ${amount} is held. It reaches the seller only when the purchase is final: your confirmation of receipt, or
          for an ebook your first successful download.
        </p>
        {onDone && (
          <button type="button" onClick={onDone} className={`${primaryBtn} mt-4`}>
            Done
          </button>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <p className="flex items-start gap-2 text-sm text-text-mid">
        <ShieldCheck size={16} className="mt-0.5 shrink-0 text-gold" aria-hidden="true" />
        <span>
          You pay <span className="mono-data text-text-hi">${amount}</span> through Sonco Pay. It is held until the
          purchase is final, not paid to the seller straight away.
        </span>
      </p>

      {!payment && (
        <button type="button" onClick={start} disabled={busy} className={primaryBtn}>
          {busy ? 'Starting…' : `Pay $${amount}`}
        </button>
      )}

      {payment?.mock && (
        <div className="space-y-3 rounded-2xl border border-sky/30 bg-sky/10 p-3">
          <p className="flex items-start gap-2 text-sm text-text-hi">
            <FlaskConical size={16} className="mt-0.5 shrink-0 text-sky" aria-hidden="true" />
            <span>Test mode: no payment provider is connected, so no real money moves.</span>
          </p>
          <button type="button" onClick={completeTest} disabled={busy} className={primaryBtn}>
            {busy ? 'Completing…' : 'Complete the test payment'}
          </button>
        </div>
      )}

      {payment && inPage(payment.rail) && (
        <InPagePayment
          paymentId={payment.payment_id}
          fallbackUrl={payment.checkout_url}
          amount={payment.amount}
          returnPath={`/market/orders/${orderId}`}
          onPaid={() => {
            setPaid(true)
            onPaid()
          }}
        />
      )}

      {payment && !payment.mock && !inPage(payment.rail) && (
        <div className="space-y-3 rounded-2xl border border-white/10 bg-ink-2/40 p-3">
          {payment.checkout_url && /^https?:\/\//i.test(payment.checkout_url) && (
            <a
              href={payment.checkout_url}
              target="_blank"
              rel="noopener noreferrer"
              className={primaryBtn}
            >
              Continue to payment
              <ExternalLink size={14} className="ms-1.5" aria-hidden="true" />
            </a>
          )}
          {payment.pay_address && (
            <div>
              <p className="caption mb-1">
                Send {payment.pay_currency ? payment.pay_currency.toUpperCase() : 'the amount'} to this address
              </p>
              <div className="flex items-center gap-2">
                <code className="mono-data min-w-0 flex-1 break-all rounded-xl bg-black/30 px-3 py-2 text-xs text-text-hi">
                  {payment.pay_address}
                </code>
                <button
                  type="button"
                  onClick={() => copy(payment.pay_address as string)}
                  aria-label="Copy the payment address"
                  className={ghostBtn}
                >
                  <Copy size={13} aria-hidden="true" />
                  {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>
          )}
          <p className="caption">
            Confirmation can take a few minutes. Your order moves to “Paid, held” on its own once the payment is
            confirmed.
          </p>
          <button type="button" onClick={onPaid} className={ghostBtn}>
            Check my order
          </button>
        </div>
      )}

      {error && (
        <p role="alert" className="text-sm text-red-200">
          {error}
        </p>
      )}
    </div>
  )
}
