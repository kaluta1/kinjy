import { useState } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, MapPin, PackageX, ShieldCheck, ShoppingBag } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import CheckoutSheet from '@/components/commerce/CheckoutSheet'
import ProductGallery from '@/components/commerce/ProductGallery'
import { KindBadge } from '@/components/commerce/ProductCard'
import ProductReviews from '@/components/commerce/ProductReviews'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { kaluta, type VendorBrief } from '@/lib/api'

function SellerAvatar({ vendor }: { vendor: VendorBrief }) {
  const [failed, setFailed] = useState(false)
  const initial = (vendor.display_name || vendor.handle || '?').slice(0, 1).toUpperCase()
  if (!vendor.avatar_url || failed) {
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
      src={vendor.avatar_url}
      alt=""
      onError={() => setFailed(true)}
      className="h-12 w-12 shrink-0 rounded-full object-cover"
    />
  )
}

export default function MarketProduct() {
  const { id = '' } = useParams()
  const { user } = useAuth()
  const product = useApi(() => kaluta.market.product(id), [id])
  const terms = useApi(() => kaluta.market.escrowTerms(), [])
  const [checkout, setCheckout] = useState(false)
  const [ordered, setOrdered] = useState(false)

  const back = (
    <Link
      to="/market"
      className="inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
    >
      <ArrowLeft size={14} aria-hidden="true" />
      Marketplace
    </Link>
  )

  if (product.loading && !product.data) {
    return (
      <AppShell title="Listing" action={back}>
        <div className="animate-pulse space-y-4" aria-hidden="true">
          <div className="aspect-square w-full rounded-card-sm bg-white/5 md:aspect-[4/3]" />
          <div className="h-6 w-2/3 rounded bg-white/10" />
          <div className="h-4 w-1/3 rounded bg-white/5" />
        </div>
      </AppShell>
    )
  }

  const item = product.data
  if (!item) {
    return (
      <AppShell title="Listing" action={back}>
        <div className="cloud-card mx-auto flex max-w-md flex-col items-center p-8 text-center">
          <PackageX size={30} className="text-text-low" aria-hidden="true" />
          <p className="mt-3 text-sm text-text-mid">This listing is not available.</p>
          <Link
            to="/market"
            className="mt-4 inline-flex min-h-[44px] items-center rounded-full border border-white/12 px-5 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
          >
            Browse the marketplace
          </Link>
        </div>
      </AppShell>
    )
  }

  const unlimited = item.stock === null
  const soldOut = !unlimited && item.stock! <= 0
  const lowStock = !unlimited && item.stock! > 0 && item.stock! < 5
  const unavailable = soldOut || item.status !== 'active'
  const place = [item.city, item.country].filter(Boolean).join(', ')
  const sellerName = item.vendor.display_name || 'Seller'

  const action = item.is_owner ? (
    <p className="flex-1 text-center text-sm text-text-mid">This is your listing</p>
  ) : !user ? (
    <Link
      // After signing in, come back to this listing instead of the dashboard.
      to={`/join?mode=signin&next=${encodeURIComponent(`/market/products/${item.id}`)}`}
      className="inline-flex min-h-[48px] flex-1 items-center justify-center rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink"
    >
      Sign in to order
    </Link>
  ) : (
    <button
      type="button"
      disabled={unavailable}
      onClick={() => setCheckout(true)}
      className="inline-flex min-h-[48px] flex-1 items-center justify-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink disabled:opacity-40"
    >
      <ShoppingBag size={15} aria-hidden="true" />
      {unavailable ? (soldOut ? 'Out of stock' : 'Unavailable') : 'Order - held in escrow'}
    </button>
  )

  return (
    <AppShell title={item.title} action={back}>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
        <ProductGallery images={item.images} title={item.title} />

        <div className="space-y-4">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <KindBadge kind={item.kind} />
              {soldOut && <span className="text-xs font-semibold text-red-200">Out of stock</span>}
              {lowStock && <span className="text-xs font-semibold text-gold-soft">Only {item.stock} left</span>}
            </div>
            <p className="mono-data mt-3 text-2xl text-gold-soft">
              ${item.customer_price}
              <span className="caption ms-2">you pay</span>
            </p>
            {place && (
              <p className="mt-2 inline-flex items-center gap-1 text-sm text-text-low">
                <MapPin size={13} aria-hidden="true" />
                {place}
              </p>
            )}
          </div>

          {item.description && (
            <p className="whitespace-pre-line text-sm leading-relaxed text-text-mid">{item.description}</p>
          )}

          <div className="cloud-card flex items-start gap-3 p-4">
            <ShieldCheck size={20} className="mt-0.5 shrink-0 text-gold-soft" aria-hidden="true" />
            <div className="text-sm">
              <p className="font-semibold text-text-hi">
                Funds protected by a licensed custodian until you confirm receipt
              </p>
              {terms.data && (
                <p className="mt-1 text-text-mid">
                  Held by {terms.data.custodian}. If you do nothing, the funds are released to the seller after{' '}
                  {terms.data.auto_release_days} days.
                </p>
              )}
            </div>
          </div>

          <div className="cloud-card p-4">
            <Link to={`/market/vendors/${item.vendor.user_id}`} className="flex items-center gap-3">
              <SellerAvatar vendor={item.vendor} />
              <span className="min-w-0">
                <span
                  className="block truncate text-sm font-semibold text-text-hi"
                  title={item.vendor.handle ? `@${item.vendor.handle}` : undefined}
                >
                  {sellerName}
                </span>
                {item.vendor_since && (
                  <span className="block text-xs text-text-low">
                    Member since {new Date(item.vendor_since).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
                  </span>
                )}
              </span>
            </Link>
            <dl className="mono-data mt-3 grid grid-cols-2 gap-3 border-t border-white/10 pt-3 text-center text-sm">
              <div>
                <dd className="text-text-hi">{item.vendor_stats.listings_active}</dd>
                <dt className="caption">Active listings</dt>
              </div>
              <div>
                <dd className="text-text-hi">{item.vendor_stats.sales_settled}</dd>
                <dt className="caption">Sales settled</dt>
              </div>
            </dl>
            <Link
              to={`/market/vendors/${item.vendor.user_id}`}
              className="mt-3 inline-flex min-h-[44px] items-center text-sm font-semibold text-gold-soft"
            >
              View the seller's shop
            </Link>
          </div>
        </div>
      </div>

      <div className="mt-6">
        <ProductReviews productId={item.id} />
      </div>

      {/* Same offsets as the sell form: clears the mobile tab bar, sits low on desktop. */}
      <div className="sticky bottom-20 z-10 mt-6 lg:bottom-4">
        <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-ink/95 p-3 backdrop-blur">
          <p className="mono-data shrink-0 text-lg text-gold-soft">${item.customer_price}</p>
          {ordered && !item.is_owner ? (
            <Link
              to="/market"
              className="inline-flex min-h-[48px] flex-1 items-center justify-center rounded-full border border-white/12 px-6 text-sm font-semibold text-text-hi"
            >
              Order placed - see my orders
            </Link>
          ) : (
            action
          )}
        </div>
      </div>

      {checkout && (
        <CheckoutSheet
          product={item}
          onClose={() => setCheckout(false)}
          onOrdered={() => setOrdered(true)}
        />
      )}
    </AppShell>
  )
}
