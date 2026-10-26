import { useState } from 'react'
import { Link } from 'react-router'
import { BookOpen, Download, FileText } from 'lucide-react'
import EbookFileField, { type EbookFileValue } from '@/components/commerce/EbookFileField'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type ProductDetail } from '@/lib/api'
import { EBOOK_CATEGORY_LABELS, FORMAT_NOTES, LICENSE_LABELS, fileSize, languageName } from '@/lib/ebooks'
import { htmlToText, sanitizeHtml } from '@/lib/richtext'

/** The cover, whole: a book cover is a design, so it is never cropped. */
export function EbookCover({ src, title, className }: { src?: string; title: string; className?: string }) {
  return (
    <div className={`overflow-hidden rounded-card-sm border border-white/10 bg-black/20 ${className ?? ''}`}>
      {src ? (
        <img src={src} alt={`Cover of ${title}`} className="aspect-[2/3] w-full object-contain" />
      ) : (
        <div className="flex aspect-[2/3] w-full items-center justify-center text-text-low" aria-hidden="true">
          <BookOpen size={40} />
        </div>
      )}
    </div>
  )
}

/** What the book is, what the buyer gets, and the free sample. */
export function EbookDetails({ item }: { item: ProductDetail }) {
  const e = item.ebook!
  const rows: Array<[string, string | null]> = [
    ['Format', e.format ? `${e.format.toUpperCase()}${e.size_bytes ? ` · ${fileSize(e.size_bytes)}` : ''}` : null],
    ['Language', languageName(e.language)],
    ['Pages', e.page_count ? String(e.page_count) : null],
    ['Category', e.category ? EBOOK_CATEGORY_LABELS[e.category] ?? e.category : null],
    ['Published', e.published_on ? new Date(e.published_on).toLocaleDateString() : null],
    ['Edition', e.edition],
    ['ISBN', e.isbn],
    ['Licence', LICENSE_LABELS[e.license] ?? e.license],
    ['Version', e.version && e.version > 1 ? `${e.version} (updated ${new Date(e.updated_at!).toLocaleDateString()})` : null],
  ]
  return (
    <div className="space-y-4">
      <div className="cloud-card flex items-start gap-3 p-4">
        <Download size={20} className="mt-0.5 shrink-0 text-gold-soft" aria-hidden="true" />
        <div className="text-sm">
          <p className="font-semibold text-text-hi">Digital product - nothing is shipped</p>
          <p className="mt-1 text-text-mid">
            {e.delivery}.{e.format && FORMAT_NOTES[e.format] ? ` ${FORMAT_NOTES[e.format]}` : ''}
            {e.download_limit ? ` Up to ${e.download_limit} downloads.` : ''}
            {e.access_days ? ` Available for ${e.access_days} days after purchase.` : ''}
          </p>
        </div>
      </div>
      <dl className="cloud-card grid grid-cols-1 gap-x-4 gap-y-2 p-4 text-sm sm:grid-cols-2">
        {rows
          .filter(([, value]) => value)
          .map(([label, value]) => (
            <div key={label} className="flex justify-between gap-3 sm:block">
              <dt className="caption">{label}</dt>
              <dd className="text-text-hi">{value}</dd>
            </div>
          ))}
      </dl>
      {e.sample && (
        <a
          href={e.sample.url}
          download
          className="inline-flex min-h-[44px] items-center gap-2 rounded-full border border-white/12 px-5 text-sm font-semibold text-text-mid hover:border-gold/40 hover:text-gold-soft"
        >
          <FileText size={15} aria-hidden="true" />
          Read a free sample ({e.sample.format.toUpperCase()})
        </a>
      )}
    </div>
  )
}

/** The formatted description of an ebook, cleaned again on the way in. */
export function EbookDescription({ html }: { html: string }) {
  if (!htmlToText(html)) return null
  return (
    <div
      className="prose-article text-sm leading-relaxed text-text-mid"
      // Sanitised on the server when it was saved, and again here.
      dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }}
    />
  )
}

/**
 * Search engines and link previews read this: the book as it really is, with
 * its real price. No rating or sales figure that does not exist.
 */
export function EbookStructuredData({ item }: { item: ProductDetail }) {
  const e = item.ebook!
  const data = {
    '@context': 'https://schema.org',
    '@type': 'Book',
    name: item.title,
    ...(e.author ? { author: { '@type': 'Person', name: e.author } } : {}),
    description: e.summary || htmlToText(item.description).slice(0, 300),
    ...(item.images[0] ? { image: item.images[0] } : {}),
    ...(e.isbn ? { isbn: e.isbn } : {}),
    ...(e.language ? { inLanguage: e.language } : {}),
    ...(e.page_count ? { numberOfPages: e.page_count } : {}),
    bookFormat: 'https://schema.org/EBook',
    url: `${window.location.origin}/market/products/${item.id}`,
    offers: {
      '@type': 'Offer',
      price: item.customer_price,
      priceCurrency: item.currency,
      availability: item.status === 'active' ? 'https://schema.org/InStock' : 'https://schema.org/Discontinued',
    },
  }
  return <script type="application/ld+json">{JSON.stringify(data)}</script>
}

