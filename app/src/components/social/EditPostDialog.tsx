import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import RichTextEditor from '@/components/social/RichTextEditor'
import { ApiError, kaluta, type Post, type PostMedia, type UploadedMedia } from '@/lib/api'
import { articleMediaIds, htmlToText } from '@/lib/richtext'

/**
 * Fixing a post, in a dialog.
 *
 * It was edited in place in the card, as a textarea - which for an article
 * meant nothing at all: the card kept drawing the article, the textarea never
 * appeared, and the draft it held was the article stripped to plain text, so
 * saving it would have wiped every heading, list and picture. An article is
 * now edited the way it was written: its headline and the same rich editor,
 * pictures and clips included, where they can be added, moved by cutting and
 * re-inserting, or taken out.
 *
 * The audience, the community and the photos attached under a post are not
 * edited here: those are not typos, and changing them would be a re-post.
 */
export default function EditPostDialog({
  post,
  body,
  media,
  onSaved,
  onClose,
}: {
  post: Post
  /** The body as currently shown, which may already be an edit of this card's. */
  body: string
  /** The post's media as signed for this viewer, for the editor's previews. */
  media: PostMedia[]
  onSaved: (updated: Post) => void
  onClose: () => void
}) {
  const isArticle = post.format === 'article'
  // An article's headline is the first <h2> of its body: split it off for the
  // field, put it back on save.
  const split = (() => {
    if (!isArticle) return { headline: '', rest: body }
    const match = /^\s*<h2>([\s\S]*?)<\/h2>/i.exec(body)
    return match ? { headline: htmlToText(match[1]), rest: body.slice(match[0].length) } : { headline: '', rest: body }
  })()
  const [headline, setHeadline] = useState(split.headline)
  const [html, setHtml] = useState(split.rest)
  const [text, setText] = useState(isArticle ? '' : body)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Files uploaded in this dialog, offered to the server if the text keeps them.
  const added = useRef(new Map<string, UploadedMedia>())
  const previews = useRef<string[]>([])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && !busy && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onClose])
  useEffect(() => () => previews.current.forEach((url) => URL.revokeObjectURL(url)), [])

  const mediaFor = useCallback(
    (id: string) => media.find((m) => m.media_id === id) ?? null,
    [media],
  )
  const upload = useCallback(
    async (file: File) => {
      const asset = await kaluta.media.upload(file)
      added.current.set(asset.id, asset)
      // The server's address is not viewable until the file is attached, so
      // the preview is the local file.
      const url = URL.createObjectURL(file)
      previews.current.push(url)
      return { id: asset.id, url, kind: asset.kind }
    },
    [],
  )

  const save = async () => {
    setError(null)
    if (isArticle && !headline.trim()) {
      setError('An article needs a headline.')
      return
    }
    const next = isArticle ? `<h2>${headline.trim().replace(/[<>&]/g, '')}</h2>${html}` : text.trim()
    if (!(isArticle ? htmlToText(html) : next)) {
      setError(isArticle ? 'The article has no text.' : 'The post is empty.')
      return
    }
    setBusy(true)
    try {
      const newlyPlaced = isArticle
        ? articleMediaIds(html)
            .map((id) => added.current.get(id))
            .filter((m): m is UploadedMedia => Boolean(m))
            .map((m) => ({ media_id: m.id, url: m.url, kind: m.kind }))
        : undefined
      const updated = await kaluta.posts.edit(post.id, next, newlyPlaced)
      onSaved(updated)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the change')
    } finally {
      setBusy(false)
    }
  }

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={isArticle ? 'Edit your article' : 'Edit your post'}
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/55 p-3 sm:p-6"
      onClick={() => !busy && onClose()}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="cloud-card flex max-h-[92svh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl"
      >
        <header className="flex items-center justify-between gap-2 border-b border-[var(--cloud-border)] px-4 py-3">
          <h2 className="text-sm font-semibold text-text-hi">{isArticle ? 'Edit your article' : 'Edit your post'}</h2>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="rounded-full p-1.5 text-text-mid hover:bg-text-hi/[0.07]"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
          {isArticle ? (
            <>
              <input
                value={headline}
                onChange={(e) => setHeadline(e.target.value)}
                maxLength={200}
                aria-label="Article headline"
                placeholder="Article headline"
                className="w-full rounded-card-sm border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-lg font-semibold text-text-hi focus:border-gold/40 focus:outline-none"
              />
              <RichTextEditor value={html} onChange={setHtml} onUploadMedia={upload} mediaFor={mediaFor} minHeight={320} />
            </>
          ) : (
            <textarea
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
              aria-label="Edit your post"
              className="w-full resize-y rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-[0.95rem] leading-relaxed text-text-hi focus:border-gold/40 focus:outline-none"
            />
          )}
          {error && (
            <p role="alert" className="text-sm text-red-300">
              {error}
            </p>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-[var(--cloud-border)] px-4 py-3">
          <span className="text-xs text-text-low">Editable for 24 hours after posting</span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              className="rounded-full px-4 py-1.5 text-sm font-semibold text-text-mid hover:text-text-hi"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void save()}
              disabled={busy}
              className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-sm font-bold text-ink disabled:opacity-40"
            >
              {busy ? 'Saving…' : 'Save'}
            </button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
