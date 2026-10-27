import { useEffect, useRef, useState } from 'react'
import { BarChart3, FileText, Heading, Link2, List, Paperclip, Save, ScrollText, Send, X } from 'lucide-react'
import { ApiError, kaluta, type CommunityEvent, type ForumDetail, type ThreadBrief, type ThreadKind } from '@/lib/api'
import { cn } from '@/lib/utils'
import RichText from './RichText'
import { KIND, countryName, field } from './forumText'

const LANGS = [
  ['en', 'English'],
  ['fr', 'Français'],
  ['sw', 'Kiswahili'],
  ['ar', 'العربية'],
  ['pt', 'Português'],
  ['es', 'Español'],
  ['zh', '中文'],
  ['ha', 'Hausa'],
  ['yo', 'Yorùbá'],
  ['am', 'አማርኛ'],
] as const

/**
 * Writing a thread: what kind it is, the forum's rules, questions that may
 * already be answered, formatting, files, an optional poll, and a draft kept
 * on the server so it can be finished later.
 */
export default function ThreadComposer({
  forum,
  onPosted,
  onCancel,
  onOpenThread,
}: {
  forum: ForumDetail
  onPosted: (threadId: string) => void
  onCancel: () => void
  onOpenThread: (threadId: string) => void
}) {
  const [kind, setKind] = useState<ThreadKind>('question')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [tags, setTags] = useState('')
  const [lang, setLang] = useState<string>(forum.lang ?? (navigator.language || 'en').slice(0, 2))
  const [country, setCountry] = useState(forum.country ?? '')
  const [region, setRegion] = useState('')
  const [commercial, setCommercial] = useState(false)
  const [files, setFiles] = useState<Array<{ id: string; name: string }>>([])
  const [uploading, setUploading] = useState(false)
  const [withPoll, setWithPoll] = useState(false)
  const [pollQuestion, setPollQuestion] = useState('')
  const [pollOptions, setPollOptions] = useState(['', ''])
  const [eventId, setEventId] = useState('')
  const [events, setEvents] = useState<CommunityEvent[]>([])
  const [sessionId, setSessionId] = useState('')
  const [preview, setPreview] = useState(false)
  const [related, setRelated] = useState<ThreadBrief[]>([])
  const [draftId, setDraftId] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Awaited<ReturnType<typeof kaluta.forums.drafts>>['items']>([])
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const openSessions = forum.sessions.filter((s) => s.open)

  useEffect(() => {
    kaluta.forums.drafts(forum.id).then((r) => setDrafts(r.items)).catch(() => setDrafts([]))
    if (forum.community) {
      kaluta.communityPaid.events(forum.community.id).then((r) => setEvents(r.items)).catch(() => setEvents([]))
    }
  }, [forum.id, forum.community])

  // Questions that may already be answered, as the title is typed. Only threads
  // the member could open come back.
  useEffect(() => {
    if (title.trim().length < 8) {
      setRelated([])
      return
    }
    const timer = window.setTimeout(() => {
      kaluta.forums.related(title.trim(), forum.id).then((r) => setRelated(r.items)).catch(() => setRelated([]))
    }, 500)
    return () => window.clearTimeout(timer)
  }, [title, forum.id])

  const insert = (before: string, after = '') => {
    const el = bodyRef.current
    const start = el?.selectionStart ?? body.length
    const end = el?.selectionEnd ?? body.length
    const next = body.slice(0, start) + before + body.slice(start, end) + after + body.slice(end)
    setBody(next)
    window.setTimeout(() => el?.focus(), 0)
  }

  const attach = async (list: FileList | null) => {
    if (!list?.length) return
    setUploading(true)
    setNote(null)
    try {
      for (const file of Array.from(list).slice(0, 6 - files.length)) {
        // Uploaded as a private attachment: served only on links signed for
        // somebody who can read the forum.
        const asset = await kaluta.media.uploadWithProgress(file, () => undefined, { purpose: 'chat' })
        setFiles((current) => [...current, { id: asset.id, name: file.name }])
      }
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'The file could not be uploaded')
    } finally {
      setUploading(false)
    }
  }

  const saveDraft = async () => {
    setNote(null)
    try {
      const saved = await kaluta.forums.saveDraft({
        id: draftId ?? undefined,
        forum_id: forum.id,
        title,
        body,
        kind,
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        lang: lang || undefined,
        country: country ? country.toUpperCase() : undefined,
        region: region || undefined,
      })
      setDraftId(saved.id)
      setNote('Draft saved. Only you can see it.')
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not save the draft')
    }
  }

  const publish = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setNote(null)
    try {
      const created = await kaluta.forums.createThread(forum.id, {
        title: title.trim(),
        body: body.trim(),
        kind,
        lang: lang || 'en',
        tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
        country: country ? country.toUpperCase() : undefined,
        region: region.trim() || undefined,
        commercial,
        attachments: files.map((f) => f.id),
        poll: withPoll ? { question: pollQuestion.trim(), options: pollOptions.map((o) => o.trim()).filter(Boolean) } : undefined,
        event_id: eventId || undefined,
        session_id: sessionId || undefined,
        draft_id: draftId ?? undefined,
      })
      onPosted(created.id)
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not post the thread')
    } finally {
      setBusy(false)
    }
  }

  const kinds = (Object.keys(KIND) as ThreadKind[]).filter((k) => k !== 'announcement' || forum.can_announce)
  return (
    <form onSubmit={publish} className="mt-4 space-y-3 border-t border-white/8 pt-4">
      <h3 className="text-sm font-bold text-text-hi">New thread</h3>
      {forum.rules && (
        <details className="rounded-xl border border-gold/30 bg-gold/5 px-3 py-2">
          <summary className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-gold-soft">
            <ScrollText size={13} aria-hidden="true" />
            This forum’s rules
          </summary>
          <p className="mt-1 whitespace-pre-wrap text-xs text-text-mid">{forum.rules}</p>
        </details>
      )}
      {drafts.length > 0 && !draftId && (
        <div className="rounded-xl border border-white/10 px-3 py-2 text-xs">
          <p className="font-semibold text-text-hi">Your drafts here</p>
          <ul className="mt-1 space-y-1">
            {drafts.map((d) => (
              <li key={d.id} className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setDraftId(d.id)
                    setTitle(d.title)
                    setBody(d.body)
                    setKind(d.kind)
                    setTags(d.tags.join(', '))
                    if (d.lang) setLang(d.lang)
                    setCountry(d.country ?? '')
                    setRegion(d.region ?? '')
                  }}
                  className="min-w-0 flex-1 truncate text-start text-gold-soft hover:underline"
                >
                  {d.title || 'Untitled draft'} · {new Date(d.updated_at).toLocaleDateString()}
                </button>
                <button type="button" aria-label="Delete this draft" onClick={() => void kaluta.forums.deleteDraft(d.id).then(() => setDrafts((x) => x.filter((y) => y.id !== d.id)))} className="text-text-low hover:text-red-300">
                  <X size={13} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <fieldset>
        <legend className="mb-1.5 text-xs font-semibold text-text-hi">What kind of thread?</legend>
        <div role="radiogroup" className="grid gap-2 sm:grid-cols-2">
          {kinds.map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={kind === k}
              onClick={() => setKind(k)}
              className={cn('rounded-xl border px-3 py-2 text-start', kind === k ? 'border-gold/60 bg-gold/10' : 'border-white/10 hover:border-white/20')}
            >
              <span className="block text-sm font-semibold text-text-hi">{KIND[k].label}</span>
              <span className="block text-xs text-text-low">{KIND[k].hint}</span>
            </button>
          ))}
        </div>
      </fieldset>

      <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} placeholder={kind === 'question' ? 'Your question, in one line' : 'Title'} aria-label="Title" className={field} autoFocus />
      {related.length > 0 && (
        <div className="rounded-xl border border-sky-400/25 bg-sky-500/5 px-3 py-2">
          <p className="text-xs font-semibold text-sky">Maybe already answered</p>
          <ul className="mt-1 space-y-1">
            {related.map((r) => (
              <li key={r.id}>
                <button type="button" onClick={() => onOpenThread(r.id)} className="text-start text-xs text-text-mid hover:text-text-hi hover:underline">
                  {r.title}
                  <span className="text-text-low"> · {r.forum_name}{r.solved ? ' · solved' : ''}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1">
        <button type="button" onClick={() => insert('\n## ')} className="rounded-lg px-2 py-1 text-text-mid hover:bg-white/8" aria-label="Insert a heading" title="Heading">
          <Heading size={14} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => insert('\n- ')} className="rounded-lg px-2 py-1 text-text-mid hover:bg-white/8" aria-label="Insert a list item" title="List">
          <List size={14} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => insert('*', '*')} className="rounded-lg px-2 py-1 text-xs font-bold text-text-mid hover:bg-white/8" aria-label="Bold" title="Bold">
          B
        </button>
        <button type="button" onClick={() => insert('https://')} className="rounded-lg px-2 py-1 text-text-mid hover:bg-white/8" aria-label="Insert a link" title="Link">
          <Link2 size={14} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => setPreview((v) => !v)} aria-pressed={preview} className="ms-auto rounded-lg px-2 py-1 text-xs font-semibold text-text-mid hover:bg-white/8">
          {preview ? 'Edit' : 'Preview'}
        </button>
      </div>
      {preview ? (
        <div className="min-h-[120px] rounded-xl border border-white/10 p-3">
          <RichText text={body || 'Nothing written yet.'} />
        </div>
      ) : (
        <textarea
          ref={bodyRef}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={7}
          maxLength={40000}
          placeholder={kind === 'question' ? 'What you tried, what happened, what you need. @name to call someone in.' : 'Write here. # for a heading, - for a list, *bold*.'}
          aria-label="Thread text"
          className={`${field} resize-y`}
        />
      )}

      <details className="rounded-xl border border-white/10 px-3 py-2">
        <summary className="cursor-pointer text-xs font-semibold text-text-mid">Topics, language and place</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Topics, separated by commas" aria-label="Topics" className={field} />
          <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language of the thread" className={field}>
            {LANGS.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
          <input value={country} onChange={(e) => setCountry(e.target.value.toUpperCase().slice(0, 2))} placeholder="Country it is about (CI, KE…) - optional" aria-label="Country code" className={field} />
          <input value={region} onChange={(e) => setRegion(e.target.value)} maxLength={120} placeholder="Region or city - optional" aria-label="Region" className={field} />
        </div>
        {country.length === 2 && <p className="mt-1 text-xs text-text-low">{countryName(country)}. Only the place the question is about; nothing about where you are.</p>}
      </details>

      <div className="flex flex-wrap gap-2">
        <input ref={fileRef} type="file" multiple hidden onChange={(e) => void attach(e.target.files)} />
        <button type="button" disabled={uploading || files.length >= 6} onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1.5 rounded-full border border-white/10 px-3 py-1.5 text-xs font-semibold text-text-mid hover:text-text-hi disabled:opacity-50">
          <Paperclip size={13} aria-hidden="true" />
          {uploading ? 'Uploading…' : 'Attach a photo, video or document'}
        </button>
        <button type="button" aria-pressed={withPoll} onClick={() => setWithPoll((v) => !v)} className={cn('inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold', withPoll ? 'border-gold/60 text-gold-soft' : 'border-white/10 text-text-mid')}>
          <BarChart3 size={13} aria-hidden="true" />
          {withPoll ? 'Remove the poll' : 'Add a poll'}
        </button>
      </div>
      {files.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {files.map((f) => (
            <li key={f.id} className="inline-flex items-center gap-1 rounded-full bg-text-hi/[0.07] px-2.5 py-1 text-xs text-text-mid">
              <FileText size={12} aria-hidden="true" />
              {f.name}
              <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles((x) => x.filter((y) => y.id !== f.id))}>
                <X size={12} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {withPoll && (
        <div className="space-y-2 rounded-xl border border-white/10 p-3">
          <input value={pollQuestion} onChange={(e) => setPollQuestion(e.target.value)} maxLength={300} placeholder="Poll question" aria-label="Poll question" className={field} />
          {pollOptions.map((o, i) => (
            <input key={i} value={o} onChange={(e) => setPollOptions((all) => all.map((x, j) => (j === i ? e.target.value : x)))} maxLength={200} placeholder={`Answer ${i + 1}`} aria-label={`Answer ${i + 1}`} className={field} />
          ))}
          {pollOptions.length < 10 && (
            <button type="button" onClick={() => setPollOptions((all) => [...all, ''])} className="text-xs font-semibold text-gold-soft">
              + Add an answer
            </button>
          )}
        </div>
      )}
      {(events.length > 0 || openSessions.length > 0) && (
        <div className="grid gap-2 sm:grid-cols-2">
          {events.length > 0 && (
            <select value={eventId} onChange={(e) => setEventId(e.target.value)} aria-label="Link a community event" className={field}>
              <option value="">No event linked</option>
              {events.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  Event: {ev.title}
                </option>
              ))}
            </select>
          )}
          {openSessions.length > 0 && kind === 'question' && (
            <select value={sessionId} onChange={(e) => setSessionId(e.target.value)} aria-label="Ask in an expert session" className={field}>
              <option value="">Not in a session</option>
              {openSessions.map((s) => (
                <option key={s.id} value={s.id}>
                  Ask in: {s.title}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      <label className="flex items-start gap-2 text-xs text-text-mid">
        <input type="checkbox" className="mt-0.5 accent-[#E5B556]" checked={commercial} onChange={(e) => setCommercial(e.target.checked)} />
        <span>It promotes something I sell or am paid for. It will be labelled “Commercial”.</span>
      </label>

      {note && <p role="status" className="text-xs text-text-mid">{note}</p>}
      <div className="flex flex-wrap justify-end gap-2 pt-1">
        <button type="button" onClick={onCancel} className="min-h-[44px] rounded-xl px-4 text-xs font-semibold text-text-mid hover:bg-white/6">
          Cancel
        </button>
        <button type="button" onClick={() => void saveDraft()} disabled={!title.trim() && !body.trim()} className="inline-flex min-h-[44px] items-center gap-1.5 rounded-xl border border-white/10 px-4 text-xs font-semibold text-text-mid hover:text-text-hi disabled:opacity-40">
          <Save size={14} aria-hidden="true" />
          Save draft
        </button>
        <button type="submit" disabled={busy || title.trim().length < 3 || !body.trim()} className="inline-flex min-h-[44px] items-center gap-2 rounded-xl bg-gold px-5 text-xs font-bold text-ink disabled:opacity-40">
          <Send size={14} aria-hidden="true" />
          Publish
        </button>
      </div>
    </form>
  )
}
