import { useState } from 'react'
import { Link } from 'react-router'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta } from '@/lib/api'

/**
 * A community's marketplace: its members' own listings from the Kinjy
 * marketplace. Buying, delivery, reviews and disputes are the marketplace's.
 */
export default function CommunityMarket({
  communityId,
  member,
  steward,
  currentUserId,
}: {
  communityId: string
  member: boolean
  steward: boolean
  currentUserId: string | null
}) {
  const listings = useApi(() => kaluta.communityPaid.listings(communityId), [communityId])
  const [adding, setAdding] = useState(false)
  const mine = useApi(() => (adding ? kaluta.market.myProducts() : Promise.resolve(null)), [adding])
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setNote(null)
    try {
      await fn()
      setNote({ ok: true, text: ok })
      listings.reload()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    }
  }

  const items = listings.data?.items ?? []
  return (
    <section aria-label="Marketplace" className="space-y-3">
      {member && (
        <button type="button" onClick={() => setAdding(!adding)} aria-expanded={adding}
          className="rounded-full border border-[var(--cloud-border)] px-3.5 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi">
          {adding ? 'Close' : 'Show one of my listings here'}
        </button>
      )}
      {adding && (
        <div className="cloud-card p-4">
          {mine.loading && <p className="caption">Loading your listings…</p>}
          {mine.data?.items.filter((p) => p.status === 'active').length === 0 && (
            <p className="text-sm text-text-mid">You have no active listing. <Link to="/market/sell" className="text-gold-soft hover:underline">Sell something</Link></p>
          )}
          <ul className="space-y-2">
            {mine.data?.items.filter((p) => p.status === 'active').map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate text-text-hi">{p.title}</span>
                <button type="button" className="text-sm font-semibold text-gold-soft hover:underline"
                  onClick={() => void run(() => kaluta.communityPaid.addListing(communityId, p.id), 'Listed in this community.')}>
                  Show here
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {note && <p role={note.ok ? 'status' : 'alert'} className={note.ok ? 'text-sm text-emerald-300' : 'text-sm text-red-300'}>{note.text}</p>}
      {listings.error && <p className="text-sm text-text-mid">{listings.error}</p>}
      {!listings.loading && items.length === 0 && <p className="cloud-card p-4 text-sm text-text-mid">Nothing listed here yet.</p>}
      <ul className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2">
        {items.map((p) => (
          <li key={p.id} className="cloud-card flex flex-col overflow-hidden">
            <Link to={`/market/products/${p.id}`} className="block flex-1 p-4">
              {p.images[0] && <img src={p.images[0]} alt="" loading="lazy" className="mb-3 aspect-[4/3] w-full rounded-xl object-cover" />}
              <p className="line-clamp-2 text-sm font-semibold text-text-hi">{p.title}</p>
              <p className="caption mt-1">{p.vendor?.display_name ?? 'A member'} · {p.kind}</p>
              <p className="mt-1 font-bold text-gold-soft">${Number(p.customer_price).toFixed(2)}</p>
            </Link>
            {(p.listed_by === currentUserId || steward) && (
              <button type="button" className="px-4 pb-3 text-start text-xs font-semibold text-text-low hover:text-red-200"
                onClick={() => void run(() => kaluta.communityPaid.removeListing(communityId, p.id), 'Taken off this community.')}>
                Take off this community
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
