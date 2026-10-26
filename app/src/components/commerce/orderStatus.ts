import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { useTopic } from '@/hooks/useRealtime'
import { kaluta, type MarketplacePolicy, type Order } from '@/lib/api'

export type Tone = 'wait' | 'good' | 'warn' | 'muted'

export const STATUS: Record<string, { label: string; tone: Tone }> = {
  pending: { label: 'Awaiting payment', tone: 'muted' },
  in_escrow: { label: 'Paid, held', tone: 'wait' },
  delivered: { label: 'Shipped / ready', tone: 'wait' },
  accepted: { label: 'Final — settlement pending', tone: 'good' },
  settled: { label: 'Settled', tone: 'good' },
  refund_pending: { label: 'Refund pending', tone: 'warn' },
  refunded: { label: 'Refunded', tone: 'warn' },
  cancelled: { label: 'Cancelled', tone: 'muted' },
  // Orders from before 27/10 only.
  disputed: { label: 'Disputed (closed process)', tone: 'warn' },
  part_refunded: { label: 'Partly refunded', tone: 'good' },
}

export const TONE_CLASS: Record<Tone, string> = {
  wait: 'bg-sky/15 text-sky',
  good: 'bg-emerald-400/15 text-emerald-200',
  warn: 'bg-amber-400/15 text-amber-200',
  muted: 'bg-text-hi/[0.07] text-text-mid',
}

export function isService(order: Pick<Order, 'product'>): boolean {
  return order.product?.kind === 'service'
}

export function statusInfo(status: string, service = false): { label: string; tone: Tone } {
  if (status === 'delivered' && service) return { label: 'Service performed', tone: 'wait' }
  return STATUS[status] ?? { label: status.replace(/_/g, ' '), tone: 'muted' }
}

/** Filter pills on the buyer's orders page; each groups the raw statuses a member thinks of as one thing. */
export const STATUS_GROUPS = [
  { id: 'pending', label: 'Awaiting payment', statuses: ['pending'] },
  { id: 'in_escrow', label: 'Paid, held', statuses: ['in_escrow'] },
  { id: 'delivered', label: 'Shipped / ready', statuses: ['delivered'] },
  { id: 'done', label: 'Final', statuses: ['accepted', 'settled', 'part_refunded'] },
  { id: 'closed', label: 'Refunded or cancelled', statuses: ['refund_pending', 'refunded', 'cancelled', 'disputed'] },
] as const

/** One line telling the viewer what happens next, so a status is never a dead end. */
export function nextStepHint(order: Pick<Order, 'status' | 'role' | 'product'>): string {
  const buyer = order.role !== 'seller'
  const service = isService(order)
  const ebook = Boolean(order.product?.ebook)
  switch (order.status) {
    case 'pending':
      return buyer ? 'Pay to place the order' : 'Waiting for the buyer to pay. Hand over nothing yet.'
    case 'in_escrow':
      if (ebook) return buyer ? 'In your library: the first successful download makes it final' : 'Paid, waiting for the first download'
      if (service) return buyer ? 'Waiting for the seller to perform the service' : 'Perform the service, then mark it as performed'
      return buyer ? 'Waiting for the seller to ship or get it ready' : 'Ship it or get it ready, then mark it'
    case 'delivered':
      return buyer
        ? 'Check it, then confirm receipt on the order page'
        : 'Waiting for the buyer to confirm receipt. Do not hand over until confirmed.'
    case 'accepted':
      return buyer ? 'The purchase is final' : 'The purchase is final. Settlement is pending.'
    case 'settled':
      return buyer ? 'The purchase is final' : 'The settlement to you is confirmed'
    case 'refund_pending':
      return buyer ? 'Your payment is on its way back to your Kinjy balance' : 'The buyer is being refunded'
    case 'refunded':
      return buyer ? 'Your payment was returned' : 'The buyer was refunded'
    case 'part_refunded':
      return 'Part of the payment was refunded, the rest went to the seller'
    case 'disputed':
      return 'Opened under the old dispute process'
    case 'cancelled':
      return 'This order was cancelled'
    default:
      return ''
  }
}

