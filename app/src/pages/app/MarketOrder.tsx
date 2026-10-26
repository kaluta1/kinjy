import { useState } from 'react'
import { Link, useParams } from 'react-router'
import {
  ArrowLeft,
  BookOpen,
  Check,
  ChevronDown,
  Copy,
  ExternalLink,
  LifeBuoy,
  MapPin,
  RefreshCw,
  ShieldCheck,
  Truck,
} from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { fieldClass, primaryBtn } from '@/components/commerce/ActionSheet'
import OrderActions from '@/components/commerce/OrderActions'
import { AcceptanceRecord } from '@/components/commerce/OrderCard'
import OrderStatusChip from '@/components/commerce/OrderStatusChip'
import ProductGallery from '@/components/commerce/ProductGallery'
import { KindBadge } from '@/components/commerce/ProductCard'
import {
  dateTimeLabel,
  dateTimeSecondsLabel,
  isService,
  nextStepHint,
  timeSecondsLabel,
  useOrdersLive,
} from '@/components/commerce/orderStatus'
import { addressLines } from '@/components/commerce/shippingText'
import { useApi } from '@/hooks/useApi'
import {
  ApiError,
  kaluta,
  type Order,
  type OrderCounterpart,
  type ProductProtection,
  type SupportCase,
  type SupportKind,
} from '@/lib/api'
import { countryName } from '@/lib/profileOptions'
import { htmlToText } from '@/lib/richtext'
import { cn } from '@/lib/utils'

function PersonAvatar({ who }: { who: OrderCounterpart }) {
  const [failed, setFailed] = useState(false)
  const initial = (who.display_name || who.handle || '?').slice(0, 1).toUpperCase()
  if (!who.avatar_url || failed) {
    return (
      <span
        aria-hidden="true"
        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-indigo/30 text-base font-semibold text-sky"
      >
        {initial}
      </span>
    )
  }
  return (
    <img
      src={who.avatar_url}
      alt=""
      onError={() => setFailed(true)}
      className="h-12 w-12 shrink-0 rounded-full object-cover"
    />
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="cloud-card p-4">
      <h2 className="mb-3 text-sm font-semibold text-text-hi">{title}</h2>
      {children}
    </section>
  )
}

