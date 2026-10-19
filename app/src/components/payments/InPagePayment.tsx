import { useEffect, useRef, useState } from 'react'
import { Check, Copy, ExternalLink, Loader2, Smartphone, TriangleAlert } from 'lucide-react'
import { ApiError, kaluta, type PayAction, type PayChoice, type PayMethods } from '@/lib/api'
import { cn } from '@/lib/utils'

type Tab = 'mobile' | 'crypto' | 'other'


const POLL_MS = 3500

const field =
  'w-full rounded-[14px] border border-[var(--cloud-border)] bg-text-hi/[0.05] px-4 py-3 text-[0.95rem] text-text-hi focus:border-gold/60 focus:outline-none'
const primary =
  'inline-flex min-h-[48px] w-full items-center justify-center gap-2 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink disabled:opacity-40'
const ghost = 'text-sm font-semibold text-text-mid hover:text-gold-soft hover:underline'

const amountText = (value?: string | null) => {
  const n = Number(value)
  return value == null || value === '' || !Number.isFinite(n) ? '' : n.toLocaleString(undefined, { maximumFractionDigits: 8 })
}

const countryName = (code: string) => {
  try {
    return new Intl.DisplayNames([navigator.language || 'en'], { type: 'region' }).of(code) || code
  } catch {
    return code
  }
}

/**
 * Pays a Kinjy payment on Kinjy's own page, through the Sonco Pay SDK.
 *
 * The server asks Sonco Pay which methods this payment can use, passes on the
 * member's choice and returns the next step: approve on the phone, send an
 * exact amount to an address, or - for a card or an operator's page - a
 * secure page, opened automatically. This component settles nothing: it polls
 * the server, which reads the payment back from Sonco Pay and checks the
 * amount, and calls `onPaid` only on that verdict. If the methods cannot be
 * loaded, the member is sent to Sonco Pay's own page instead.
 */
