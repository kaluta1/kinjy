import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ArrowLeft, Star, Store } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import VendorListingCard from '@/components/commerce/VendorListingCard'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type Product, type StorefrontResponse } from '@/lib/api'
import { cn } from '@/lib/utils'

const PAGE = 24

function Stars({ value }: { value: number }) {
  return (
    <span className="inline-flex" aria-hidden="true">
      {[1, 2, 3, 4, 5].map((n) => (
        <Star key={n} size={15} className={cn(n <= Math.round(value) ? 'fill-gold text-gold' : 'text-text-low')} />
      ))}
    </span>
  )
}

export default function MarketVendor() {
  const { vendorId = '' } = useParams()
  const rating = useApi(() => kaluta.market.vendorRating(vendorId), [vendorId])

  const [shop, setShop] = useState<StorefrontResponse | null>(null)
  const [items, setItems] = useState<Product[]>([])
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (offset: number) => {
      setLoading(true)
      setError(null)
      try {
        const page = await kaluta.market.vendor(vendorId, { limit: PAGE, offset })
        setShop(page)
        setItems((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) setNotFound(true)
        else setError(err instanceof ApiError ? err.message : 'Could not load this shop')
      } finally {
        setLoading(false)
      }
    },
    [vendorId],
  )

  useEffect(() => {
    setNotFound(false)
    setShop(null)
    setItems([])
    load(0)
  }, [load])

  const back = (
    <Link
      to="/market"
      className="inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
    >
      <ArrowLeft size={14} aria-hidden="true" />
      Marketplace
    </Link>
  )

  if (notFound) {
    return (
      <AppShell title="Shop" action={back}>
        <div className="cloud-card mx-auto max-w-md p-6 text-center">
          <Store size={28} className="mx-auto text-text-low" aria-hidden="true" />
          <p className="mt-3 text-sm text-text-mid">This shop is not available.</p>
        </div>
      </AppShell>
    )
  }

  const vendor = shop?.vendor
  const name = vendor?.display_name ?? 'Shop'
  const since = shop?.since
    ? new Date(shop.since).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    : null

  return (
    <AppShell title={shop ? undefined : 'Shop'} action={back}>
      <div className="mx-auto max-w-3xl">
        {error && (
          <p role="alert" className="mb-3 text-sm text-red-200">
            {error}
          </p>
        )}

        {shop && vendor && (
          <header className="cloud-card p-4">
            <div className="flex items-center gap-3">
              {vendor.avatar_url ? (
                <img src={vendor.avatar_url} alt="" className="size-16 shrink-0 rounded-full object-cover" />
              ) : (
                <span className="flex size-16 shrink-0 items-center justify-center rounded-full bg-ink-2 text-xl font-semibold text-gold-soft">
                  {name.slice(0, 1).toUpperCase()}
                </span>
              )}
              <div className="min-w-0">
                <h1
                  className="truncate text-lg font-semibold text-text-hi"
                  title={vendor.handle ? `@${vendor.handle}` : undefined}
                >
                  {name}
                </h1>
                {since && <p className="caption">Member since {since}</p>}
              </div>
            </div>

            <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
              <div className="rounded-2xl bg-ink-2/60 p-2">
                <dd className="mono-data text-base font-semibold text-text-hi">{shop.stats.listings_active}</dd>
                <dt className="caption">Listings</dt>
              </div>
              <div className="rounded-2xl bg-ink-2/60 p-2">
                <dd className="mono-data text-base font-semibold text-text-hi">{shop.stats.sales_settled}</dd>
                <dt className="caption">Sales</dt>
              </div>
              <div className="rounded-2xl bg-ink-2/60 p-2">
                <dd className="mono-data text-base font-semibold text-text-hi">
                  {rating.data && rating.data.count > 0 ? rating.data.average.toFixed(1) : '—'}
                </dd>
                <dt className="caption">Rating</dt>
              </div>
            </dl>

            <p className="mt-3 flex items-center gap-2 text-sm text-text-mid">
              {rating.data && rating.data.count > 0 ? (
                <>
                  <Stars value={rating.data.average} />
                  <span>
                    {rating.data.average.toFixed(1)} out of 5 · {rating.data.count} review
                    {rating.data.count === 1 ? '' : 's'}
                  </span>
                </>
              ) : (
                <span className="text-text-low">No reviews yet</span>
              )}
            </p>
          </header>
        )}

        {loading && items.length === 0 && !error && <p className="mt-4 text-sm text-text-low">Loading this shop…</p>}

        {shop && (
          <section className="mt-5" aria-label="Listings">
            {items.length === 0 && !loading ? (
              <p className="text-sm text-text-low">No active listings right now.</p>
            ) : (
              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {items.map((product) => (
                  <li key={product.id}>
                    <VendorListingCard product={product} />
                  </li>
                ))}
              </ul>
            )}
            {items.length < shop.total && (
              <button
                type="button"
                disabled={loading}
                onClick={() => load(items.length)}
                className="mx-auto mt-4 flex min-h-[48px] items-center justify-center rounded-full border border-white/12 px-8 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft disabled:opacity-40"
              >
                {loading ? 'Loading…' : 'Load more'}
              </button>
            )}
          </section>
        )}
      </div>
    </AppShell>
  )
}
