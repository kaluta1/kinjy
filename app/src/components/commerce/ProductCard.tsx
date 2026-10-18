import { Link } from 'react-router'
import { MapPin, ShoppingBag, Star, Store } from 'lucide-react'
import { ProductImage } from '@/components/commerce/ProductGallery'
import type { Product } from '@/lib/api'
import { cn } from '@/lib/utils'

const KIND_LABEL: Record<string, string> = { product: 'Product', service: 'Service', digital: 'Digital' }

export function KindBadge({ kind }: { kind: string }) {
  return (
    <span className="inline-flex shrink-0 items-center rounded-full bg-indigo/25 px-2.5 py-1 text-[11px] font-semibold text-sky">
      {KIND_LABEL[kind] ?? kind}
    </span>
  )
}

export function ProductCardSkeleton() {
  return (
    <div className="cloud-card animate-pulse overflow-hidden" aria-hidden="true">
      <div className="aspect-[4/3] bg-white/5" />
      <div className="space-y-2 p-4">
        <div className="h-4 w-3/4 rounded bg-white/10" />
        <div className="h-3 w-1/2 rounded bg-white/5" />
        <div className="h-5 w-1/3 rounded bg-white/10" />
      </div>
    </div>
  )
}

export default function ProductCard({
  product,
  onOrder,
  mine = false,
}: {
  product: Product
  onOrder: (p: Product) => void
  /** The signed-in member is the seller: marked, and not offered for ordering (the server refuses it). */
  mine?: boolean
}) {
  const place = [product.city, product.country].filter(Boolean).join(', ')
  const seller = product.vendor?.display_name || null
  const rated = (product.rating_count ?? 0) > 0 && product.rating_average != null

  return (
    <article className={cn('cloud-card flex flex-col overflow-hidden', mine && 'ring-1 ring-gold/50')}>
      <Link to={`/market/products/${product.id}`} className="relative block flex-1">
        <ProductImage
          src={product.images[0]}
          alt={product.title}
          className="aspect-[4/3] w-full"
        />
        {mine && (
          <span className="absolute start-2 top-2 inline-flex items-center gap-1 rounded-full bg-gold px-2.5 py-1 text-[11px] font-bold text-ink shadow">
            <Store size={11} aria-hidden="true" />
            Your listing
          </span>
        )}
        <div className="p-4 pb-2">
          <div className="flex items-start justify-between gap-2">
            <h2 className="line-clamp-2 text-sm font-semibold text-text-hi">{product.title}</h2>
            <KindBadge kind={product.kind} />
          </div>
          {product.description && (
            <p className="mt-1.5 line-clamp-2 whitespace-pre-line text-xs leading-relaxed text-text-mid">
              {product.description}
            </p>
          )}
          <div className="mt-2 flex items-center justify-between gap-2">
            <p className="mono-data text-lg text-gold-soft">${product.customer_price}</p>
            {rated && (
              <span className="inline-flex items-center gap-1 text-xs text-text-mid">
                <Star size={12} className="fill-gold-soft text-gold-soft" aria-hidden="true" />
                <span className="sr-only">Rated </span>
                {product.rating_average!.toFixed(1)} ({product.rating_count})
              </span>
            )}
          </div>
          {(seller || place) && (
            <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-text-low">
              {seller && <span className="truncate">{seller}</span>}
              {place && (
                <span className="inline-flex items-center gap-1">
                  <MapPin size={11} aria-hidden="true" />
                  {place}
                </span>
              )}
            </p>
          )}
        </div>
      </Link>
      <div className="px-4 pb-4 pt-2">
        {mine ? (
          <Link
            to="/market/seller"
            className="inline-flex min-h-[44px] w-full items-center justify-center gap-1.5 rounded-full border border-gold/40 px-4 text-xs font-semibold text-gold-soft"
          >
            <Store size={13} aria-hidden="true" />
            Manage in My shop
          </Link>
        ) : (
          <button
            type="button"
            onClick={() => onOrder(product)}
            className="inline-flex min-h-[44px] w-full items-center justify-center gap-1.5 rounded-full border border-white/12 px-4 text-xs font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
          >
            <ShoppingBag size={13} aria-hidden="true" />
            Order - held in escrow
          </button>
        )}
      </div>
    </article>
  )
}
