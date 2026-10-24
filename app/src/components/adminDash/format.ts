/** Formatting and class names shared by the admin dashboard sections. */

export const usd = (value: string | number | null | undefined) =>
  `$${Number(value ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : '—')

export const BTN =
  'inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-3 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'
export const BTN_GOLD =
  'inline-flex items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40'
export const INPUT =
  'rounded-full border border-[var(--cloud-border)] bg-transparent px-3 py-1.5 text-sm text-text-hi focus:border-gold/50 focus:outline-none'

export type Msg = { ok: boolean; text: string } | null
