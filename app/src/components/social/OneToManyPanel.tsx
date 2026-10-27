import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, Clapperboard, Copy, Download, Languages, Loader2, Newspaper, Pencil, RotateCcw, X } from 'lucide-react'
import {
  ApiError,
  kaluta,
  type OneToManyCapabilities,
  type OneToManyJob,
  type OneToManyOutput,
  type OneToManyTarget,
} from '@/lib/api'
import { sanitizeHtml } from '@/lib/richtext'
import { LANGUAGES } from '@/i18n'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { ONE_TO_MANY_OPTIONS } from '@/lib/oneToMany'


const langLabel = (code: string) => LANGUAGES.find((l) => l.code === code)?.label ?? code

const STATUS: Record<OneToManyOutput['status'], string> = {
  queued: 'Waiting',
  processing: 'Being made',
  completed: 'Ready',
  failed: 'Failed',
  cancelled: 'Withdrawn',
}

function StatusChip({ output }: { output: OneToManyOutput }) {
  const tone =
    output.status === 'completed'
      ? output.stale
        ? 'bg-gold/15 text-gold-soft'
        : 'bg-emerald-400/15 text-emerald-200'
      : output.status === 'failed'
        ? 'bg-red-400/15 text-red-200'
        : output.status === 'cancelled'
          ? 'bg-white/8 text-text-low'
          : 'bg-sky/15 text-sky'
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.7rem] font-semibold', tone)}>
      {output.status === 'processing' && <Loader2 size={11} className="animate-spin" aria-hidden="true" />}
      {output.status === 'completed' && output.stale ? 'Out of date' : STATUS[output.status]}
    </span>
  )
}

function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setDone(true)
          window.setTimeout(() => setDone(false), 1500)
        })
      }}
      className="inline-flex items-center gap-1 rounded-full bg-white/6 px-2.5 py-1 text-xs font-semibold text-text-mid hover:text-text-hi"
    >
      {done ? <Check size={12} aria-hidden="true" /> : <Copy size={12} aria-hidden="true" />}
      {done ? 'Copied' : label}
    </button>
  )
}

const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/** The draft's body as paragraphs of plain text, for editing. The subject
 *  heading is left out: it has its own field, and goes back on save. */
function bodyParagraphs(html: string): string {
  const doc = new DOMParser().parseFromString(sanitizeHtml(html), 'text/html')
  return Array.from(doc.body.querySelectorAll('p, h3, li'))
    .map((el) => {
      const link = el.querySelector('a[href]')
      return link && el.textContent?.trim() === link.textContent?.trim()
        ? `${link.textContent?.trim()}: ${link.getAttribute('href')}`
        : (el.textContent ?? '').trim()
    })
    .filter(Boolean)
    .join('\n\n')
}

/** Paragraphs back to HTML under the subject; a line "label: https://…"
 *  becomes a link again. */
function paragraphsToHtml(subject: string, text: string): string {
  return `<h2>${escapeHtml(subject.trim())}</h2>
` + text
    .split(/\n{2,}|\r?\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => {
      const link = p.match(/^(.{1,80}):\s+(https?:\/\/\S+)$/)
      return link
        ? `<p><a href="${escapeHtml(link[2])}">${escapeHtml(link[1])}</a></p>`
        : `<p>${escapeHtml(p)}</p>`
    })
    .join('\n')
}

function Failure({ output, onRetry, busy }: { output: OneToManyOutput; onRetry: () => void; busy: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-red-200">
      <AlertTriangle size={13} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        {output.error ?? 'It could not be made.'}
        {output.attempts > 0 && ` (${output.attempts} of ${output.max_attempts} tries)`}
      </span>
      <button
        type="button"
        onClick={onRetry}
        disabled={busy}
        className="inline-flex items-center gap-1 rounded-full bg-white/8 px-2.5 py-1 font-semibold text-text-hi disabled:opacity-40"
      >
        <RotateCcw size={12} aria-hidden="true" /> Try again
      </button>
    </div>
  )
}

function Section({ title, icon: Icon, children, defaultOpen }: { title: string; icon: typeof Clapperboard; children: React.ReactNode; defaultOpen: boolean }) {
  return (
    <details open={defaultOpen} className="group rounded-card-md bg-white/4 p-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-text-hi">
        <Icon size={15} className="text-gold" aria-hidden="true" />
        {title}
        <span className="ms-auto text-xs text-text-low group-open:hidden">Show</span>
      </summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  )
}

