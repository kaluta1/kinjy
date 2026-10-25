import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, BookOpen, Check, Copy, ExternalLink, MapPin, ShieldCheck, Truck } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import OrderActions from '@/components/commerce/OrderActions'
import OrderStatusChip from '@/components/commerce/OrderStatusChip'
import ProductGallery from '@/components/commerce/ProductGallery'
import { KindBadge } from '@/components/commerce/ProductCard'
import { dateLabel, dateTimeLabel, nextStepHint } from '@/components/commerce/orderStatus'
import { addressLines } from '@/components/commerce/shippingText'
import { useApi } from '@/hooks/useApi'
import { kaluta, type Order, type OrderCounterpart } from '@/lib/api'
import { countryName } from '@/lib/profileOptions'
import { htmlToText } from '@/lib/richtext'
import { cn } from '@/lib/utils'

function SellerAvatar({ who }: { who: OrderCounterpart }) {
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

function Timeline({ order }: { order: Order }) {
  const done = order.status === 'settled' || order.status === 'part_refunded'
  const steps = [
    { label: 'Placed', at: order.created_at, reached: true },
    { label: 'Paid, held in escrow', at: order.escrow_funded_at, reached: Boolean(order.escrow_funded_at) },
    { label: 'Delivered', at: order.delivered_at, reached: Boolean(order.delivered_at) },
    { label: 'Completed', at: order.escrow_released_at, reached: done || Boolean(order.escrow_released_at) },
  ]
  let current = 0
  steps.forEach((s, i) => {
    if (s.reached) current = i
  })

  return (
    <div>
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
              {step.at && <p className="caption">{dateTimeLabel(step.at)}</p>}
            </div>
          </li>
        ))}
      </ol>
      {order.status === 'in_escrow' && order.dispute_window_ends && (
        <p className="caption text-sky">
          Releases automatically on {dateLabel(order.dispute_window_ends)} unless you confirm or dispute first.
        </p>
      )}
      {order.status === 'delivered' && order.dispute_window_ends && (
        <p className="caption text-sky">
          Releases automatically on {dateLabel(order.dispute_window_ends)} if you do nothing.
        </p>
      )}
      {order.status === 'disputed' && (
        <p className="caption text-amber-200">This order is in dispute. The release clock is stopped.</p>
      )}
      {order.status === 'refunded' && <p className="caption text-amber-200">This order was refunded.</p>}
      {order.status === 'cancelled' && <p className="caption">This order was cancelled.</p>}
    </div>
  )
}

export default function MarketOrder() {
  const { id = '' } = useParams()
  const order = useApi<Order>(() => kaluta.market.order_(id), [id])
  const terms = useApi(() => kaluta.market.escrowTerms(), [])
  const [note, setNote] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const item = order.data

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

    const product = item.product
    const seller = item.counterpart
    const unit = (Number(item.customer_price) / Math.max(1, item.quantity)).toFixed(2)
    const place = [product?.city, product?.country ? countryName(product.country, 'en') : null]
      .filter(Boolean)
      .join(', ')
    const safeTracking = item.tracking_url && /^https?:\/\//i.test(item.tracking_url)
    const hasDelivery = Boolean(
      item.shipping || item.carrier || item.tracking_number || item.tracking_url || item.delivery_note,
    )

    return (
      <div className="space-y-3">
        <section className="cloud-card p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <OrderStatusChip status={item.status} className="text-xs" />
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
          {note && (
            <p role="status" className="mt-2 text-sm text-gold-soft">
              {note}
            </p>
          )}
          <div className="mt-3 empty:hidden">
            <OrderActions order={item} onChanged={order.reload} onNote={setNote} />
          </div>
        </section>

        {product?.ebook && item.role === 'buyer' && (
          <section className="cloud-card flex flex-wrap items-center gap-3 p-4">
            <BookOpen size={20} className="text-gold-soft" aria-hidden="true" />
            <p className="min-w-0 flex-1 text-sm text-text-mid">
              {['pending', 'cancelled'].includes(item.status)
                ? 'The ebook is delivered to your library once the payment is confirmed.'
                : item.status === 'refunded'
                  ? 'This order was refunded, so the ebook is no longer available.'
                  : 'Your ebook is in your library, ready to download.'}
            </p>
            {!['pending', 'cancelled', 'refunded'].includes(item.status) && (
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

        <Section title="What you bought">
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
                <p className="mt-3 whitespace-pre-line break-words text-sm text-text-mid">
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
              <dt className="text-text-hi">Total</dt>
              <dd className="mono-data text-gold-soft">${item.customer_price}</dd>
            </div>
          </dl>
        </Section>

        {seller && (
          <Section title="Seller">
            <Link
              to={`/market/vendors/${seller.user_id}`}
              className="flex min-h-[44px] items-center gap-3 hover:text-gold-soft"
            >
              <SellerAvatar who={seller} />
              <span className="min-w-0">
                <span
                  className="block truncate text-sm font-semibold text-text-hi"
                  title={seller.handle ? `@${seller.handle}` : undefined}
                >
                  {seller.display_name || 'Seller'}
                </span>
              </span>
            </Link>
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

        {terms.data && (
          <Section title="How escrow protects you">
            <p className="caption flex items-start gap-2">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-gold" aria-hidden="true" />
              <span>
                Your money is held by {terms.data.custodian} — {terms.data.licence} — not by Kinjy. It reaches the
                seller when you confirm receipt, or {terms.data.auto_release_days} days after payment if you neither
                confirm nor dispute.
              </span>
            </p>
          </Section>
        )}
      </div>
    )
  }

  return (
    <AppShell title="Order" subtitle="Status, product, delivery and what to do next.">
      <div className="mx-auto max-w-2xl">
        <Link
          to="/market/orders"
          className="mb-3 inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          My orders
        </Link>
        {body()}
      </div>
    </AppShell>
  )
}
