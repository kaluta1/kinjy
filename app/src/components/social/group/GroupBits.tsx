import { useState, type ReactNode } from 'react'
import { ChevronDown, Flag, X } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { ApiError, kaluta, type ReportReason } from '@/lib/api'
import { REPORT_REASONS } from './groupText'
import { cn } from '@/lib/utils'

/**
 * One part of the group's information, folded away until it is wanted.
 *
 * A <details>, so it opens with the keyboard and is announced as what it is,
 * and the browser keeps nothing open that the member closed.
 */
export function GroupSection({
  title,
  icon,
  open = false,
  badge,
  children,
}: {
  title: string
  icon: ReactNode
  open?: boolean
  badge?: ReactNode
  children: ReactNode
}) {
  const { tok } = useAppTheme()
  return (
    <details open={open} className="group/section mt-4 rounded-2xl border border-[var(--cloud-border)] bg-text-hi/[0.025]">
      <summary className={cn('flex min-h-[44px] cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-semibold', tok.text)}>
        <span className="shrink-0 text-gold-soft">{icon}</span>
        <span className="min-w-0 flex-1 truncate">{title}</span>
        {badge}
        <ChevronDown size={15} className="shrink-0 transition-transform group-open/section:rotate-180" aria-hidden="true" />
      </summary>
      <div className="px-4 pb-4">{children}</div>
    </details>
  )
}


/**
 * Report a message, a member or the group to Kinjy's moderators.
 *
 * Said plainly: the report goes to Kinjy, not to the group's admins, so a
 * member can report an admin without asking that admin to read it.
 */
export function ReportDialog({
  conversationId,
  target,
  onClose,
}: {
  conversationId: string
  target: { kind: 'message' | 'member' | 'group'; id: string; label: string }
  onClose: () => void
}) {
  const { tok } = useAppTheme()
  const [reason, setReason] = useState<ReportReason | null>(null)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const send = async () => {
    if (!reason) return
    setBusy(true)
    setError(null)
    try {
      await kaluta.messages.report(conversationId, {
        target_kind: target.kind,
        target_id: target.id,
        reason,
        note: note.trim() || undefined,
      })
      setDone(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not send the report')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Report ${target.label}`}
      className="fixed inset-0 z-[90] flex items-end justify-center bg-black/55 sm:items-center sm:p-6"
      onClick={onClose}
    >
      <div onClick={(e) => e.stopPropagation()} className={cn('w-full max-w-md rounded-t-3xl p-5 sm:rounded-3xl', tok.card)}>
        <div className="flex items-center gap-2">
          <Flag size={16} className="text-red-300" aria-hidden="true" />
          <h2 className={cn('flex-1 text-base font-bold', tok.text)}>Report {target.label}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className={cn('rounded-full p-1.5 hover:bg-text-hi/[0.07]', tok.mid)}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        {done ? (
          <>
            <p className={cn('mt-4 text-sm', tok.mid)}>
              Thank you. Kinjy’s moderators will look at it. The group’s admins are not told who reported it.
            </p>
            <button type="button" onClick={onClose} className="mt-4 w-full rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink">
              Close
            </button>
          </>
        ) : (
          <>
            <p className={cn('mt-2 text-xs', tok.low)}>Goes to Kinjy’s moderators, not to the group’s admins.</p>
            <div role="radiogroup" aria-label="Why" className="mt-3 space-y-1">
              {REPORT_REASONS.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  role="radio"
                  aria-checked={reason === r.id}
                  onClick={() => setReason(r.id)}
                  className={cn(
                    'flex w-full items-center rounded-xl border px-3 py-2 text-start text-sm',
                    reason === r.id ? 'border-gold/60 bg-gold/10 text-text-hi' : cn('border-[var(--cloud-border)]', tok.mid),
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={2000}
              rows={2}
              aria-label="Anything to add (optional)"
              placeholder="Anything to add (optional)"
              className={cn('mt-3 w-full resize-none rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm', tok.text)}
            />
            {error && <p role="alert" className="mt-2 text-xs text-red-200">{error}</p>}
            <button
              type="button"
              disabled={!reason || busy}
              onClick={() => void send()}
              className="mt-3 w-full rounded-full bg-red-500/90 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
            >
              {busy ? 'Sending…' : 'Send the report'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

/** A confirmation that asks for the group's name: deleting a group is final. */
export function TypeToConfirm({
  title,
  description,
  expected,
  confirmLabel,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  title: string
  description: ReactNode
  expected: string
  confirmLabel: string
  busy: boolean
  error: string | null
  onConfirm: (typed: string) => void
  onClose: () => void
}) {
  const { tok } = useAppTheme()
  const [typed, setTyped] = useState('')
  return (
    <div role="alertdialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-[95] flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className={cn('w-full max-w-md rounded-3xl p-5', tok.card)}>
        <h2 className={cn('text-base font-bold', tok.text)}>{title}</h2>
        <div className={cn('mt-2 space-y-2 text-sm leading-relaxed', tok.mid)}>{description}</div>
        <label className={cn('mt-4 block text-xs font-semibold', tok.mid)}>
          Type <span className="font-bold text-text-hi">{expected}</span> to confirm
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            autoFocus
            className={cn('mt-1.5 w-full rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm', tok.text)}
          />
        </label>
        {error && <p role="alert" className="mt-2 text-xs text-red-200">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={cn('rounded-full px-4 py-2 text-sm font-semibold', tok.mid)}>
            Cancel
          </button>
          <button
            type="button"
            disabled={busy || typed.trim() !== expected.trim()}
            onClick={() => onConfirm(typed)}
            className="rounded-full bg-red-500/90 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
