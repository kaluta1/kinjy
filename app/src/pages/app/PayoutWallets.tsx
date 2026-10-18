import { useState } from 'react'
import { Check, ShieldCheck, X } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta, type PayoutWalletStatus } from '@/lib/api'
import { cn } from '@/lib/utils'

const FILTERS: Array<{ id: PayoutWalletStatus | 'all'; label: string }> = [
  { id: 'pending_admin', label: 'To approve' },
  { id: 'confirmed', label: 'Approved' },
  { id: 'rejected', label: 'Not approved' },
  { id: 'pending_email', label: 'Waiting for e-mail' },
  { id: 'all', label: 'All' },
]

const STATUS_LABEL: Record<PayoutWalletStatus, string> = {
  pending_email: 'Waiting for the member’s e-mail',
  pending_admin: 'Confirmed by the member',
  confirmed: 'Approved',
  rejected: 'Not approved',
}

/**
 * Admin: payout wallets to approve.
 *
 * A wallet reaches this queue once the member has confirmed it from their
 * e-mail. Approving it opens withdrawals to it; the approving admin's name is
 * kept on the wallet and shown to the member. The server refuses an admin's
 * own wallet - somebody else on the team has to approve that one.
 */
export default function PayoutWallets() {
  const { user } = useAuth()
  const [filter, setFilter] = useState<PayoutWalletStatus | 'all'>('pending_admin')
  const wallets = useApi(() => kaluta.payoutAdmin.wallets(filter), [filter])
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [busy, setBusy] = useState<number | null>(null)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const review = async (id: number, approve: boolean, name: string | null | undefined) => {
    setBusy(id)
    setMessage(null)
    try {
      const note = notes[id]?.trim() || undefined
      const result = approve ? await kaluta.payoutAdmin.approve(id, note) : await kaluta.payoutAdmin.reject(id, note)
      setMessage({
        ok: true,
        text: `${name ?? result.wallet.display_name ?? 'The member'}’s wallet is ${approve ? 'approved' : 'not approved'}. They have been notified.`,
      })
      wallets.reload()
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'That did not work' })
    } finally {
      setBusy(null)
    }
  }

  const forbidden = wallets.error && /403|access required|forbidden/i.test(wallets.error)

  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-[clamp(32px,4.5vw,48px)] font-bold leading-[1.05] tracking-[-0.04em] text-text-hi">
          Payout wallets
        </h1>
        <p className="mt-2 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          Wallets members want their commissions paid to, in USDT on BSC. Each one was confirmed from the member’s
          e-mail before it reached you. Check the address and the account, then approve or refuse.
        </p>
      </header>

      {forbidden ? (
        <p role="alert" className="rounded-2xl bg-warning/10 px-5 py-4 text-sm text-text-hi">
          This page is for Kinjy admins. Your account does not have access.
        </p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filter wallets">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                type="button"
                role="tab"
                aria-selected={filter === f.id}
                onClick={() => setFilter(f.id)}
                className={cn(
                  'rounded-full px-4 py-1.5 text-sm font-semibold',
                  filter === f.id ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid hover:bg-text-hi/[0.1]',
                )}
              >
                {f.label}
              </button>
            ))}
          </div>

          {message && (
            <p role={message.ok ? 'status' : 'alert'} className={cn('text-sm', message.ok ? 'text-emerald-200' : 'text-red-200')}>
              {message.text}
            </p>
          )}

          {wallets.loading && <p className="text-sm text-text-low">Loading…</p>}
          {wallets.error && !forbidden && <p className="text-sm text-red-200">{wallets.error}</p>}
          {wallets.data && wallets.data.items.length === 0 && (
            <p className="cloud-card p-5 text-sm text-text-low">Nothing here.</p>
          )}

          <ul className="space-y-3">
            {wallets.data?.items.map((w) => {
              const own = w.user_id === user?.id
              return (
                <li key={w.id} className="cloud-card space-y-3 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-semibold text-text-hi">{w.display_name ?? 'Member'}</p>
                      <p className="caption">
                        {w.kyc_verified ? 'Identity verified' : 'Identity not verified yet'}
                        {w.email_confirmed_at ? ` · confirmed by e-mail ${new Date(w.email_confirmed_at).toLocaleString()}` : ''}
                      </p>
                    </div>
                    <span className="rounded-full bg-text-hi/[0.07] px-2.5 py-1 text-xs font-semibold text-text-mid">
                      {STATUS_LABEL[w.status]}
                    </span>
                  </div>
                  <code className="mono-data block break-all rounded-xl bg-text-hi/[0.06] px-3 py-2 text-xs text-text-hi">
                    {w.address}
                  </code>
                  <p className="caption">
                    {w.asset} on {w.network} ·{' '}
                    <a
                      href={`https://bscscan.com/address/${w.address}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-semibold text-gold-soft hover:underline"
                    >
                      See it on BscScan
                    </a>
                  </p>
                  {w.reviewed_by_name && (
                    <p className="flex items-center gap-1.5 text-sm text-text-mid">
                      <ShieldCheck size={14} className="text-gold-soft" aria-hidden="true" />
                      {w.status === 'confirmed' ? 'Approved' : 'Refused'} by {w.reviewed_by_name}
                      {w.reviewed_at ? ` on ${new Date(w.reviewed_at).toLocaleString()}` : ''}
                      {w.review_note ? ` - ${w.review_note}` : ''}
                    </p>
                  )}
                  {w.status === 'pending_admin' &&
                    (own ? (
                      <p className="text-sm text-amber-200">This is your own wallet: another admin has to approve it.</p>
                    ) : (
                      <div className="space-y-2">
                        <input
                          value={notes[w.id] ?? ''}
                          onChange={(e) => setNotes((n) => ({ ...n, [w.id]: e.target.value }))}
                          maxLength={300}
                          placeholder="Note to the member (optional, shown if refused)"
                          className="w-full rounded-full border border-[var(--cloud-border)] bg-transparent px-4 py-2 text-sm text-text-hi focus:border-gold/50 focus:outline-none"
                        />
                        <div className="flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => review(w.id, true, w.display_name)}
                            disabled={busy === w.id}
                            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-40"
                          >
                            <Check size={14} aria-hidden="true" /> Approve
                          </button>
                          <button
                            type="button"
                            onClick={() => review(w.id, false, w.display_name)}
                            disabled={busy === w.id}
                            className="inline-flex items-center gap-1.5 rounded-full px-5 py-2 text-sm font-semibold text-red-200 hover:bg-red-400/10 disabled:opacity-40"
                          >
                            <X size={14} aria-hidden="true" /> Refuse
                          </button>
                        </div>
                      </div>
                    ))}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </AppShell>
  )
}
