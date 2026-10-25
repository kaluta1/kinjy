import { useState } from 'react'
import { Link } from 'react-router'
import { AlertTriangle, ArrowLeft, BookOpen, Download, Sparkles } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { EbookCover } from '@/components/commerce/EbookPanels'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type LibraryItem } from '@/lib/api'
import { fileSize } from '@/lib/ebooks'

const STATE_TEXT: Record<LibraryItem['state'], string | null> = {
  active: null,
  unpaid: 'Waiting for the payment to be confirmed.',
  refunded: 'Refunded: this ebook is no longer available to download.',
  revoked: 'Access was withdrawn. Contact support if this is a mistake.',
  expired: 'The download period has ended.',
  limit_reached: 'All the downloads this ebook allows are used. Contact the seller or support.',
  unavailable: 'The file is not available right now. Try again later.',
}

function LibraryRow({ item }: { item: LibraryItem }) {
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const download = async () => {
    setBusy(true)
    setMessage(null)
    try {
      // A fresh link every time: it works for a few minutes and only for this
      // book. If a download is interrupted, pressing again is not counted.
      const link = await kaluta.market.downloadEbook(item.order_id)
      const a = document.createElement('a')
      a.href = link.url
      a.download = link.filename
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      a.remove()
      setMessage({
        ok: true,
        text: `Downloading ${link.filename} (${fileSize(link.size_bytes)}).${
          link.download_limit ? ` ${link.downloads_used} of ${link.download_limit} downloads used.` : ''
        }`,
      })
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'The download could not start. Try again.' })
    } finally {
      setBusy(false)
    }
  }

  const blocked = STATE_TEXT[item.state]
  return (
    <li className="cloud-card flex gap-4 p-4">
      <EbookCover src={item.product.images[0]} title={item.product.title} className="w-20 shrink-0 self-start sm:w-24" />
      <div className="min-w-0 flex-1">
        <Link to={`/market/products/${item.product.id}`} className="block text-base font-semibold text-text-hi hover:text-gold-soft">
          {item.product.title}
        </Link>
        {item.product.author && <p className="text-sm text-text-mid">by {item.product.author}</p>}
        <p className="mt-1 text-xs text-text-low">
          Sold by {item.seller.display_name ?? 'a seller'} · bought {new Date(item.purchased_at).toLocaleDateString()} · order{' '}
          <span className="mono-data">{item.order_id.slice(0, 12)}</span>
        </p>
        {item.file && (
          <p className="mt-1 text-xs text-text-low">
            {item.file.format.toUpperCase()} · {fileSize(item.file.size_bytes)} · version {item.file.version}
            {item.download_limit ? ` · ${item.downloads_used} of ${item.download_limit} downloads used` : ''}
            {item.access_until ? ` · available until ${new Date(item.access_until).toLocaleDateString()}` : ''}
          </p>
        )}
        {item.update_available && item.state === 'active' && (
          <p className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-gold-soft">
            <Sparkles size={12} aria-hidden="true" /> A new version is available
          </p>
        )}
        {blocked && <p className="mt-2 text-sm text-red-200">{blocked}</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void download()}
            disabled={busy || item.state !== 'active'}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 text-sm font-bold text-ink disabled:opacity-40"
          >
            <Download size={15} aria-hidden="true" />
            {busy ? 'Preparing…' : `Download${item.file ? ` ${item.file.format.toUpperCase()}` : ''}`}
          </button>
          <Link
            to={`/market/orders/${item.order_id}/dispute`}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full border border-white/12 px-4 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
          >
            <AlertTriangle size={14} aria-hidden="true" />
            Report a damaged or wrong file
          </Link>
        </div>
        {message && (
          <p role={message.ok ? 'status' : 'alert'} className={message.ok ? 'mt-2 text-xs text-success' : 'mt-2 text-xs text-red-200'}>
            {message.text}
          </p>
        )}
      </div>
    </li>
  )
}

/** Every ebook the member has bought, ready to download again. */
export default function MarketLibrary() {
  const library = useApi(() => kaluta.market.library(), [])
  const items = library.data?.items ?? []
  return (
    <AppShell
      title="My ebooks"
      action={
        <Link to="/market" className="inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft">
          <ArrowLeft size={14} aria-hidden="true" />
          Marketplace
        </Link>
      }
    >
      {library.loading && !library.data ? (
        <p className="text-sm text-text-mid" role="status">Loading your ebooks…</p>
      ) : library.error ? (
        <p role="alert" className="text-sm text-red-200">{library.error}</p>
      ) : items.length === 0 ? (
        <div className="cloud-card mx-auto flex max-w-md flex-col items-center p-8 text-center">
          <BookOpen size={30} className="text-text-low" aria-hidden="true" />
          <p className="mt-3 text-sm text-text-mid">The ebooks you buy appear here, ready to download.</p>
          <Link to="/market?kind=digital" className="mt-4 inline-flex min-h-[44px] items-center rounded-full border border-white/12 px-5 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft">
            Browse ebooks
          </Link>
        </div>
      ) : (
        <ul className="mx-auto max-w-3xl space-y-3">
          {items.map((item) => (
            <LibraryRow key={item.order_id} item={item} />
          ))}
        </ul>
      )}
    </AppShell>
  )
}
