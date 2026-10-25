import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { BookOpen, ClipboardList, PackageSearch, Plus, Search, SlidersHorizontal, Store } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import CheckoutSheet from '@/components/commerce/CheckoutSheet'
import OrderRecent from '@/components/commerce/OrderRecent'
import FilterSheet, { NO_FILTERS, countFilters, type MarketFilters } from '@/components/commerce/FilterSheet'
import ProductCard, { ProductCardSkeleton } from '@/components/commerce/ProductCard'
import KinjyServiceCard, { poolServiceMatches } from '@/components/commerce/KinjyServiceCard'
import { kaluta, type Product } from '@/lib/api'
import { EBOOK_CATEGORY_LABELS, EBOOK_LANGUAGES } from '@/lib/ebooks'
import { cn } from '@/lib/utils'

const TABS = [
  { id: '', label: 'All' },
  { id: 'product', label: 'Products' },
  { id: 'service', label: 'Services' },
  { id: 'digital', label: 'Ebooks & digital' },
] as const

const SORTS = [
  { id: 'recent', label: 'Newest' },
  { id: 'price_asc', label: 'Price: low to high' },
  { id: 'price_desc', label: 'Price: high to low' },
] as const

const PAGE = 24

export default function Marketplace() {
  const { user } = useAuth()
  // Kinjy's own service, shown first among services: the referral pool.
  const pool = useApi(() => kaluta.referralPool.state(), [])
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  // ?kind=digital opens on the ebooks (the library's empty state links here).
  const [params] = useSearchParams()
  const [kind, setKind] = useState<string>(() => {
    const asked = params.get('kind') ?? ''
    return ['product', 'service', 'digital'].includes(asked) ? asked : ''
  })
  // Ebook filters, offered once the digital tab is chosen.
  const [category, setCategory] = useState('')
  const [language, setLanguage] = useState('')
  const [sort, setSort] = useState<(typeof SORTS)[number]['id']>('recent')
  const [filters, setFilters] = useState<MarketFilters>(NO_FILTERS)
  const [filtersOpen, setFiltersOpen] = useState(false)
  // Only my own listings. The page stays public: signed out, the pill is simply absent.
  const [mine, setMine] = useState(false)
  // Pages are appended: the next one starts where the loaded ones end.
  const [offset, setOffset] = useState(0)
  const [loaded, setLoaded] = useState<Product[]>([])
  const [ordering, setOrdering] = useState<Product | null>(null)
  // Bumped after a purchase so the escrow list refetches; it owns its own data.
  const [orderTick, setOrderTick] = useState(0)

  const products = useApi(
    () =>
      kaluta.market.products({
        q: search || undefined,
        kind: kind || undefined,
        sort,
        country: filters.country || undefined,
        city: filters.city || undefined,
        min_price: filters.min_price || undefined,
        max_price: filters.max_price || undefined,
        mine: mine || undefined,
        category: kind === 'digital' ? category || undefined : undefined,
        language: kind === 'digital' ? language || undefined : undefined,
        limit: PAGE,
        offset,
      }),
    [search, kind, sort, filters, mine, offset, category, language],
  )

  useEffect(() => {
    const page = products.data
    if (!page) return
    setLoaded((previous) =>
      offset === 0
        ? page.items
        : [...previous, ...page.items.filter((item) => !previous.some((known) => known.id === item.id))],
    )
  }, [products.data, offset])

  const items = loaded
  const total = products.data?.total ?? 0
  const activeFilters = countFilters(filters)
  const dirty = activeFilters > 0 || search !== '' || kind !== '' || mine
  const firstLoad = products.loading && !products.data

  const reset = () => {
    setFilters(NO_FILTERS)
    setQuery('')
    setSearch('')
    setKind('')
    setMine(false)
    setOffset(0)
  }

  return (
    <AppShell>
      <header className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <p className="mono-data text-[0.72rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Marketplace</p>
          <h1 className="mt-2 text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
            Buy and sell, safely
          </h1>
          <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
            Sellers set their price; Kinjy adds a 20% markup on top. Buyer money is held by a licensed custodian
            until the buyer confirms receipt.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link to="/market/orders" className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 text-sm font-semibold text-text-mid transition-colors hover:border-gold/50 hover:bg-gold/10 hover:text-text-hi">
            <ClipboardList size={14} aria-hidden="true" />
            My orders
          </Link>
          {user && (
            <Link to="/market/library" className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 text-sm font-semibold text-text-mid transition-colors hover:border-gold/50 hover:bg-gold/10 hover:text-text-hi">
              <BookOpen size={14} aria-hidden="true" />
              My ebooks
            </Link>
          )}
          <Link to="/market/seller" className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 text-sm font-semibold text-text-mid transition-colors hover:border-gold/50 hover:bg-gold/10 hover:text-text-hi">
            My shop
          </Link>
          <Link
            to="/market/sell"
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 text-sm font-bold text-ink"
          >
            <Plus size={14} aria-hidden="true" />
            Sell
          </Link>
        </div>
      </header>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          setSearch(query.trim())
          setOffset(0)
        }}
      >
        <label className="flex min-h-[44px] items-center gap-2.5 rounded-full bg-text-hi/[0.07] px-5">
          <Search size={15} className="shrink-0 text-text-low" aria-hidden="true" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the marketplace..."
            aria-label="Search products"
            className="w-full bg-transparent text-base text-text-hi placeholder:text-text-low focus:outline-none sm:text-sm"
          />
        </label>
      </form>

      <div
        role="tablist"
        aria-label="Listing type"
        className="-mx-4 mt-4 flex gap-2 overflow-x-auto px-4 pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={kind === tab.id}
            onClick={() => {
              setKind(tab.id)
              setOffset(0)
            }}
            className={cn(
              'min-h-[44px] shrink-0 rounded-full border px-5 text-sm font-semibold',
              kind === tab.id
                ? 'border-gold/60 bg-gold/[0.12] text-gold-soft'
                : 'border-white/12 text-text-mid hover:border-gold/40',
            )}
          >
            {tab.label}
          </button>
        ))}
        {user && (
          <button
            type="button"
            aria-pressed={mine}
            onClick={() => {
              setMine((on) => !on)
              setOffset(0)
            }}
            className={cn(
              'inline-flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-full border px-5 text-sm font-semibold',
              mine
                ? 'border-gold/60 bg-gold/[0.12] text-gold-soft'
                : 'border-white/12 text-text-mid hover:border-gold/40',
            )}
          >
            <Store size={14} aria-hidden="true" />
            My listings
          </button>
        )}
      </div>

      {kind === 'digital' && (
        <div className="mt-3 flex flex-wrap gap-2">
          <label>
            <span className="sr-only">Ebook category</span>
            <select
              value={category}
              onChange={(e) => {
                setCategory(e.target.value)
                setOffset(0)
              }}
              className="min-h-[44px] rounded-full border border-white/12 bg-ink-2/60 px-4 text-base text-text-hi focus:border-gold/40 focus:outline-none sm:text-sm"
            >
              <option value="">All categories</option>
              {Object.entries(EBOOK_CATEGORY_LABELS).map(([id, label]) => (
                <option key={id} value={id}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">Ebook language</span>
            <select
              value={language}
              onChange={(e) => {
                setLanguage(e.target.value)
                setOffset(0)
              }}
              className="min-h-[44px] rounded-full border border-white/12 bg-ink-2/60 px-4 text-base text-text-hi focus:border-gold/40 focus:outline-none sm:text-sm"
            >
              <option value="">All languages</option>
              {EBOOK_LANGUAGES.map(([code, name]) => (
                <option key={code} value={code}>{name}</option>
              ))}
            </select>
          </label>
        </div>
      )}

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setFiltersOpen(true)}
          className="relative inline-flex min-h-[44px] items-center gap-1.5 rounded-full border border-white/12 px-4 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
        >
          <SlidersHorizontal size={14} aria-hidden="true" />
          Filters
          {activeFilters > 0 && (
            <span
              aria-label={`${activeFilters} active`}
              className="flex h-5 min-w-[20px] items-center justify-center rounded-full bg-gold px-1 text-[11px] font-bold text-ink"
            >
              {activeFilters}
            </span>
          )}
        </button>
        <label className="ms-auto">
          <span className="sr-only">Sort by</span>
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as typeof sort)
              setOffset(0)
            }}
            className="min-h-[44px] rounded-full border border-white/12 bg-ink-2/60 px-4 text-base text-text-hi focus:border-gold/40 focus:outline-none sm:text-sm"
          >
            {SORTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {products.error && (
        <p role="alert" className="mt-4 text-sm text-red-200">
          {products.error}
        </p>
      )}

      <div className="mt-4 grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 xl:grid-cols-3">
        {!mine && poolServiceMatches(search, kind) && <KinjyServiceCard unitPrice={pool.data?.unit_price ?? null} />}
        {firstLoad && Array.from({ length: 6 }, (_, i) => <ProductCardSkeleton key={i} />)}
        {items.map((product) => (
          <ProductCard
            key={product.id}
            product={product}
            onOrder={setOrdering}
            mine={user != null && product.vendor_id === user.id}
          />
        ))}
      </div>

      {!products.loading && !products.error && items.length === 0 && (
        <div className="cloud-card mt-4 flex flex-col items-center p-8 text-center">
          <PackageSearch size={30} className="text-text-low" aria-hidden="true" />
          <p className="mt-3 text-sm text-text-mid">
            {dirty ? 'Nothing matches these filters.' : 'Nothing listed yet.'}
          </p>
          {dirty && (
            <button
              type="button"
              onClick={reset}
              className="mt-4 min-h-[44px] rounded-full border border-white/12 px-5 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {items.length > 0 && items.length < total && (
        <div className="mt-5 flex justify-center">
          <button
            type="button"
            disabled={products.loading}
            onClick={() => setOffset(items.length)}
            className="min-h-[44px] rounded-full border border-white/12 px-6 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft disabled:opacity-50"
          >
            {products.loading ? 'Loading...' : `Load more (${items.length} of ${total})`}
          </button>
        </div>
      )}

      <section id="orders" className="mt-8 scroll-mt-24">
        <h2 className="mb-3 text-sm font-semibold text-text-hi">Your recent orders</h2>
        <OrderRecent key={orderTick} />
      </section>

      {filtersOpen && (
        <FilterSheet
          value={filters}
          onApply={(next) => {
            setFilters(next)
            setOffset(0)
          }}
          onClose={() => setFiltersOpen(false)}
        />
      )}
      {ordering && (
        <CheckoutSheet
          product={ordering}
          onClose={() => setOrdering(null)}
          onOrdered={() => setOrderTick((n) => n + 1)}
        />
      )}
    </AppShell>
  )
}
