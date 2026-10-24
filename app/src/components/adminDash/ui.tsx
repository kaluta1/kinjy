import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { BTN, type Msg } from './format'

/** Shared components of the admin dashboard sections. */


export function Message({ msg }: { msg: Msg }) {
  if (!msg) return null
  return (
    <p role={msg.ok ? 'status' : 'alert'} className={cn('mb-3 text-sm', msg.ok ? 'text-emerald-300' : 'text-red-300')}>
      {msg.text}
    </p>
  )
}

export function Row({ children, className }: { children: ReactNode; className?: string }) {
  return <li className={cn('rounded-2xl border border-[var(--cloud-border)] bg-ink-2/40 p-3.5', className)}>{children}</li>
}

export function Chips<T extends string>({
  options,
  value,
  onChange,
}: {
  options: ReadonlyArray<{ id: T; label: string }>
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div className="mb-4 flex flex-wrap gap-2">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => onChange(o.id)}
          className={cn(
            'rounded-full px-3.5 py-1.5 text-xs font-semibold',
            value === o.id ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid hover:bg-text-hi/[0.1]',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** A button that asks before doing something that cannot be undone. */
export function ConfirmButton({
  label,
  question,
  onConfirm,
  danger = false,
  disabled = false,
}: {
  label: string
  question: string
  onConfirm: () => Promise<void> | void
  danger?: boolean
  disabled?: boolean
}) {
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  if (!asking) {
    return (
      <button type="button" className={BTN} disabled={disabled} onClick={() => setAsking(true)}>
        {label}
      </button>
    )
  }
  return (
    <span className="inline-flex flex-wrap items-center gap-2" role="group" aria-label={question}>
      <span className="text-xs text-text-mid">{question}</span>
      <button
        type="button"
        disabled={busy}
        className={cn(
          'rounded-full px-3 py-1.5 text-xs font-bold disabled:opacity-40',
          danger ? 'bg-red-500/80 text-white' : 'bg-gold text-ink',
        )}
        onClick={async () => {
          setBusy(true)
          try {
            await onConfirm()
          } finally {
            setBusy(false)
            setAsking(false)
          }
        }}
      >
        {busy ? '…' : 'Yes'}
      </button>
      <button type="button" className={BTN} disabled={busy} onClick={() => setAsking(false)}>
        Cancel
      </button>
    </span>
  )
}

/** A number on the overview, with what it is and where to look. */
export function Kpi({
  label,
  value,
  hint,
  tone = 'default',
  onClick,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'gold' | 'warn' | 'bad'
  onClick?: () => void
}) {
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className={cn(
        'cloud-card flex flex-col items-start p-5 text-start',
        onClick && 'transition-colors hover:border-gold/50',
      )}
    >
      <span className="caption">{label}</span>
      <span
        className={cn(
          'mt-1 text-[28px] font-bold leading-tight tracking-[-0.03em] tabular-nums',
          tone === 'gold' && 'text-gold-soft',
          tone === 'warn' && 'text-amber-300',
          tone === 'bad' && 'text-red-300',
          tone === 'default' && 'text-text-hi',
        )}
      >
        {value}
      </span>
      {hint && <span className="caption mt-1">{hint}</span>}
    </Tag>
  )
}