function NewsletterOutput({
  output,
  busy,
  onSave,
  onRetry,
}: {
  output: OneToManyOutput
  busy: boolean
  onSave: (input: { subject: string; preview_text: string; html: string }) => Promise<boolean>
  onRetry: (force: boolean) => void
}) {
  const [editing, setEditing] = useState(false)
  const [subject, setSubject] = useState('')
  const [preview, setPreview] = useState('')
  const [body, setBody] = useState('')
  const html = output.html ?? ''

  const start = () => {
    setSubject(output.title ?? '')
    setPreview(output.preview_text ?? '')
    setBody(bodyParagraphs(html))
    setEditing(true)
  }

  if (editing) {
    return (
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          void onSave({ subject, preview_text: preview, html: paragraphsToHtml(subject, body) }).then((ok) => ok && setEditing(false))
        }}
      >
        <label className="block text-xs text-text-low">
          Subject
          <input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={300} required
            className="mt-1 w-full rounded-card-sm bg-white/6 px-3 py-2 text-sm text-text-hi focus:outline-none" />
        </label>
        <label className="block text-xs text-text-low">
          Preview line
          <input value={preview} onChange={(e) => setPreview(e.target.value)} maxLength={300}
            className="mt-1 w-full rounded-card-sm bg-white/6 px-3 py-2 text-sm text-text-hi focus:outline-none" />
        </label>
        <label className="block text-xs text-text-low">
          Body, one paragraph per line
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={8} required
            className="mt-1 w-full rounded-card-sm bg-white/6 px-3 py-2 text-sm text-text-hi focus:outline-none" />
        </label>
        <div className="flex gap-2">
          <button type="submit" disabled={busy} className="rounded-full bg-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40">
            Save draft
          </button>
          <button type="button" onClick={() => setEditing(false)} className="rounded-full px-3 py-1.5 text-xs font-semibold text-text-mid">
            Cancel
          </button>
        </div>
      </form>
    )
  }

  return (
    <div className="space-y-2">
      <p className="text-sm font-semibold text-text-hi">{output.title}</p>
      {output.preview_text && <p className="text-xs text-text-low">{output.preview_text}</p>}
      <div
        className="prose-kinjy max-h-64 overflow-y-auto rounded-card-sm bg-text-hi/[0.05] p-3 text-sm text-text-mid [&_a]:text-gold [&_p]:mb-2"
        dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }}
      />
      {output.warning && <p className="text-xs text-gold-soft">{output.warning}</p>}
      <p className="text-xs text-text-low">
        A draft{output.user_edited ? ', edited by you' : ''}. Kinjy does not send it: copy it into the tool you send your newsletter with.
      </p>
      <div className="flex flex-wrap gap-1.5">
        <CopyButton text={output.content ?? ''} label="Copy text" />
        <CopyButton text={html} label="Copy HTML" />
        <button type="button" onClick={start}
          className="inline-flex items-center gap-1 rounded-full bg-white/6 px-2.5 py-1 text-xs font-semibold text-text-mid hover:text-text-hi">
          <Pencil size={12} aria-hidden="true" /> Edit
        </button>
        {output.stale && (
          <button type="button" disabled={busy}
            onClick={() => {
              if (!output.user_edited || window.confirm('Make the draft again from the post as it is now? Your edits will be replaced.')) {
                onRetry(output.user_edited)
              }
            }}
            className="inline-flex items-center gap-1 rounded-full bg-gold/15 px-2.5 py-1 text-xs font-semibold text-gold-soft disabled:opacity-40">
            <RotateCcw size={12} aria-hidden="true" /> Make again from the edited post
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * What One-to-Many made from a post, as it is being made: each format with its
 * own state, the failed ones with the reason and a way to try again.
 */
export function OneToManyPanel({ postId, lang }: { postId: string; lang: string }) {
  const [job, setJob] = useState<OneToManyJob | null | undefined>(undefined)
  const [caps, setCaps] = useState<OneToManyCapabilities | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [wanted, setWanted] = useState<OneToManyTarget[]>([])
  const wide = typeof window !== 'undefined' && window.matchMedia('(min-width: 640px)').matches

  const load = useCallback(async () => {
    try {
      setJob((await kaluta.creators.jobForPost(postId)).job)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load what was made from this post.')
      setJob(null)
    }
  }, [postId])

  useEffect(() => {
    void load()
    kaluta.creators.capabilities(lang).then((r) => setCaps(r.targets)).catch(() => setCaps(null))
  }, [load, lang])

  const active = job?.outputs.some((o) => o.status === 'queued' || o.status === 'processing')
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(), 3000)
    return () => window.clearInterval(timer)
  }, [active, load])

  const act = async (run: () => Promise<OneToManyJob>) => {
    setBusy(true)
    setError(null)
    try {
      setJob(await run())
      return true
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not work. Try again.')
      return false
    } finally {
      setBusy(false)
    }
  }

  if (job === undefined) {
    return <p className="py-6 text-center text-sm text-text-low">Loading…</p>
  }

  const outputs = job?.outputs ?? []
  const has = new Set(outputs.map((o) => o.target))
  const missing = ONE_TO_MANY_OPTIONS.filter((o) => !has.has(o.id))
  const failed = outputs.filter((o) => o.status === 'failed').length
  const video = outputs.find((o) => o.target === 'short_video')
  const newsletter = outputs.find((o) => o.target === 'newsletter')
  const translations = outputs.filter((o) => o.target === 'translation')
  const retry = (o: OneToManyOutput, force = false) => void act(() => kaluta.creators.retryOutput(o.id, force))

  return (
    <div className="space-y-3">
      {job?.source.deleted && (
        <p className="rounded-card-sm bg-white/6 px-3 py-2 text-sm text-text-mid">
          The post was deleted, so what was made from it has been withdrawn.
        </p>
      )}
      {error && <p role="alert" className="text-sm text-red-200">{error}</p>}

      {failed > 1 && !job?.source.deleted && (
        <button type="button" disabled={busy} onClick={() => void act(() => kaluta.creators.retryFailed(job!.job_id))}
          className="inline-flex items-center gap-1.5 rounded-full bg-white/8 px-3 py-1.5 text-xs font-semibold text-text-hi disabled:opacity-40">
          <RotateCcw size={12} aria-hidden="true" /> Try the {failed} failed ones again
        </button>
      )}

      {video && (
        <Section title="Short video" icon={Clapperboard} defaultOpen={wide || video.status === 'completed'}>
          <div className="flex items-center gap-2"><StatusChip output={video} /></div>
          {video.status === 'completed' && video.video_url && (
            <>
              <video src={video.video_url} controls playsInline preload="metadata"
                className="mx-auto aspect-[9/16] max-h-[60svh] rounded-card-sm bg-black" />
              <div className="flex flex-wrap gap-1.5">
                {video.download_url && (
                  <a href={video.download_url} className="inline-flex items-center gap-1 rounded-full bg-white/6 px-2.5 py-1 text-xs font-semibold text-text-mid hover:text-text-hi">
                    <Download size={12} aria-hidden="true" /> Download MP4
                  </a>
                )}
                {video.stale && (
                  <button type="button" disabled={busy} onClick={() => retry(video)}
                    className="inline-flex items-center gap-1 rounded-full bg-gold/15 px-2.5 py-1 text-xs font-semibold text-gold-soft disabled:opacity-40">
                    <RotateCcw size={12} aria-hidden="true" /> Make again from the edited post
                  </button>
                )}
                {!video.stale && video.narrated === false && (
                  // Made before narration existed: silent. Remade only when the author asks.
                  <button type="button" disabled={busy} onClick={() => retry(video)}
                    className="inline-flex items-center gap-1 rounded-full bg-gold/15 px-2.5 py-1 text-xs font-semibold text-gold-soft disabled:opacity-40">
                    <RotateCcw size={12} aria-hidden="true" /> Make again with narration
                  </button>
                )}
              </div>
              {video.narrated === false && (
                <p className="text-xs text-warning">This video was made before narration was added, so it has no voice.</p>
              )}
              <p className="text-xs text-text-low">Only you can play this. It is yours to post or share as you choose.</p>
            </>
          )}
          {video.status === 'failed' && <Failure output={video} busy={busy} onRetry={() => retry(video)} />}
          {video.status === 'cancelled' && video.error && <p className="text-xs text-text-low">{video.error}</p>}
        </Section>
      )}

      {newsletter && (
        <Section title="Newsletter draft" icon={Newspaper} defaultOpen={wide}>
          <div className="flex items-center gap-2"><StatusChip output={newsletter} /></div>
          {newsletter.status === 'completed' && newsletter.html && (
            <NewsletterOutput output={newsletter} busy={busy}
              onSave={(input) => act(() => kaluta.creators.editNewsletter(newsletter.id, input))}
              onRetry={(force) => retry(newsletter, force)} />
          )}
          {newsletter.status === 'failed' && <Failure output={newsletter} busy={busy} onRetry={() => retry(newsletter)} />}
          {newsletter.status === 'cancelled' && newsletter.error && <p className="text-xs text-text-low">{newsletter.error}</p>}
        </Section>
      )}

      {translations.length > 0 && (
        <Section title="Translations" icon={Languages} defaultOpen={wide}>
          {translations.map((t) => (
            <div key={t.id} className="space-y-1.5 border-b border-white/6 pb-3 last:border-0 last:pb-0">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-text-hi">{langLabel(t.lang)}</span>
                <StatusChip output={t} />
                {t.status === 'completed' && t.content && (
                  <span className="ms-auto flex gap-1.5">
                    {t.stale && (
                      <button type="button" disabled={busy} onClick={() => retry(t)}
                        className="inline-flex items-center gap-1 rounded-full bg-gold/15 px-2.5 py-1 text-xs font-semibold text-gold-soft disabled:opacity-40">
                        <RotateCcw size={12} aria-hidden="true" /> Again
                      </button>
                    )}
                    <CopyButton text={t.content} />
                  </span>
                )}
                {t.status === 'queued' && (
                  <button type="button" disabled={busy} onClick={() => void act(() => kaluta.creators.cancelOutput(t.id))}
                    className="ms-auto inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs text-text-low hover:text-text-hi">
                    <X size={12} aria-hidden="true" /> Cancel
                  </button>
                )}
              </div>
              {t.status === 'completed' && t.content && (
                <p dir={t.rtl ? 'rtl' : 'ltr'} lang={t.lang}
                  className={cn('whitespace-pre-line text-sm text-text-mid', t.rtl && 'text-right')}>
                  {t.content}
                </p>
              )}
              {t.status === 'failed' && <Failure output={t} busy={busy} onRetry={() => retry(t)} />}
              {t.status === 'cancelled' && t.error && <p className="text-xs text-text-low">{t.error}</p>}
            </div>
          ))}
          <p className="text-xs text-text-low">Machine translations, kept here for you. They are not posted.</p>
        </Section>
      )}

      {!job?.source.deleted && missing.length > 0 && (
        <div className="rounded-card-md bg-white/4 p-3">
          <p className="mb-2 text-sm font-semibold text-text-hi">{job ? 'Also make' : 'Make from this post'}</p>
          <div className="flex flex-wrap gap-1.5">
            {missing.map((o) => {
              const cap = caps?.[o.id]
              const on = wanted.includes(o.id)
              return (
                <button key={o.id} type="button" aria-pressed={on} disabled={!cap?.available}
                  title={cap && !cap.available ? cap.reason ?? undefined : undefined}
                  onClick={() => setWanted((w) => (on ? w.filter((x) => x !== o.id) : [...w, o.id]))}
                  className={cn('inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold disabled:opacity-40',
                    on ? 'bg-gold text-ink' : 'bg-white/6 text-text-mid hover:text-text-hi')}>
                  <o.icon size={12} aria-hidden="true" /> {o.label}
                </button>
              )
            })}
          </div>
          {missing.some((o) => caps && !caps[o.id].available) && (
            <ul className="mt-2 space-y-0.5 text-xs text-text-low">
              {missing.filter((o) => caps && !caps[o.id].available).map((o) => (
                <li key={o.id}>{o.label}: {caps?.[o.id].reason}</li>
              ))}
            </ul>
          )}
          <button type="button" disabled={busy || wanted.length === 0}
            onClick={() => void act(() => kaluta.creators.publish({ source_ref: postId, targets: wanted })).then((ok) => ok && setWanted([]))}
            className="mt-3 rounded-full bg-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40">
            Start
          </button>
        </div>
      )}
    </div>
  )
}

export function OneToManyDialog({ postId, lang, open, onOpenChange }: { postId: string; lang: string; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base">Other formats of this post</DialogTitle>
        </DialogHeader>
        {open && <OneToManyPanel postId={postId} lang={lang} />}
      </DialogContent>
    </Dialog>
  )
}
