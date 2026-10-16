import { useState } from 'react'
import { BadgeCheck, ExternalLink, FlaskConical, Lock } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type KycStatus, type OrderPayment } from '@/lib/api'
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
 */
export default function Verification() {
  const kyc = useApi<KycStatus>(() => kaluta.account.kyc(), [])
  const [starting, setStarting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [payment, setPayment] = useState<OrderPayment | null>(null)
  const [paying, setPaying] = useState(false)

  const pay = async () => {
    if (!kyc.data) return
    setPaying(true)
    setMessage(null)
    try {
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
      setMessage(`Verification ${result.status}. Attempt ${result.attempt ?? 1}.`)
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
                  {payment && !payment.mock && (
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
                <button
                  type="button"
                  onClick={start}
                  disabled={starting || kyc.data.attempts_used >= kyc.data.attempts_allowed}
                  className="rounded-full bg-gold px-5 py-2.5 text-sm font-semibold text-ink disabled:opacity-40"
                >
                  Start verification
                </button>
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