export default function InPagePayment({
  paymentId,
  fallbackUrl,
  amount,
  returnPath,
  onPaid,
}: {
  paymentId: string
  /** Sonco Pay's hosted page, used only if the methods cannot be loaded. */
  fallbackUrl?: string | null
  /** "10.00", for the button. */
  amount: string
  /** Where a card or operator page sends the member back, on this site. */
  returnPath: string
  onPaid: () => void
}) {
  const [methods, setMethods] = useState<PayMethods | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [tab, setTab] = useState<Tab>('mobile')
  const [country, setCountry] = useState('')
  const [operator, setOperator] = useState('')
  const [phone, setPhone] = useState('')
  const [action, setAction] = useState<PayAction | null>(null)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState('')
  const [closed, setClosed] = useState(false)
  const done = useRef(false)
  // While the member is choosing another method, the old one is still open at
  // Sonco Pay and the poll keeps returning its step; only a new fact (a
  // deposit seen, an amount still owed) may take the screen back.
  const choosing = useRef(false)
  const onPaidRef = useRef(onPaid)
  onPaidRef.current = onPaid

  const load = () => {
    setLoadFailed(false)
    kaluta.account
      .paymentMethods(paymentId)
      .then((m) => {
        if (m.status === 'paid') {
          done.current = true
          onPaidRef.current()
          return
        }
        setMethods(m)
        const first = m.mobile_money[0]
        if (first) {
          setCountry(first.country)
          setOperator(first.code)
        }
        setTab(m.mobile_money.length ? 'mobile' : m.crypto.length ? 'crypto' : 'other')
      })
      .catch(() => {
        setLoadFailed(true)
        // Redirected automatically rather than left stuck: the hosted page
        // takes the same payment, and brings the member back here.
        if (fallbackUrl && /^https:\/\//i.test(fallbackUrl)) window.location.assign(fallbackUrl)
      })
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [paymentId])

  // The poll: the server decides, never this screen.
  useEffect(() => {
    const id = window.setInterval(async () => {
      if (done.current) return
      try {
        const status = await kaluta.account.paymentStatus(paymentId)
        if (status.settled) {
          done.current = true
          window.clearInterval(id)
          onPaidRef.current()
        } else if (status.retryable) {
          setAction(null)
          setError(status.action?.last_error || 'The payment was declined. Try again or choose another method.')
        } else if (status.status === 'failed' || status.status === 'expired') {
          done.current = true
          window.clearInterval(id)
          setClosed(true)
        } else if (status.action && status.action.type !== 'choose_method') {
          const forced = status.action.type === 'wait_for_confirmation' || Boolean(status.action.underpaid)
          if (!choosing.current || forced) {
            choosing.current = false
            setAction(status.action)
          }
        }
      } catch {
        /* the network will be tried again on the next round */
      }
    }, POLL_MS)
    return () => window.clearInterval(id)
  }, [paymentId])

  const choose = async (choice: PayChoice) => {
    choosing.current = false
    setSending(true)
    setError(null)
    try {
      const result = await kaluta.account.choosePaymentMethod(paymentId, { ...choice, return_path: returnPath })
      if (result.settled) {
        done.current = true
        onPaidRef.current()
        return
      }
      const next = result.action
      if (next && (next.type === 'redirect' || next.type === 'open_wallet') && next.url && /^https:\/\//i.test(next.url)) {
        // A card or an operator's page: go there straight away, this tab.
        // Sonco Pay sends the member back with ?payment= when it is done.
        window.location.assign(next.url)
      }
      setAction(next)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The request could not be sent. Try again.')
    } finally {
      setSending(false)
    }
  }

  const copy = async (text: string, what: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(what)
      window.setTimeout(() => setCopied(''), 1800)
    } catch {
      /* clipboard refused: the text stays selectable */
    }
  }

  const changeMethod = (
    <button
      type="button"
      onClick={() => {
        choosing.current = true
        setAction(null)
        setError(null)
      }}
      className={ghost}
    >
      Use another method
    </button>
  )
  const waiting = (
    <p className="flex items-center justify-center gap-2 text-xs font-semibold text-text-low" role="status">
      <Loader2 size={14} className="animate-spin" aria-hidden="true" />
      Waiting for the payment…
    </p>
  )

  if (closed) {
    return (
      <p role="alert" className="rounded-2xl bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
        This payment expired without being paid. Nothing was charged; start again to pay.
      </p>
    )
  }

  // --- the step after the choice ---------------------------------------------
  if (action && action.type !== 'choose_method') {
    if (action.type === 'send_crypto') {
      const sum = `${action.amount ?? ''} ${action.asset ?? ''}`.trim()
      return (
        <div className="space-y-4">
          {action.underpaid && (
            <p className="rounded-xl bg-amber-500/10 px-3 py-2 text-sm font-semibold text-amber-200">
              Part of the amount arrived. Send the rest: {sum}.
            </p>
          )}
          <div className="flex items-start gap-4">
            {action.qr_url && /^https:\/\//i.test(action.qr_url) && (
              <img src={action.qr_url} alt="" width={120} height={120} className="h-[120px] w-[120px] shrink-0 rounded-xl bg-white p-1" />
            )}
            <div className="min-w-0">
              <p className="text-sm text-text-mid">
                {action.amount_rule === 'exact' ? 'Send exactly' : 'Send at least'}
              </p>
              <button
                type="button"
                onClick={() => copy(action.amount || '', 'amount')}
                className="mono-data mt-1 text-left text-2xl font-bold text-text-hi hover:text-gold-soft"
              >
                {sum}
              </button>
              <p className="caption">{copied === 'amount' ? 'Copied' : 'Tap to copy'}</p>
            </div>
          </div>
          <div>
            <p className="text-sm text-text-mid">To this address</p>
            <button
              type="button"
              onClick={() => copy(action.address || '', 'address')}
              className="mt-1.5 flex w-full items-center gap-3 rounded-2xl border border-[var(--cloud-border)] px-4 py-3 text-left"
            >
              <span className="mono-data min-w-0 flex-1 break-all text-xs text-text-hi">{action.address}</span>
              <span className="flex shrink-0 items-center gap-1 text-xs font-semibold text-gold-soft">
                {copied === 'address' ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
                {copied === 'address' ? 'Copied' : 'Copy'}
              </span>
            </button>
          </div>
          {action.memo && (
            <div>
              <p className="text-sm text-text-mid">With this memo</p>
              <button
                type="button"
                onClick={() => copy(action.memo || '', 'memo')}
                className="mono-data mt-1.5 w-full break-all rounded-2xl border border-[var(--cloud-border)] px-4 py-3 text-left text-xs text-text-hi"
              >
                {action.memo}
              </button>
            </div>
          )}
          <p className="flex items-start gap-2 rounded-xl bg-red-400/10 px-3 py-2 text-xs font-semibold text-red-200">
            <TriangleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
            Only on {action.network_label || action.network}. Sent on another network, the money is lost.
          </p>
          {action.expires_at && (
            <p className="caption text-center">
              Valid until {new Date(action.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </p>
          )}
          {waiting}
          <div className="text-center">{changeMethod}</div>
        </div>
      )
    }

    if (action.type === 'wait_for_confirmation') {
      return (
        <div className="space-y-2 py-2 text-center">
          <p className="font-semibold text-text-hi">Payment seen on the network</p>
          <p className="text-sm text-text-mid">It is being confirmed. This takes a few minutes; you can stay here.</p>
          {waiting}
        </div>
      )
    }

    if (action.type === 'approve_on_phone') {
      return (
        <div className="space-y-3 py-2 text-center">
          <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-gold/15 text-gold-soft">
            <Smartphone size={24} aria-hidden="true" />
          </span>
          <p className="font-semibold text-text-hi">Approve the payment on your phone</p>
          <p className="text-sm text-text-mid">
            A request was sent to {action.phone}
            {action.amount ? ` for ${amountText(action.amount)} ${action.currency ?? ''}` : ''}. Enter your mobile money
            PIN on the phone to pay.
          </p>
          {action.instructions && <p className="caption">{action.instructions}</p>}
          {waiting}
          {changeMethod}
        </div>
      )
    }

    // redirect / open_wallet: opened automatically; a link in case it was blocked.
    return (
      <div className="space-y-3 py-2 text-center">
        <p className="text-sm text-text-mid">Opening the secure payment page…</p>
        {action.url && /^https:\/\//i.test(action.url) && (
          <a href={action.url} className={primary}>
            Open the payment page <ExternalLink size={14} aria-hidden="true" />
          </a>
        )}
        {waiting}
        {changeMethod}
      </div>
    )
  }

  // --- choosing the method -------------------------------------------------------
  if (loadFailed) {
    return (
      <div className="space-y-3 text-center">
        <p className="text-sm text-text-mid">The payment methods could not be loaded.</p>
        <button type="button" onClick={load} className={ghost}>
          Try again
        </button>
        {fallbackUrl && /^https:\/\//i.test(fallbackUrl) && (
          <a href={fallbackUrl} className={primary}>
            Pay on Sonco Pay <ExternalLink size={14} aria-hidden="true" />
          </a>
        )}
      </div>
    )
  }
  if (!methods) {
    return (
      <p className="flex items-center gap-2 py-3 text-sm text-text-low" role="status">
        <Loader2 size={15} className="animate-spin" aria-hidden="true" /> Loading the payment methods…
      </p>
    )
  }

  const tabs = (
    [
      { id: 'mobile', label: 'Mobile money', n: methods.mobile_money.length },
      { id: 'crypto', label: 'Crypto', n: methods.crypto.length },
      {
        id: 'other',
        label: methods.other.length === 1 ? (methods.other[0].method === 'card' ? 'Card' : 'Binance Pay') : 'Card · Binance Pay',
        n: methods.other.length,
      },
    ] as Array<{ id: Tab; label: string; n: number }>
  ).filter((t) => t.n > 0)

  if (!tabs.length) {
    return <p className="text-sm text-text-mid">No payment method is open for this payment right now.</p>
  }

  const countries = Array.from(new Set(methods.mobile_money.map((m) => m.country)))
  const operators = methods.mobile_money.filter((m) => m.country === country)
  const mm = operators.find((m) => m.code === operator) ?? operators[0] ?? null
  const mmTotal = mm ? mm.estimated_payer_total || mm.estimated_pay_amount : null
  const mmLabel = mmTotal && mm?.currency ? `${amountText(mmTotal)} ${mm.currency === 'XOF' ? 'F CFA' : mm.currency}` : `$${amount}`

  const payMobile = () => {
    if (!mm) return
    if (mm.redirect) {
      void choose({ method: 'mobile_money', code: mm.code, country: mm.country })
      return
    }
    const digits = phone.replace(/\D/g, '')
    if (digits.length < 8) {
      setError('Enter your mobile money number.')
      return
    }
    void choose({ method: 'mobile_money', code: mm.code, phone: `${mm.prefix}${digits}`, country: mm.country })
  }

  return (
    <div className="space-y-4">
      {tabs.length > 1 && (
        <div
          className="grid gap-1 rounded-2xl bg-text-hi/[0.06] p-1"
          style={{ gridTemplateColumns: `repeat(${tabs.length}, minmax(0, 1fr))` }}
          role="tablist"
        >
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              onClick={() => {
                setTab(t.id)
                setError(null)
              }}
              className={cn(
                'truncate rounded-xl px-2 py-2 text-sm font-semibold',
                tab === t.id ? 'bg-gold text-ink' : 'text-text-mid hover:text-text-hi',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {tab === 'mobile' && (
        <div className="space-y-3">
          <div className={countries.length > 1 ? 'grid gap-3 sm:grid-cols-2' : ''}>
            {countries.length > 1 && (
              <label className="block">
                <span className="mb-1 block text-sm text-text-mid">Country</span>
                <select
                  value={country}
                  onChange={(e) => {
                    setCountry(e.target.value)
                    setOperator(methods.mobile_money.find((m) => m.country === e.target.value)?.code ?? '')
                  }}
                  className={field}
                >
                  {countries.map((c) => (
                    <option key={c} value={c}>
                      {countryName(c)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="block">
              <span className="mb-1 block text-sm text-text-mid">Operator</span>
              <select value={mm?.code ?? ''} onChange={(e) => setOperator(e.target.value)} className={field}>
                {operators.map((m) => (
                  <option key={m.code} value={m.code}>
                    {m.label || m.operator}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {!mm?.redirect && (
            <label className="block">
              <span className="mb-1 block text-sm text-text-mid">Mobile money number</span>
              <div className="flex items-center gap-2 rounded-[14px] border border-[var(--cloud-border)] bg-text-hi/[0.05] px-4 focus-within:border-gold/60">
                {mm?.prefix && <span className="mono-data border-r border-[var(--cloud-border)] pr-2 text-sm text-text-mid">{mm.prefix}</span>}
                <input
                  type="tel"
                  inputMode="numeric"
                  autoComplete="tel-national"
                  value={phone}
                  maxLength={16}
                  onChange={(e) => {
                    setPhone(e.target.value.replace(/[^\d ]/g, ''))
                    if (error) setError(null)
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') payMobile()
                  }}
                  className="w-full border-0 bg-transparent py-3 text-base text-text-hi outline-none"
                  placeholder="97 00 00 00"
                />
              </div>
            </label>
          )}
          <button type="button" onClick={payMobile} disabled={sending || !mm} className={primary}>
            {sending ? 'Sending…' : `Pay ${mmLabel}`}
          </button>
          {!mm?.redirect && (
            <p className="caption text-center">You will be asked to confirm on your phone with your PIN.</p>
          )}
        </div>
      )}

      {tab === 'crypto' && (
        <div className="grid gap-2">
          {methods.crypto.map((c) => (
            <button
              key={c.asset + c.network}
              type="button"
              disabled={sending}
              onClick={() => void choose({ method: 'crypto', asset: c.asset, network: c.network })}
              className="flex items-center gap-3 rounded-2xl border border-[var(--cloud-border)] px-4 py-3 text-left transition-colors hover:border-gold/50 disabled:opacity-40"
            >
              <span className="mono-data grid h-10 w-10 shrink-0 place-items-center rounded-full bg-text-hi/[0.07] text-xs font-bold text-text-hi">
                {c.asset.slice(0, 4)}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-text-hi">{c.asset}</span>
                <span className="block truncate text-xs text-text-low">{c.network_label || c.network}</span>
              </span>
              {c.estimated_pay_amount && (
                <span className="mono-data shrink-0 text-sm text-text-mid">
                  ≈ {amountText(c.estimated_pay_amount)} {c.pay_currency || c.asset}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {tab === 'other' && (
        <div className="grid gap-2">
          {methods.other.map((o) => (
            <button
              key={o.method + (o.code ?? '')}
              type="button"
              disabled={sending}
              onClick={() => void choose(o.code ? { method: o.method, code: o.code } : { method: o.method })}
              className={primary}
            >
              Pay with {o.label || (o.method === 'card' ? 'a card' : 'Binance Pay')}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p role="alert" className="rounded-xl bg-red-400/10 px-3 py-2 text-sm font-semibold text-red-200">
          {error}
        </p>
      )}
    </div>
  )
}
