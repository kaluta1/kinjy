import { useState } from 'react'
import { Check, Loader2, Share2 } from 'lucide-react'
import { ApiError, kaluta } from '@/lib/api'
import { copyLink, shareLink } from '@/lib/share'
import { cn } from '@/lib/utils'

/**
 * "Share deal": the member's personal short link to one listing.
 *
 * The existing short links (kinjy.com/s/<token>) with the member's invitation
 * switched on - made for whoever is signed in, never for a code or an id from
 * the page. The server checks the deal can be bought before making it, and
 * asking again returns the same link. On a phone or tablet the device's share
 * sheet opens with the deal's title; on a computer the link goes to the
 * clipboard (the desktop sheet is a window over the page - see lib/share).
 *
 * Someone who joins Kinjy through it is linked to the sharer by the usual
 * invitation rules; a member who already has an account is not.
 */
export default function ShareDeal({
  productId,
  title,
  compact = false,
}: {
  productId: string
  title: string
  /** The small text button of a deal card, rather than the page's pill. */
  compact?: boolean
}) {
  const [busy, setBusy] = useState(false)
  const [url, setUrl] = useState<string | null>(null)
  const [note, setNote] = useState<{ text: string; failed: boolean } | null>(null)

  const share = async () => {
    setBusy(true)
    setNote(null)
    try {
      const link = url ?? (await kaluta.auth.shareLink(`/market/products/${productId}`, true)).url
      setUrl(link)
      const touch = typeof window.matchMedia === 'function' && window.matchMedia('(pointer: coarse)').matches
      const how = touch ? await shareLink(link, `${title} on Kinjy`) : await copyLink(link)
      if (how === 'copied') setNote({ text: 'Personal link copied', failed: false })
      else if (how === 'shared') setNote({ text: 'Shared', failed: false })
      else if (how === 'failed') setNote({ text: 'Copying was blocked. Select the link below and copy it.', failed: true })
    } catch (err) {
      setNote({
        text: err instanceof ApiError ? err.message : 'Could not make your link. Check your connection and try again.',
        failed: true,
      })
    } finally {
      setBusy(false)
    }
  }

  const copyAgain = async () => {
    if (!url) return
    const result = await copyLink(url)
    setNote(
      result === 'copied'
        ? { text: 'Personal link copied', failed: false }
        : { text: 'Copying was blocked. Select the link below and copy it.', failed: true },
    )
  }

  return (
    <div className={cn(compact ? 'mt-2' : 'mt-3')}>
      <button
        type="button"
        onClick={share}
        disabled={busy}
        aria-label={`Share deal: ${title}`}
        title="Your personal link to this deal. People who join Kinjy through it are linked to you."
        className={cn(
          'inline-flex items-center justify-center gap-1.5 font-semibold disabled:opacity-50',
          compact
            ? 'min-h-[44px] w-full rounded-full px-4 text-xs text-text-low hover:text-gold-soft'
            : 'min-h-[44px] rounded-full border border-white/12 px-5 text-sm text-text-mid hover:border-gold/40 hover:text-gold-soft',
        )}
      >
        {busy ? (
          <Loader2 size={13} className="animate-spin" aria-hidden="true" />
        ) : note && !note.failed ? (
          <Check size={13} aria-hidden="true" />
        ) : (
          <Share2 size={13} aria-hidden="true" />
        )}
        Share deal
      </button>
      <p role="status" aria-live="polite" className={cn('text-center text-xs', note?.failed ? 'text-red-200' : 'text-text-low')}>
        {note?.text}
      </p>
      {url && note?.failed && (
        <div className="mt-1 flex items-center gap-2">
          <input
            readOnly
            value={url}
            aria-label="Your personal link to this deal"
            onFocus={(e) => e.currentTarget.select()}
            className="mono-data min-w-0 flex-1 rounded-lg bg-black/30 px-2 py-1.5 text-xs text-text-hi"
          />
          <button type="button" onClick={copyAgain} className="text-xs font-semibold text-gold-soft">
            Copy
          </button>
        </div>
      )}
    </div>
  )
}
