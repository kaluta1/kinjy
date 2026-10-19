import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { BadgeCheck, ExternalLink, FlaskConical, Lock } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type KycStatus, type OrderPayment } from '@/lib/api'
import InPagePayment from '@/components/payments/InPagePayment'
import { inPage } from '@/lib/payrail'
import { Badge, Panel, PanelState } from './primitives'

const TONE = {
  verified: 'good',
  pending: 'warn',
  rejected: 'bad',
  unverified: 'neutral',
} as const

/**
 * KinjyKYC (blueprint §20).
 *
 * Payment and verification are deliberately separate: paying the annual fee
 * does not verify anyone, and a failed check does not consume the year that was
 * paid for. The panel shows both facts independently for that reason.
 *
 * The fee is paid on Sonco Pay's page and the check is done on Kaluta KYC's;
 * the member comes back here from both. Coming back is not proof of anything,
 * so the page asks the server, which reads the answer from Sonco Pay or
 * Kaluta, and waits a little for it when it is not there yet.
 */
export default function Verification() {
  const kyc = useApi<KycStatus>(() => kaluta.account.kyc(), [])
  const [params, setParams] = useSearchParams()
  const [starting, setStarting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [payment, setPayment] = useState<OrderPayment | null>(null)
  const [paying, setPaying] = useState(false)

  // Back from Sonco Pay with ?payment=: wait for the payment to be confirmed.
  const returnedPayment = params.get('payment')
  useEffect(() => {
    if (!returnedPayment) return
    let cancelled = false
    let tries = 0
    setMessage('Checking your payment…')
    const check = async () => {
      tries += 1
      try {
        const status = await kaluta.account.paymentStatus(returnedPayment)
        if (cancelled) return
        if (status.settled) {
          setMessage('Fee paid. You can start the identity check now.')
          kyc.reload()
          return
        }
        if (status.status === 'failed' || status.status === 'expired') {
          setMessage('The payment did not go through. You were not charged; you can try again.')
          return
        }
      } catch {
        /* keep waiting: a confirmation can take a moment */
      }
      if (!cancelled && tries < 20) window.setTimeout(check, 3000)
      else if (!cancelled) setMessage('Your payment is still being confirmed. This page updates when it is.')
    }
    void check()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [returnedPayment])

  // Back from Kaluta with ?kyc=done: the verdict can take a moment.
  const backFromKyc = params.get('kyc') === 'done'
  const pendingNow = kyc.data?.status === 'pending'
  useEffect(() => {
    if (!backFromKyc || !pendingNow) return
    const id = window.setTimeout(() => kyc.reload(), 5000)
    return () => window.clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backFromKyc, pendingNow, kyc.data])

  // Once there is nothing left to wait for, the return markers leave the URL
  // so a reload does not start waiting again.
  useEffect(() => {
    if (!kyc.data) return
    if ((returnedPayment && kyc.data.fee_paid) || (backFromKyc && kyc.data.status !== 'pending')) {
      const next = new URLSearchParams(params)
      next.delete('payment')
      next.delete('kyc')
      setParams(next, { replace: true })
    }
  }, [kyc.data, returnedPayment, backFromKyc, params, setParams])

  const pay = async () => {
    if (!kyc.data) return
    setPaying(true)
    setMessage(null)
    try {
      // Paid right here, on Kinjy's page (components/payments/InPagePayment).
      setPayment(await kaluta.account.payKycFee(kyc.data.fee_usd))
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not start the payment')
    } finally {
      setPaying(false)
    }
  }

  const completeTest = async () => {
    if (!payment) return
    setPaying(true)
    try {
      await kaluta.market.settleMockPayment(payment.payment_id)
      setPayment(null)
      setMessage('Fee paid.')
      kyc.reload()
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'The test payment did not go through')
    } finally {
      setPaying(false)
    }
  }

  const start = async () => {
    setStarting(true)
    setMessage(null)
    try {
      const result = await kaluta.account.startKyc()
      if (result.verification_url && /^https:\/\//i.test(result.verification_url)) {
        // Kaluta's own flow, in this tab; it sends the member back here.
        window.location.assign(result.verification_url)
        return
      }
      setMessage(result.status === 'verified' ? 'You are already verified.' : `Verification ${result.status}.`)
      kyc.reload()
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not start verification')
    } finally {
      setStarting(false)
    }
  }

  return (
    <Panel
      title="Identity verification"
      subtitle="Required before you can take part in affiliate earnings."
      action={
        kyc.data ? <Badge tone={TONE[kyc.data.status]}>{kyc.data.status}</Badge> : undefined
      }
    >
      <PanelState loading={kyc.loading} error={kyc.error}>
        {kyc.data && (
          <>
            <dl className="grid gap-4 sm:grid-cols-3">
              <div>
                <dt className="caption">Annual fee ({kyc.data.year})</dt>
                <dd className="mono-data mt-1 text-text-hi">
                  ${kyc.data.fee_usd} {kyc.data.fee_paid ? '· paid' : '· unpaid'}
                </dd>
              </div>
              <div>
                <dt className="caption">Attempts used</dt>
                <dd className="mono-data mt-1 text-text-hi">
                  {kyc.data.attempts_used} / {kyc.data.attempts_allowed}
                </dd>
              </div>
              <div>
                <dt className="caption">Valid until</dt>
                <dd className="mono-data mt-1 text-text-hi">{kyc.data.expires_on ?? '—'}</dd>
              </div>
            </dl>

            <div className="mt-5 flex flex-wrap items-center gap-3">
              {kyc.data.status === 'verified' ? (
                <p className="flex items-center gap-2 text-sm text-emerald-200">
                  <BadgeCheck size={16} aria-hidden="true" />
                  Verified — you can receive affiliate payouts.
                </p>
              ) : !kyc.data.fee_paid ? (
                <div className="w-full space-y-3">
                  <p className="text-sm text-text-mid">
                    Pay the ${kyc.data.fee_usd} annual fee to unlock verification and to pin more than three
                    groups. Paying does not verify you on its own — the identity check still has to pass.
                  </p>
                  {!payment && (
                    <button
                      type="button"
                      onClick={pay}
                      disabled={paying}
                      className="rounded-full bg-gold px-5 py-2.5 text-sm font-semibold text-ink disabled:opacity-40"
                    >
                      {paying ? 'Starting…' : `Pay $${kyc.data.fee_usd}`}
                    </button>
                  )}
                  {payment?.mock && (
                    <div className="space-y-2 rounded-2xl border border-sky/30 bg-sky/10 p-3">
                      <p className="flex items-start gap-2 text-sm text-text-hi">
                        <FlaskConical size={16} className="mt-0.5 shrink-0 text-sky" aria-hidden="true" />
                        Test mode: no payment provider is connected, so no real money moves.
                      </p>
                      <button
                        type="button"
                        onClick={completeTest}
                        disabled={paying}
                        className="rounded-full bg-gold px-5 py-2.5 text-sm font-semibold text-ink disabled:opacity-40"
                      >
                        Complete the test payment
                      </button>
                    </div>
                  )}
                  {payment && inPage(payment.rail) && (
                    <div className="rounded-2xl border border-[var(--cloud-border)] p-4">
                      <InPagePayment
                        paymentId={payment.payment_id}
                        fallbackUrl={payment.checkout_url}
                        amount={payment.amount}
                        returnPath="/dashboard?tab=verification"
                        onPaid={() => {
                          setPayment(null)
                          setMessage('Fee paid. You can start the identity check now.')
                          kyc.reload()
                        }}
                      />
                    </div>
                  )}
                  {payment && !payment.mock && !inPage(payment.rail) && (
                    <div className="space-y-2 rounded-2xl border border-white/10 p-3 text-sm text-text-mid">
                      {payment.checkout_url && /^https?:\/\//i.test(payment.checkout_url) && (
                        <a
                          href={payment.checkout_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 rounded-full bg-gold px-5 py-2.5 font-semibold text-ink"
                        >
                          Continue to payment
                          <ExternalLink size={14} aria-hidden="true" />
                        </a>
                      )}
                      {payment.pay_address && (
                        <p>
                          Send {payment.pay_currency?.toUpperCase() ?? 'the amount'} to{' '}
                          <code className="mono-data break-all text-text-hi">{payment.pay_address}</code>
                        </p>
                      )}
                      <button type="button" onClick={kyc.reload} className="font-semibold text-gold-soft hover:underline">
                        I have paid — check again
                      </button>
                    </div>
                  )}
                </div>
              ) : (
                <div className="w-full space-y-2">
                  {kyc.data.status === 'pending' && (
                    <p className="text-sm text-text-mid">
                      Your check is open. Continue where you left off - it does not use another attempt.
                    </p>
                  )}
                  {kyc.data.status === 'rejected' && (
                    <p className="text-sm text-red-200">
                      The last check did not pass. Make sure your name and date of birth on Kinjy match your document.
                    </p>
                  )}
                  <button
                    type="button"
                    onClick={start}
                    disabled={
                      starting ||
                      (kyc.data.status !== 'pending' && kyc.data.attempts_used >= kyc.data.attempts_allowed)
                    }
                    className="inline-flex items-center gap-1.5 rounded-full bg-gold px-5 py-2.5 text-sm font-semibold text-ink disabled:opacity-40"
                  >
                    {starting ? 'Opening…' : kyc.data.status === 'pending' ? 'Continue verification' : 'Start verification'}
                    <ExternalLink size={14} aria-hidden="true" />
                  </button>
                </div>
              )}
            </div>

            {message && <p className="caption mt-3 text-text-mid">{message}</p>}

            <p className="caption mt-5 flex items-start gap-2">
              <Lock size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
              Only the verification <em>result</em> is stored on Kinjy. Your identity documents stay
              with the verification provider.
            </p>
          </>
        )}
      </PanelState>
    </Panel>
  )
}