/** Accepted, or awaiting: the line both back offices show. */
export function acceptanceText(order: Pick<Order, 'acceptance' | 'final_when' | 'product'>): string {
  const a = order.acceptance
  if (a?.status === 'buyer_confirmed') return 'Buyer confirmed receipt'
  if (a?.status === 'first_download') return 'First successful download'
  const ebook = order.final_when === 'first_download' || Boolean(order.product?.ebook)
  return ebook ? 'Awaiting the first successful download' : 'Awaiting buyer confirmation'
}

/** Settlement is separate from acceptance: never "paid" just because the buyer confirmed. */
export function settlementInfo(order: Pick<Order, 'acceptance' | 'settlement'>): { label: string; tone: Tone } | null {
  const s = order.settlement?.status ?? 'none'
  if (s === 'confirmed') return { label: 'Settlement confirmed', tone: 'good' }
  if (s === 'action_required') return { label: 'Settlement needs Kinjy support', tone: 'warn' }
  if (order.acceptance?.accepted) return { label: 'Settlement pending', tone: 'wait' }
  return null
}

export function refundInfo(order: Pick<Order, 'cancellation'>): { label: string; tone: Tone } | null {
  switch (order.cancellation?.refund_status) {
    case 'pending':
      return { label: 'Refund pending', tone: 'wait' }
    case 'confirmed':
      return { label: 'Refunded to your Kinjy balance', tone: 'good' }
    case 'action_required':
      return { label: 'Refund needs Kinjy support', tone: 'warn' }
    default:
      return null
  }
}

export const CANCEL_REASON_LABEL: Record<string, string> = {
  cannot_supply: 'The seller cannot supply it',
  refused_at_handover: 'The buyer refused it at handover',
  delivery_failed: 'Delivery failed',
  unpaid: 'Cancelled before payment',
  operational: 'Cancelled by Kinjy (operational correction)',
}

export function dateLabel(iso: string | null | undefined): string {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function dateTimeLabel(iso: string | null | undefined): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** The acceptance time is evidence: show it to the second. */
export function dateTimeSecondsLabel(iso: string | null | undefined): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function timeSecondsLabel(date: Date): string {
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function money(value: string | number): string {
  return `$${value}`
}

// One fetch of the marketplace policy per page load; a failure is retried next time.
let policyRequest: Promise<MarketplacePolicy> | null = null

export function loadMarketplacePolicy(): Promise<MarketplacePolicy> {
  if (!policyRequest) {
    policyRequest = kaluta.market.marketplacePolicy().catch((err: unknown) => {
      policyRequest = null
      throw err
    })
  }
  return policyRequest
}

/** The policy version an acceptance is given under: the order's own, else the current policy. */
export async function policyVersionFor(order: Pick<Order, 'acceptance'>): Promise<string> {
  return order.acceptance?.policy_version || (await loadMarketplacePolicy()).version
}

const LIVE_KINDS = new Set(['order_accepted', 'order_settled', 'order_refunded', 'order_cancelled', 'order_funded'])

/** Something on these orders can still change without the viewer doing anything. */
export function awaitingSomething(orders: Order[]): boolean {
  return orders.some((o) => {
    if (o.cancellation) return o.cancellation.refund_status === 'pending'
    if (!o.acceptance?.accepted) return ['in_escrow', 'delivered'].includes(o.status)
    return (o.settlement?.status ?? 'none') === 'none' || o.settlement?.status === 'pending'
  })
}

const POLL_MS = 15_000

/**
 * Keep order screens current: refetch on the viewer's order notifications and
 * every 15 s while something is still awaited. `data` is whatever the screen
 * fetched; a new value means a check finished.
 */
export function useOrdersLive(data: unknown, orders: Order[], reload: () => void) {
  const { user } = useAuth()
  const [checked, setChecked] = useState<{ data: unknown; at: Date }>(() => ({ data, at: new Date() }))
  // Stamped during render when a new response arrives (the React-endorsed "adjust state on prop change").
  if (checked.data !== data && data != null) setChecked({ data, at: new Date() })

  useTopic(user ? `user:${user.id}` : null, (event) => {
    if (event.type === 'notification' && event.kind && LIVE_KINDS.has(event.kind)) reload()
  })

  const pending = awaitingSomething(orders)
  useEffect(() => {
    if (!pending) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') reload()
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [pending, reload])

  const refresh = useCallback(() => reload(), [reload])
  return { lastChecked: checked.at, refresh, polling: pending }
}