/** The seller's tools on their own ebook: publish, replace the file, sales. */
export function EbookOwnerPanel({ item, onChanged }: { item: ProductDetail; onChanged: () => void }) {
  const e = item.ebook!
  const terms = useApi(() => kaluta.market.ebookTerms(), [])
  const sales = useApi(() => kaluta.market.productSales(item.id), [item.id])
  const versions = useApi(() => kaluta.market.ebookVersions(item.id), [item.id])
  const [replacement, setReplacement] = useState<EbookFileValue | null>(null)
  const [uploading, setUploading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const run = async (action: () => Promise<string>) => {
    setBusy(true)
    setNote(null)
    try {
      setNote({ ok: true, text: await action() })
      onChanged()
      versions.reload()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    } finally {
      setBusy(false)
    }
  }

  const publish = (status: 'active' | 'paused') =>
    run(async () => {
      await kaluta.market.updateProduct(item.id, { status })
      return status === 'active'
        ? 'Published. Buyers can find and buy it.'
        : 'Unpublished. Nobody new can buy it; buyers keep their copy.'
    })

  return (
    <section className="cloud-card space-y-4 p-4" aria-label="Manage your ebook">
      <h2 className="text-base font-semibold text-text-hi">Your ebook</h2>
      {item.status === 'suspended' && (
        <p className="rounded-xl bg-red-500/10 p-3 text-sm text-red-200">
          Suspended by Kinjy{e.suspended_reason ? `: ${e.suspended_reason}` : ''}. Contact support to resolve it.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-text-mid">
          Status: <strong className="text-text-hi">{item.status === 'active' ? 'Published' : item.status === 'draft' ? 'Draft' : item.status === 'paused' ? 'Unpublished' : item.status}</strong>
        </span>
        {item.status !== 'active' && item.status !== 'suspended' && (
          <button type="button" disabled={busy} onClick={() => void publish('active')}
            className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-xs font-bold text-ink disabled:opacity-40">
            Publish
          </button>
        )}
        {item.status === 'active' && (
          <button type="button" disabled={busy} onClick={() => void publish('paused')}
            className="rounded-full border border-white/15 px-4 py-2 text-xs font-semibold text-text-mid hover:text-text-hi disabled:opacity-40">
            Unpublish
          </button>
        )}
      </div>

      <div className="text-sm text-text-mid">
        {e.file ? (
          <p>
            Current file: <span className="text-text-hi">{e.file.filename}</span> · version {e.file.version} · {fileSize(e.file.size_bytes)}
          </p>
        ) : (
          <p className="text-red-200">No ebook file yet: add one before publishing.</p>
        )}
      </div>
      <EbookFileField
        id="owner-replace-file"
        purpose="ebook"
        label={e.file ? 'Upload a new version' : 'Add the ebook file'}
        hint="Every buyer gets the new version from their library. Earlier versions stay on record."
        maxMb={terms.data?.max_file_mb ?? 100}
        value={replacement}
        onChange={setReplacement}
        onBusy={setUploading}
      />
      {replacement && (
        <button type="button" disabled={busy || uploading}
          onClick={() => void run(async () => {
            const result = await kaluta.market.replaceEbookFile(item.id, replacement.id)
            setReplacement(null)
            return result.unchanged ? 'That is already the current file.' : `Version ${result.version} is now the file buyers download.`
          })}
          className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-xs font-bold text-ink disabled:opacity-40">
          Use this file
        </button>
      )}
      {(versions.data?.items.length ?? 0) > 1 && (
        <details className="text-sm text-text-mid">
          <summary className="cursor-pointer">Version history</summary>
          <ul className="mt-2 space-y-1">
            {versions.data!.items.map((v) => (
              <li key={v.version}>
                Version {v.version}{v.current ? ' (current)' : ''} · {v.filename} · {new Date(v.created_at).toLocaleDateString()}
                {v.note ? ` - ${v.note}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}

      {sales.data && (
        <dl className="mono-data grid grid-cols-2 gap-3 border-t border-white/10 pt-3 text-center text-sm sm:grid-cols-4">
          <div><dd className="text-text-hi">{sales.data.units_sold}</dd><dt className="caption">Copies sold</dt></div>
          <div><dd className="text-text-hi">${sales.data.gross}</dd><dt className="caption">Your price × sales</dt></div>
          <div><dd className="text-text-hi">${sales.data.refunds}</dd><dt className="caption">Refunded</dt></div>
          <div><dd className="text-gold-soft">${sales.data.net_released}</dd><dt className="caption">Released to you</dt></div>
        </dl>
      )}
      {sales.data && sales.data.awaiting_release > 0 && (
        <p className="text-xs text-text-low">
          {sales.data.awaiting_release} paid order{sales.data.awaiting_release === 1 ? '' : 's'} still held: each becomes final at the
          buyer's first successful download. <Link to="/market/seller" className="text-gold-soft underline">Seller dashboard</Link>
        </p>
      )}
      {note && (
        <p role={note.ok ? 'status' : 'alert'} className={note.ok ? 'text-sm text-success' : 'text-sm text-red-200'}>
          {note.text}
        </p>
      )}
    </section>
  )
}
