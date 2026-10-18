import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ArrowUpRight, Check, CircleDashed, ExternalLink, Loader2, MailCheck, ShieldCheck, Wallet as WalletIcon } from 'lucide-react'
import { ApiError, kaluta, type PayoutWallet, type WithdrawQuote, type Withdrawal } from '@/lib/api'
import { Badge, Panel, inputClass } from './primitives'

const usd = (value: string | number) =>
  `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

const short = (address: string) => `${address.slice(0, 6)}…${address.slice(-4)}`

const WALLET_STATE: Record<PayoutWallet['status'], { tone: 'good' | 'warn' | 'bad'; label: string }> = {
  pending_email: { tone: 'warn', label: 'Confirm by e-mail' },
  pending_admin: { tone: 'warn', label: 'Waiting for approval' },
  confirmed: { tone: 'good', label: 'Approved' },
  rejected: { tone: 'bad', label: 'Not approved' },
}

const WITHDRAWAL_STATE: Record<Withdrawal['status'], { tone: 'good' | 'warn' | 'bad' | 'neutral'; label: string }> = {
  reserved: { tone: 'warn', label: 'Sending' },
  submitted: { tone: 'warn', label: 'On its way' },
  completed: { tone: 'good', label: 'Paid' },
  failed: { tone: 'bad', label: 'Did not go through' },
}

const BLOCKERS: Record<string, string> = {
  payouts_unavailable: 'Withdrawals are not available right now.',
  kyc_required: 'Verify your identity first.',
  wallet_required: 'Add the wallet you want to be paid to.',
  wallet_pending_email: 'Confirm your wallet from the e-mail we sent you.',
  wallet_pending_admin: 'An admin is checking your wallet.',
  wallet_rejected: 'Your wallet was not approved. Save a new one.',
  below_minimum: 'Your balance is below the minimum withdrawal.',
  withdrawal_in_progress: 'A withdrawal is already on its way.',
}

const BSC_ADDRESS = /^0x[0-9a-fA-F]{40}$/

/**
 * Getting paid: the wallet, the withdraw button, and what was paid.
 *
 * The wallet has two checks before money can go to it - the member confirms
 * it from their e-mail, then an admin approves it - and both are shown, with
 * the admin's name once approved, so a member waiting knows on whom.
 * Withdrawing sends the whole available balance in USDT on BSC; the network
 * fee is shown before the click, not discovered after it.
 */
export default function Payout({ onChange }: { onChange?: () => void } = {}) {
  const [quote, setQuote] = useState<WithdrawQuote | null>(null)
  const [history, setHistory] = useState<Withdrawal[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [address, setAddress] = useState('')
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [confirming, setConfirming] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [q, h] = await Promise.all([kaluta.account.withdrawQuote(), kaluta.account.withdrawals()])
      setQuote(q)
      setHistory(h.items)
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load your payout details')
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // A withdrawal on its way is read again every 15 seconds while the page is
  // open, so "On its way" turns into "Paid" without a reload.
  const inFlight = history.some((w) => w.status === 'reserved' || w.status === 'submitted')
  useEffect(() => {
    if (!inFlight) return
    const id = window.setInterval(() => void refresh(), 15000)
    return () => window.clearInterval(id)
  }, [inFlight, refresh])

  const run = async (key: string, action: () => Promise<string>) => {
    setBusy(key)
    setNote(null)
    try {
      setNote({ ok: true, text: await action() })
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not work. Try again.' })
    } finally {
      setBusy(null)
      void refresh()
      onChange?.()
    }
  }

  const saveWallet = (event: React.FormEvent) => {
    event.preventDefault()
    const value = address.trim()
    if (!BSC_ADDRESS.test(value)) {
      setNote({ ok: false, text: 'A BSC address starts with 0x and has 40 characters after it.' })
      return
    }
    void run('save', async () => {
      const result = await kaluta.account.setPayoutWallet(value)
      setAddress('')
      setEditing(false)
      return result.changed
        ? 'We sent a confirmation link to your e-mail. The wallet is checked by an admin once you confirm it.'
        : 'That wallet is already saved.'
    })
  }

  const withdraw = () =>
    run('withdraw', async () => {
      setConfirming(false)
      const { withdrawal } = await kaluta.account.withdraw()
      if (withdrawal.status === 'failed') {
        throw new ApiError(409, withdrawal.failure_reason || 'The withdrawal did not go through. Your balance is unchanged.')
      }
      return `${usd(withdrawal.net)} ${withdrawal.asset} is on its way to ${short(withdrawal.address)}.`
    })

  const wallet = quote?.wallet ?? null
  const showForm = !wallet || wallet.status === 'rejected' || editing
  const blockers = (quote?.blocked_by ?? []).filter((b) => b !== 'below_minimum' || Number(quote?.available) > 0)

  return (
    <Panel
      title="Getting paid"
      subtitle="Commissions are paid in USDT on BNB Smart Chain (BSC), to a wallet you confirm and an admin approves."
      action={
        quote ? (
          <Badge tone={quote.eligible ? 'good' : 'warn'}>{quote.eligible ? 'Ready to withdraw' : 'Action required'}</Badge>
        ) : undefined
      }
    >
      {!quote && !loadError && (
        <p className="flex items-center gap-2 py-4 text-sm text-text-low" role="status">
          <Loader2 size={16} className="animate-spin" aria-hidden="true" /> Loading…
        </p>
      )}
      {loadError && !quote && (
        <p role="alert" className="rounded-2xl bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          {loadError}
        </p>
      )}

      {quote && (
        <div className="space-y-5">
          <ul className="space-y-2">
            {[
              {
                ok: !quote.blocked_by.includes('kyc_required'),
                label: 'Identity verified with KinjyKYC',
                to: '/dashboard?tab=verification',
              },
              { ok: wallet?.status === 'confirmed', label: 'Payout wallet approved' },
              {
                ok: Number(quote.available) >= Number(quote.minimum),
                label: `Balance of at least ${usd(quote.minimum)}`,
              },
            ].map((row) => (
              <li key={row.label} className="flex items-center gap-3 rounded-2xl bg-text-hi/[0.05] px-4 py-3 text-[0.95rem]">
                <span
                  aria-hidden="true"
                  className={`grid h-7 w-7 shrink-0 place-items-center rounded-full ${row.ok ? 'bg-emerald-400/20 text-emerald-300' : 'bg-amber-400/20 text-amber-300'}`}
                >
                  {row.ok ? <Check size={14} /> : <CircleDashed size={14} />}
                </span>
                {row.to && !row.ok ? (
                  <Link to={row.to} className="font-medium text-text-hi hover:underline">
                    {row.label}
                  </Link>
                ) : (
                  <span className={row.ok ? 'text-text-mid' : 'font-medium text-text-hi'}>{row.label}</span>
                )}
              </li>
            ))}
          </ul>

          {/* The wallet */}
          <div className="rounded-2xl border border-[var(--cloud-border)] p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="flex items-center gap-2 text-sm font-semibold text-text-hi">
                <WalletIcon size={15} className="text-gold-soft" aria-hidden="true" />
                Payout wallet
              </p>
              {wallet && <Badge tone={WALLET_STATE[wallet.status].tone}>{WALLET_STATE[wallet.status].label}</Badge>}
            </div>

            {wallet && !editing && (
              <div className="mt-3 space-y-2 text-sm">
                <code className="mono-data block break-all rounded-xl bg-text-hi/[0.06] px-3 py-2 text-xs text-text-hi">
                  {wallet.address}
                </code>
                {wallet.status === 'pending_email' && (
                  <p className="flex items-start gap-2 text-text-mid">
                    <MailCheck size={15} className="mt-0.5 shrink-0 text-gold-soft" aria-hidden="true" />
                    <span>
                      Open the link we sent to your e-mail to confirm this wallet.{' '}
                      <button
                        type="button"
                        onClick={() => run('resend', async () => {
                          await kaluta.account.resendWalletConfirmation()
                          return 'A new link is on its way.'
                        })}
                        disabled={busy === 'resend'}
                        className="font-semibold text-gold-soft hover:underline disabled:opacity-40"
                      >
                        Send it again
                      </button>
                    </span>
                  </p>
                )}
                {wallet.status === 'pending_admin' && (
                  <p className="text-text-mid">You confirmed it. An admin is checking it now; you will be notified.</p>
                )}
                {wallet.status === 'confirmed' && (
                  <p className="flex items-center gap-2 text-text-mid">
                    <ShieldCheck size={15} className="shrink-0 text-emerald-300" aria-hidden="true" />
                    Approved{wallet.reviewed_by_name ? ` by ${wallet.reviewed_by_name}` : ''}
                    {wallet.reviewed_at ? ` on ${new Date(wallet.reviewed_at).toLocaleDateString()}` : ''}.
                  </p>
                )}
                {wallet.status === 'rejected' && (
                  <p className="text-red-200">
                    Not approved{wallet.review_note ? `: ${wallet.review_note}` : '.'} Save another address below.
                  </p>
                )}
                {wallet.status !== 'rejected' && (
                  <button
                    type="button"
                    onClick={() => setEditing(true)}
                    className="text-sm font-semibold text-gold-soft hover:underline"
                  >
                    Change wallet
                  </button>
                )}
              </div>
            )}

            {showForm && (
              <form onSubmit={saveWallet} className="mt-3">
                <label className="mb-2 block text-sm text-text-mid" htmlFor="payout-address">
                  Your {quote.asset} address on {quote.network}
                </label>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input
                    id="payout-address"
                    className={inputClass}
                    value={address}
                    onChange={(e) => setAddress(e.target.value)}
                    placeholder="0x…"
                    spellCheck={false}
                    autoComplete="off"
                  />
                  <button
                    type="submit"
                    disabled={busy === 'save' || !address.trim()}
                    className="shrink-0 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 py-3 text-sm font-bold text-ink disabled:opacity-40"
                  >
                    {busy === 'save' ? 'Saving…' : 'Save'}
                  </button>
                </div>
                <p className="caption mt-2">
                  Only an address on BNB Smart Chain (BEP-20). Money sent to an address on another network is lost.
                  {editing && ' A new address is confirmed and approved again before withdrawals resume.'}
                </p>
                {editing && (
                  <button
                    type="button"
                    onClick={() => setEditing(false)}
                    className="mt-2 text-sm font-semibold text-text-mid hover:underline"
                  >
                    Keep the current wallet
                  </button>
                )}
              </form>
            )}
          </div>

          {/* Withdraw */}
          <div className="rounded-2xl bg-text-hi/[0.05] p-4">
            <dl className="grid grid-cols-3 gap-3 text-sm">
              <div>
                <dt className="caption">Available</dt>
                <dd className="mono-data mt-1 text-text-hi">{usd(quote.available)}</dd>
              </div>
              <div>
                <dt className="caption">Network fee</dt>
                <dd className="mono-data mt-1 text-text-hi">{quote.fee != null ? usd(quote.fee) : '-'}</dd>
              </div>
              <div>
                <dt className="caption">You receive</dt>
                <dd className="mono-data mt-1 text-text-hi">
                  {quote.you_receive != null && Number(quote.you_receive) > 0 ? `${usd(quote.you_receive)}` : '-'}
                </dd>
              </div>
            </dl>

            {blockers.length > 0 && (
              <p className="mt-3 text-sm font-medium text-amber-200">{BLOCKERS[blockers[0]] ?? blockers[0]}</p>
            )}
            {quote.blocked_by.includes('below_minimum') && (
              <p className="caption mt-2">The minimum withdrawal is {usd(quote.minimum)}, network fee included.</p>
            )}

            {!confirming ? (
              <button
                type="button"
                onClick={() => setConfirming(true)}
                disabled={!quote.eligible || busy === 'withdraw'}
                className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 py-3 text-sm font-bold text-ink disabled:opacity-40"
              >
                <ArrowUpRight size={15} aria-hidden="true" />
                {busy === 'withdraw' ? 'Sending…' : 'Withdraw'}
              </button>
            ) : (
              <div className="mt-4 space-y-3 rounded-2xl border border-gold/30 p-3">
                <p className="text-sm text-text-hi">
                  Send {quote.you_receive ? usd(quote.you_receive) : ''} {quote.asset} to{' '}
                  <span className="mono-data">{wallet ? short(wallet.address) : ''}</span> on {quote.network}? A payment on
                  the blockchain cannot be taken back.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={withdraw}
                    disabled={busy === 'withdraw'}
                    className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40"
                  >
                    Yes, withdraw
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(false)}
                    className="rounded-full px-5 py-2.5 text-sm font-semibold text-text-mid hover:bg-text-hi/[0.06]"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>

          {note && (
            <p role={note.ok ? 'status' : 'alert'} className={`text-sm ${note.ok ? 'text-emerald-200' : 'text-red-200'}`}>
              {note.text}
            </p>
          )}

          {history.length > 0 && (
            <details className="group rounded-2xl border border-[var(--cloud-border)]" open={inFlight || undefined}>
              <summary className="cursor-pointer list-none px-4 py-3 text-sm font-semibold text-text-hi">
                Withdrawals ({history.length})
              </summary>
              <ul className="divide-y divide-[var(--cloud-border)] border-t border-[var(--cloud-border)]">
                {history.map((w) => (
                  <li key={w.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
                    <div className="min-w-0">
                      <p className="mono-data text-text-hi">
                        {usd(w.net)} {w.asset}
                        <span className="text-text-low"> · fee {usd(w.fee)}</span>
                      </p>
                      <p className="caption">
                        {new Date(w.created_at).toLocaleString()} · {short(w.address)}
                      </p>
                      {w.status === 'failed' && w.failure_reason && (
                        <p className="caption text-red-200">{w.failure_reason} The amount went back to your balance.</p>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={WITHDRAWAL_STATE[w.status].tone}>{WITHDRAWAL_STATE[w.status].label}</Badge>
                      {w.tx_hash && /^0x[0-9a-fA-F]+$/.test(w.tx_hash) && (
                        <a
                          href={`https://bscscan.com/tx/${w.tx_hash}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label="See the transaction on BscScan"
                          className="text-gold-soft hover:text-gold"
                        >
                          <ExternalLink size={14} aria-hidden="true" />
                        </a>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Panel>
  )
}
