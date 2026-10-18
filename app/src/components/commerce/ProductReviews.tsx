import { Star } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { kaluta } from '@/lib/api'
import { cn } from '@/lib/utils'

export function Stars({ value, size = 14 }: { value: number; size?: number }) {
  return (
    <span className="inline-flex" role="img" aria-label={`${value} out of 5 stars`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <Star
          key={n}
          size={size}
          aria-hidden="true"
          className={cn(n <= Math.round(value) ? 'fill-gold-soft text-gold-soft' : 'text-white/20')}
        />
      ))}
    </span>
  )
}

export default function ProductReviews({ productId }: { productId: string }) {
  const reviews = useApi(() => kaluta.market.productReviews(productId, { limit: 20 }), [productId])
  const data = reviews.data

  return (
    <section aria-labelledby="reviews-heading" className="cloud-card p-5">
      <h2 id="reviews-heading" className="text-sm font-semibold text-text-hi">Reviews</h2>

      {reviews.loading && !data && <div className="mt-4 h-24 animate-pulse rounded-2xl bg-white/5" aria-hidden="true" />}
      {reviews.error && !data && <p className="mt-3 text-sm text-text-low">Reviews are unavailable right now.</p>}

      {data && data.count === 0 && (
        <p className="mt-3 text-sm text-text-low">
          No reviews yet - only buyers who received the item can review.
        </p>
      )}

      {data && data.count > 0 && (
        <>
          <div className="mt-4 flex items-center gap-5">
            <div className="text-center">
              <p className="mono-data text-3xl text-text-hi">{data.average.toFixed(1)}</p>
              <Stars value={data.average} />
              <p className="caption mt-1">{data.count} {data.count === 1 ? 'review' : 'reviews'}</p>
            </div>
            <ul className="min-w-0 flex-1 space-y-1.5" aria-label="Rating distribution">
              {[5, 4, 3, 2, 1].map((n) => {
                const count = data.distribution[String(n)] ?? 0
                return (
                  <li key={n} className="flex items-center gap-2 text-xs text-text-mid">
                    <span className="w-3 text-end">{n}</span>
                    <span className="h-2 flex-1 overflow-hidden rounded-full bg-white/10">
                      <span
                        className="block h-full rounded-full bg-gold"
                        style={{ width: `${(count / data.count) * 100}%` }}
                      />
                    </span>
                    <span className="w-6 text-end">{count}</span>
                  </li>
                )
              })}
            </ul>
          </div>

          <ul className="mt-5 divide-y divide-white/10">
            {data.items.map((review) => {
              const name =
                review.reviewer?.display_name ||
                'Verified buyer'
              return (
                <li key={review.id} className="py-3">
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <p className="text-sm font-semibold text-text-hi">{name}</p>
                    <time dateTime={review.created_at} className="caption">
                      {new Date(review.created_at).toLocaleDateString()}
                    </time>
                  </div>
                  <Stars value={review.rating} size={12} />
                  {review.comment && (
                    <p className="mt-1.5 whitespace-pre-line text-sm text-text-mid">{review.comment}</p>
                  )}
                </li>
              )
            })}
          </ul>
        </>
      )}
    </section>
  )
}
