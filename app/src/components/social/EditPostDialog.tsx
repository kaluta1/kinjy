import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ImagePlus, Loader2, Play, X } from 'lucide-react'
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
 * Any other post is edited as its text and its pictures and clips: they can
 * be added (button or paste), taken off, and the server checks a new one
 * exactly as at creation. The audience and the community are not edited
 * here: those are not typos, and changing them would be a re-post.
 */

/** A picture or clip on an ordinary post, as this dialog holds it. */
interface Attached {
  media_id: string
  /** What the dialog shows: the signed address, or the local file for a new one. */
  preview: string
  /** What the server is sent: the stored address. */
  url: string
  kind: string
  poster?: string | null
}

const MAX_ATTACHED = 4
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
  const fileRef = useRef<HTMLInputElement>(null)
  // An ordinary post's attachments, editable here. Not used for an article,
  // whose pictures live in its text.
  const [attached, setAttached] = useState<Attached[]>(() =>
    isArticle
      ? []
      : media
          .filter((m): m is PostMedia & { media_id: string } => Boolean(m.media_id))
          .map((m) => ({ media_id: m.media_id, preview: m.url ?? '', url: m.url ?? '', kind: m.kind, poster: m.poster_url })),
  )
  const [uploading, setUploading] = useState(false)
  // An upload inside the article editor; saving before it lands loses it.
  const [editorUploading, setEditorUploading] = useState(false)
  const waiting = uploading || editorUploading

  const addFiles = async (files: File[]) => {
    const wanted = files.filter((f) => f.type.startsWith('image/') || f.type.startsWith('video/'))
    if (!wanted.length) return
    const room = MAX_ATTACHED - attached.length
    if (room <= 0) {
      setError(`A post can carry at most ${MAX_ATTACHED} pictures or clips.`)
      return
    }
    setUploading(true)
    setError(null)
    try {
      for (const file of wanted.slice(0, room)) {
        const asset = await kaluta.media.upload(file)
        const preview = URL.createObjectURL(file)
        previews.current.push(preview)
        setAttached((current) => [
          ...current,
          { media_id: asset.id, preview, url: asset.url, kind: file.type.startsWith('video/') ? 'video' : asset.kind },
        ])
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

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

  // Pictures that arrived with a pasted article. Offered to the server on
  // Save like the ones uploaded here, so the post carries them - except any
  // the post already has, which are attached already.
  const adopt = useCallback(
    (items: Array<{ id: string; kind: string; url: string }>) => {
      for (const item of items) {
        if (mediaFor(item.id)) continue
        added.current.set(item.id, {
          id: item.id,
          url: item.url,
          kind: item.kind,
          size_bytes: 0,
          provenance: 'original',
          provenance_signed: false,
        })
      }
    },
    [mediaFor],
  )

  const save = async () => {
    setError(null)
    if (isArticle && !headline.trim()) {
      setError('An article needs a headline.')
      return
    }
    const next = isArticle ? `<h2>${headline.trim().replace(/[<>&]/g, '')}</h2>${html}` : text.trim()
    if (!(isArticle ? htmlToText(html) : next || attached.length)) {
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
      // An ordinary post sends the whole list it should carry now; the server
      // keeps, adds and takes off accordingly.
      const updated = await kaluta.posts.edit(
        post.id,
        next,
        isArticle ? newlyPlaced : attached.map((a) => ({ media_id: a.media_id, url: a.url, kind: a.kind })),
      )
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
              <RichTextEditor
                value={html}
                onChange={setHtml}
                onUploadMedia={upload}
                onAdoptMedia={adopt}
                mediaFor={mediaFor}
                onUploadingChange={setEditorUploading}
                minHeight={320}
              />
            </>
          ) : (
            <textarea
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={8}
              aria-label="Edit your post"
              onPaste={(event) => {
                // A pasted picture joins the post rather than vanishing.
                const files = Array.from(event.clipboardData.files)
                if (files.length) {
                  event.preventDefault()
                  void addFiles(files)
                }
              }}
              className="w-full resize-y rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-[0.95rem] leading-relaxed text-text-hi focus:border-gold/40 focus:outline-none"
            />
          )}
          {!isArticle && (
            <div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {attached.map((item) => (
                  <div key={item.media_id} className="relative aspect-square overflow-hidden rounded-xl bg-black/40">
                    {item.kind === 'video' ? (
                      item.poster ? (
                        <img src={item.poster} alt="" className="h-full w-full object-cover" />
                      ) : (
                        <video src={item.preview} muted playsInline preload="metadata" className="h-full w-full object-cover" />
                      )
                    ) : (
                      <img src={item.preview} alt="" className="h-full w-full object-cover" />
                    )}
                    {item.kind === 'video' && (
                      <span className="pointer-events-none absolute inset-0 grid place-items-center">
                        <span className="grid h-9 w-9 place-items-center rounded-full bg-black/55">
                          <Play size={16} className="ms-0.5 text-white" fill="currentColor" aria-hidden="true" />
                        </span>
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => setAttached((current) => current.filter((a) => a.media_id !== item.media_id))}
                      aria-label="Take this off the post"
                      className="absolute end-1.5 top-1.5 grid h-7 w-7 place-items-center rounded-full bg-black/65 text-white hover:bg-black/80"
                    >
                      <X size={14} aria-hidden="true" />
                    </button>
                  </div>
                ))}
                {attached.length < MAX_ATTACHED && (
                  <button
                    type="button"
                    onClick={() => fileRef.current?.click()}
                    disabled={uploading}
                    className="grid aspect-square place-items-center rounded-xl border border-dashed border-[var(--cloud-border)] text-text-mid hover:border-gold/50 hover:text-gold-soft disabled:opacity-50"
                  >
                    <span className="flex flex-col items-center gap-1 text-xs font-semibold">
                      {uploading ? (
                        <Loader2 size={20} className="animate-spin" aria-hidden="true" />
                      ) : (
                        <ImagePlus size={20} aria-hidden="true" />
                      )}
                      {uploading ? 'Uploading…' : 'Add photo or video'}
                    </span>
                  </button>
                )}
              </div>
              <input
                ref={fileRef}
                type="file"
                accept="image/*,video/*"
                multiple
                hidden
                onChange={(e) => void addFiles(Array.from(e.target.files ?? []))}
              />
              <p className="mt-1.5 text-xs text-text-low">
                Up to {MAX_ATTACHED}. You can also paste a picture into the text. A new picture is reviewed before
                everyone can see it.
              </p>
            </div>
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
              disabled={busy || waiting}
              className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-sm font-bold text-ink disabled:opacity-40"
            >
              {busy ? 'Saving…' : waiting ? 'Uploading…' : 'Save'}
            </button>
          </div>
        </footer>
      </div>
    </div>,
    document.body,
  )
}