/** Long sections fold on a phone and open by default on wider screens. */
function Fold({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  const [initiallyOpen] = useState(() => window.matchMedia('(min-width: 640px)').matches)
  return (
    <details className="cloud-card group p-4" open={initiallyOpen}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-semibold text-text-hi [&::-webkit-details-marker]:hidden">
        {icon}
        <span className="flex-1">{title}</span>
        <ChevronDown size={16} className="shrink-0 text-text-low transition-transform group-open:rotate-180" aria-hidden="true" />
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  )
}

function Timeline({ order }: { order: Order }) {
  const ebook = order.final_when === 'first_download' || Boolean(order.product?.ebook)
  const service = isService(order)
  const steps = [
    { label: 'Placed', at: order.created_at, reached: true },
    { label: 'Paid, held', at: order.escrow_funded_at, reached: Boolean(order.escrow_funded_at) },
    ...(ebook
      ? []
      : [
          {
            label: service ? 'Service performed' : 'Shipped / ready',
            at: order.delivered_at,
            reached: Boolean(order.delivered_at),
          },
        ]),
    {
      label: ebook ? 'Final: first successful download' : 'Final: buyer confirmed receipt',
      at: order.acceptance?.accepted_at ?? null,
      reached: Boolean(order.acceptance?.accepted),
      seconds: true,
    },
    {
      label: 'Settlement confirmed',
      at: order.settlement?.confirmed_at ?? null,
      reached: order.settlement?.status === 'confirmed',
    },
  ]
  let current = 0
  steps.forEach((s, i) => {
    if (s.reached) current = i
  })

  return (
    <ol>
      {steps.map((step, i) => (
        <li key={step.label} className="flex gap-3">
          <div className="flex flex-col items-center">
            <span
              className={cn(
                'flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[0.65rem]',
                step.reached ? 'border-gold bg-gold/20 text-gold-soft' : 'border-white/15 text-text-low',
                i === current && 'ring-2 ring-gold/40',
              )}
            >
              {step.reached ? <Check size={12} aria-hidden="true" /> : i + 1}
            </span>
            {i < steps.length - 1 && (
              <span className={cn('w-px flex-1', steps[i + 1].reached ? 'bg-gold/50' : 'bg-white/10')} />
            )}
          </div>
          <div className="pb-4">
            <p
              aria-current={i === current ? 'step' : undefined}
              className={cn('text-sm', step.reached ? 'font-semibold text-text-hi' : 'text-text-low')}
            >
              {step.label}
            </p>
            {step.at && (
              <p className="caption">{'seconds' in step ? dateTimeSecondsLabel(step.at) : dateTimeLabel(step.at)}</p>
            )}
          </div>
        </li>
      ))}
    </ol>
  )
}

function Detail({ term, value }: { term: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <div>
      <dt className="caption">{term}</dt>
      <dd className="whitespace-pre-line break-words text-sm text-text-mid">{value}</dd>
    </div>
  )
}

function Protection({ protection, accepted }: { protection: ProductProtection | null | undefined; accepted: boolean }) {
  const g = protection?.guarantee ?? null
  const ins = protection?.insurance ?? null
  return (
    <div className="space-y-3 text-sm">
      {g ? (
        <div>
          <p className="font-semibold text-text-hi">Seller guarantee</p>
          <dl className="mt-1 space-y-1.5">
            <Detail term="What it covers" value={g.scope} />
            <Detail term="For how long" value={g.duration} />
            <Detail term="Not covered" value={g.exclusions} />
            <Detail term="How to claim" value={g.claim_contact} />
          </dl>
        </div>
      ) : (
        <p className="text-text-mid">No seller guarantee.</p>
      )}
      {ins ? (
        <div>
          <p className="font-semibold text-text-hi">Insurance</p>
          <dl className="mt-1 space-y-1.5">
            <Detail term="Insurer" value={ins.insurer} />
            <Detail term="What it covers" value={ins.coverage} />
            <Detail term="Not covered" value={ins.exclusions} />
            <Detail term="How to claim" value={ins.claim_process} />
            <Detail term="Premium" value={ins.premium_included ? 'Included in the price' : 'Not included in the price'} />
          </dl>
        </div>
      ) : (
        <p className="text-text-mid">No insurance included.</p>
      )}
      {protection?.service_terms && (
        <div>
          <p className="font-semibold text-text-hi">What “performed” means for this service</p>
          <p className="mt-1 whitespace-pre-line break-words text-text-mid">{protection.service_terms}</p>
        </div>
      )}
      <p className="caption">Kinjy does not arbitrate ordinary disputes between buyers and sellers.</p>
      {accepted && (
        <p className="caption">
          This purchase is final. Any later issue goes to the seller under their guarantee, or to the insurer.
        </p>
      )}
    </div>
  )
}

const SUPPORT_KINDS: { id: SupportKind; label: string }[] = [
  { id: 'payment_problem', label: 'A payment problem' },
  { id: 'duplicate_charge', label: 'I was charged twice' },
  { id: 'not_handed_over', label: 'Confirmed, but not handed over' },
  { id: 'file_problem', label: 'The file does not download or open' },
  { id: 'fraud', label: 'Fraud or a scam' },
  { id: 'legal', label: 'A legal problem (illegal or counterfeit item)' },
]

function SupportForm({ orderId }: { orderId: string }) {
  const cases = useApi<{ items: SupportCase[] }>(() => kaluta.market.mySupportCases(), [])
  const [kind, setKind] = useState<SupportKind>('payment_problem')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sent, setSent] = useState<SupportCase | null>(null)

  const mine = (cases.data?.items ?? []).filter((c) => c.order_id === orderId)
  const tooShort = message.trim().length < 10

  const send = async (event: React.FormEvent) => {
    event.preventDefault()
    if (tooShort || busy) return
    setBusy(true)
    setError(null)
    try {
      const opened = await kaluta.market.openSupport(orderId, kind, message.trim())
      setSent(opened)
      setMessage('')
      cases.reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not go through')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <p className="caption">
        Operational support from Kinjy: payments, charges, files, fraud or legal issues. This is not a dispute;
        Kinjy does not decide disagreements between buyers and sellers.
      </p>

      {mine.length > 0 && (
        <ul className="space-y-2">
          {mine.map((c) => (
            <li key={c.id} className="rounded-2xl bg-ink-2/60 p-3 text-sm">
              <p className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold text-text-hi">
                  {SUPPORT_KINDS.find((k) => k.id === c.kind)?.label ?? c.kind}
                </span>
                <span className={cn('text-xs font-semibold', c.status === 'open' ? 'text-sky' : 'text-text-mid')}>
                  {c.status === 'open' ? 'Open' : 'Closed'}
                </span>
              </p>
              <p className="caption mt-1">{dateTimeLabel(c.created_at)}</p>
              {c.resolution_note && <p className="mt-1 text-text-mid">Kinjy: {c.resolution_note}</p>}
            </li>
          ))}
        </ul>
      )}

      {sent && (
        <p role="status" className="text-sm text-gold-soft">
          Sent to Kinjy support. Case <span className="mono-data">{sent.id.slice(0, 8)}</span>.
        </p>
      )}

      <form onSubmit={send} className="space-y-3">
        <div>
          <label htmlFor="support-kind" className="caption mb-1.5 block">
            What is the problem?
          </label>
          <select
            id="support-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as SupportKind)}
            className={fieldClass}
          >
            {SUPPORT_KINDS.map((k) => (
              <option key={k.id} value={k.id}>
                {k.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="support-message" className="caption mb-1.5 block">
            Describe it (at least 10 characters)
          </label>
          <textarea
            id="support-message"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={4}
            minLength={10}
            maxLength={2000}
            required
            aria-describedby="support-message-hint"
            className={`${fieldClass} resize-y`}
          />
          {message.length > 0 && tooShort && (
            <p id="support-message-hint" className="mt-1 text-xs text-text-low">
              A few more words, please.
            </p>
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm text-red-200">
            {error}
          </p>
        )}
        <button type="submit" disabled={tooShort || busy} aria-busy={busy} className={primaryBtn}>
          {busy ? 'Sending…' : 'Send to Kinjy support'}
        </button>
      </form>
    </div>
  )
}

export default function MarketOrder() {
  const { id = '' } = useParams()
  const order = useApi<Order>(() => kaluta.market.order_(id), [id])
  const [note, setNote] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const item = order.data
  const live = useOrdersLive(order.data, item ? [item] : [], order.reload)

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(id)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // Clipboard can be blocked; the id is still on screen to copy by hand.
    }
  }

  const body = () => {
    if (order.loading && !item) {
      return (
        <div className="space-y-3" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="cloud-card h-32 animate-pulse" />
          ))}
        </div>
      )
    }
    if (!item) {
      // Missing and not-yours look the same on purpose: do not confirm that an id exists.
      return (
        <div className="cloud-card p-6 text-center">
          <p className="text-sm text-text-mid">We could not find that order.</p>
          <Link
            to="/market/orders"
            className="mt-3 inline-flex min-h-[44px] items-center rounded-full border border-white/12 px-5 text-sm font-semibold text-text-mid"
          >
            Back to my orders
          </Link>
        </div>
      )
    }

    const seller = item.role === 'seller'
    const product = item.product
    const other = item.counterpart
    const service = isService(item)
    const ebook = item.final_when === 'first_download' || Boolean(product?.ebook)
    const accepted = Boolean(item.acceptance?.accepted)
    const unit = (Number(item.customer_price) / Math.max(1, item.quantity)).toFixed(2)
    const place = [product?.city, product?.country ? countryName(product.country, 'en') : null]
      .filter(Boolean)
      .join(', ')
    const safeTracking = item.tracking_url && /^https?:\/\//i.test(item.tracking_url)
    const hasDelivery = Boolean(
      item.shipping || item.carrier || item.tracking_number || item.tracking_url || item.delivery_note,
    )
    const serviceNotPerformed =
      !seller && service && !ebook && !accepted && !item.cancellation && item.status === 'in_escrow'

    return (
      <div className="space-y-3">
        <section className="cloud-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <OrderStatusChip status={item.status} service={service} className="text-xs" />
            <button
              type="button"
              onClick={copyId}
              aria-label="Copy order number"
              className="inline-flex min-h-[44px] items-center gap-1.5 text-text-mid hover:text-gold-soft"
            >
              <span className="mono-data text-sm text-text-hi">{id.slice(0, 8)}</span>
              {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
              <span className="caption">{copied ? 'Copied' : 'Copy'}</span>
            </button>
          </div>
          <p className="caption mt-1">{nextStepHint(item)}</p>

          <div className="mt-3 empty:hidden">
            <AcceptanceRecord order={item} />
          </div>

          {serviceNotPerformed && (
            <p className="mt-3 text-sm text-text-mid">The seller has not marked the service as performed yet.</p>
          )}

          {note && (
            <p role="status" className="mt-3 text-sm text-gold-soft">
              {note}
            </p>
          )}
          <div className="mt-3 empty:hidden">
            <OrderActions order={item} onChanged={order.reload} onNote={setNote} />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-white/10 pt-2">
            <p className="caption flex-1">
              Last checked <span className="mono-data">{timeSecondsLabel(live.lastChecked)}</span>
              {live.polling && ' · checking every 15 s'}
            </p>
            <button
              type="button"
              onClick={live.refresh}
              disabled={order.loading}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-white/12 px-4 text-xs font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft disabled:opacity-40"
            >
              <RefreshCw size={13} className={cn(order.loading && 'animate-spin')} aria-hidden="true" />
              Refresh
            </button>
          </div>
        </section>

        {ebook && !seller && (
          <section className="cloud-card flex flex-wrap items-center gap-3 p-4">
            <BookOpen size={20} className="text-gold-soft" aria-hidden="true" />
            <p className="min-w-0 flex-1 text-sm text-text-mid">
              {item.status === 'pending' || item.cancellation
                ? 'The ebook reaches your library once the payment is confirmed.'
                : item.status === 'refunded' || item.status === 'refund_pending'
                  ? 'This order was refunded, so the ebook is no longer available.'
                  : accepted
                    ? 'The purchase became final at your first successful download. The ebook stays in your library.'
                    : 'Your ebook is in your library. The purchase becomes final at your first successful download.'}
            </p>
            {!['pending', 'cancelled', 'refunded', 'refund_pending'].includes(item.status) && !item.cancellation && (
              <Link
                to="/market/library"
                className="inline-flex min-h-[44px] items-center rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 text-sm font-bold text-ink"
              >
                Open my library
              </Link>
            )}
          </section>
        )}

        <Section title="Progress">
          <Timeline order={item} />
        </Section>

        <Section title={seller ? 'What you sold' : 'What you bought'}>
          {product ? (
            <>
              <ProductGallery images={product.images} title={product.title} />
              <h3 className="mt-3 text-base font-semibold text-text-hi">{product.title}</h3>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                <KindBadge kind={product.kind} />
                <span className="caption">Quantity {item.quantity}</span>
                {place && <span className="caption">{place}</span>}
              </div>
              {product.description && (
                <p className="mt-3 line-clamp-6 whitespace-pre-line break-words text-sm text-text-mid">
                  {product.ebook ? htmlToText(product.description) : product.description}
                </p>
              )}
              {product.available ? (
                <Link
                  to={`/market/products/${product.id}`}
                  className="mt-3 inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-gold-soft underline"
                >
                  View the listing
                  <ExternalLink size={13} aria-hidden="true" />
                </Link>
              ) : (
                <p className="caption mt-3">This listing is no longer available.</p>
              )}
            </>
          ) : (
            <p className="text-sm text-text-mid">The listing for this order has been removed.</p>
          )}
        </Section>

        <Section title="Price">
          <dl className="space-y-1.5 text-sm">
            <div className="flex justify-between gap-3">
              <dt className="text-text-mid">
                {item.quantity} × ${unit}
              </dt>
              <dd className="mono-data text-text-hi">${item.customer_price}</dd>
            </div>
            <div className="flex justify-between gap-3">
              <dt className="caption">Seller price + Kinjy markup</dt>
              <dd className="caption mono-data">
                ${item.vendor_price} + ${item.margin}
              </dd>
            </div>
            {Number(item.refunded_amount) > 0 && (
              <div className="flex justify-between gap-3 text-amber-200">
                <dt>Refunded</dt>
                <dd className="mono-data">${item.refunded_amount}</dd>
              </div>
            )}
            <div className="flex justify-between gap-3 border-t border-white/10 pt-2 font-semibold">
              <dt className="text-text-hi">{seller ? 'Buyer paid' : 'Total'}</dt>
              <dd className="mono-data text-gold-soft">${item.customer_price}</dd>
            </div>
            {seller && (
              <div className="flex justify-between gap-3">
                <dt className="text-text-mid">Your price</dt>
                <dd className="mono-data text-text-hi">${item.vendor_price}</dd>
              </div>
            )}
          </dl>
        </Section>

        {other && (
          <Section title={seller ? 'Buyer' : 'Seller'}>
            {seller ? (
              <div className="flex min-h-[44px] items-center gap-3">
                <PersonAvatar who={other} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-text-hi">
                    {other.display_name || 'Buyer'}
                  </span>
                  {other.handle && <span className="caption block truncate">@{other.handle}</span>}
                </span>
              </div>
            ) : (
              <Link
                to={`/market/vendors/${other.user_id}`}
                className="flex min-h-[44px] items-center gap-3 hover:text-gold-soft"
              >
                <PersonAvatar who={other} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-semibold text-text-hi">
                    {other.display_name || 'Seller'}
                  </span>
                  {other.handle && <span className="caption block truncate">@{other.handle}</span>}
                </span>
              </Link>
            )}
          </Section>
        )}

        {hasDelivery && (
          <Section title="Delivery">
            <div className="space-y-2 text-sm text-text-mid">
              {item.shipping && (
                <p className="flex items-start gap-2">
                  <MapPin size={14} className="mt-0.5 shrink-0 text-gold" aria-hidden="true" />
                  <span>
                    {addressLines(item.shipping).map((line) => (
                      <span key={line} className="block">
                        {line}
                      </span>
                    ))}
                  </span>
                </p>
              )}
              {(item.carrier || item.tracking_number) && (
                <p className="flex flex-wrap items-center gap-x-2">
                  <Truck size={14} className="shrink-0 text-gold" aria-hidden="true" />
                  <span>{item.carrier}</span>
                  {item.tracking_number && <span className="mono-data break-all">{item.tracking_number}</span>}
                </p>
              )}
              {safeTracking && (
                <a
                  href={item.tracking_url ?? undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-[44px] items-center gap-1 font-semibold text-gold-soft underline"
                >
                  Track parcel
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
              )}
              {item.delivery_note && (
                <p className="whitespace-pre-line break-words">Seller’s note: {item.delivery_note}</p>
              )}
            </div>
          </Section>
        )}

        <Fold title="Guarantee, insurance and terms" icon={<ShieldCheck size={15} className="text-gold" aria-hidden="true" />}>
          <Protection protection={item.protection} accepted={accepted} />
        </Fold>

        <Fold title="Report a problem to Kinjy support" icon={<LifeBuoy size={15} className="text-gold" aria-hidden="true" />}>
          <SupportForm orderId={item.id} />
        </Fold>
      </div>
    )
  }

  return (
    <AppShell title="Order" subtitle="Status, product, delivery and what to do next.">
      <div className="mx-auto max-w-2xl">
        <Link
          to={item?.role === 'seller' ? '/market/seller' : '/market/orders'}
          className="mb-3 inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          {item?.role === 'seller' ? 'My shop' : 'My orders'}
        </Link>
        {body()}
      </div>
    </AppShell>
  )
}
