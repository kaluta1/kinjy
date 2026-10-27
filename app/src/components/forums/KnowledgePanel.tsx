import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, ArrowUp, BookOpen, CheckCircle2, ExternalLink, Flag, History, Info, Pencil, Plus, Search, Sparkles } from 'lucide-react'
import { ApiError, kaluta, type KnowledgeEntry } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ReportDialog, Sheet } from './ForumDialogs'
import RichText from './RichText'
import { dateOf, field } from './forumText'

const SEARCH_DEBOUNCE_MS = 300

interface KnowledgePanelProps {
  forumId: string
  className?: string
  onOpenThread: (threadId: string) => void
}

type Status = 'loading' | 'ready' | 'error'

function names(entry: KnowledgeEntry): string {
  const list = (entry.contributors ?? []) as Array<string | { display_name?: string | null }>
  return list.map((c) => (typeof c === 'string' ? null : c.display_name)).filter(Boolean).join(', ')
}

function Provenance({ entry }: { entry: KnowledgeEntry }) {
  if (entry.origin === 'community') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-emerald-300">
        <CheckCircle2 size={12} aria-hidden="true" /> Accepted by the asker, upvoted by members
      </span>
    )
  }
  if (entry.status === 'draft') {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/30 bg-amber-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-amber-200">
        {entry.ai_generated ? <Sparkles size={12} aria-hidden="true" /> : <Pencil size={12} aria-hidden="true" />}
        {entry.ai_generated ? 'AI-generated draft, not reviewed' : 'Draft'}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-sky-400/30 bg-sky-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-sky">
      <BookOpen size={12} aria-hidden="true" /> {entry.ai_generated ? 'AI-generated, reviewed by an editor' : 'Article by the forum’s editors'}
    </span>
  )
}

/**
 * What a forum knows, and where each piece comes from.
 *
 * Two kinds of entry: an answer its asker accepted and other members upvoted
 * (the reply's own words), and articles the forum's knowledge editors write
 * from discussions - citing them, crediting their authors, with a revision
 * history. Nothing is presented as verified fact.
 */
export default function KnowledgePanel({ forumId, className, onOpenThread }: KnowledgePanelProps) {
  const [query, setQuery] = useState('')
  const [entries, setEntries] = useState<KnowledgeEntry[]>([])
  const [canEdit, setCanEdit] = useState(false)
  const [reviewDays, setReviewDays] = useState(180)
  const [status, setStatus] = useState<Status>('loading')
  const [writing, setWriting] = useState<KnowledgeEntry | 'new' | null>(null)
  const [history, setHistory] = useState<KnowledgeEntry | null>(null)
  const [suggesting, setSuggesting] = useState<KnowledgeEntry | null>(null)
  const [reporting, setReporting] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const reload = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    let current = true
    const timer = setTimeout(
      async () => {
        setStatus('loading')
        try {
          const result = await kaluta.forums.knowledge(forumId, query.trim())
          if (!current) return
          setEntries(result.items)
          setCanEdit(Boolean(result.can_edit))
          setReviewDays(result.review_days ?? 180)
          setStatus('ready')
        } catch {
          if (!current) return
          setEntries([])
          setStatus('error')
        }
      },
      query ? SEARCH_DEBOUNCE_MS : 0,
    )
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [forumId, query, version])

  return (
    <section aria-label="Knowledge base" className={cn('space-y-3', className)}>
      <details className="cloud-card p-4">
        <summary className="flex cursor-pointer items-center gap-2 text-sm font-semibold text-text-hi">
          <Info size={15} className="text-gold-soft" aria-hidden="true" />
          What is in Knowledge, and where it comes from
        </summary>
        <div className="mt-2 space-y-2 text-xs leading-relaxed text-text-mid">
          <p>
            <strong className="text-text-hi">Settled answers.</strong> When the person who asked a question accepts an answer and at least two members upvote it,
            it appears here in its author’s own words, with a link to the discussion.
          </p>
          <p>
            <strong className="text-text-hi">Articles.</strong> The forum’s knowledge editors (its moderators who have that right, and the community’s owner
            and moderators) write articles from the discussions. Each cites the threads and answers it draws on, credits their authors, lists any
            sources contributors gave, and keeps a history of every change. An editor can ask an AI for a first draft; it stays a draft, labelled as
            AI-generated, until an editor has checked it against its sources and published it.
          </p>
          <p>
            Nothing here is checked by Kinjy. An accepted answer is the asker’s choice. Articles not reviewed for {reviewDays} days are marked as such.
            Anyone who can read the forum can suggest a correction or say an article is out of date.
          </p>
        </div>
      </details>

      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-text-low" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search what this forum knows…"
            aria-label="Search the knowledge base"
            className="w-full rounded-2xl border border-white/10 bg-ink-2/60 py-3 pl-9 pr-4 text-sm text-text-hi placeholder:text-text-low transition-colors focus:border-gold/50 focus:outline-none"
          />
        </div>
        {canEdit && (
          <button type="button" onClick={() => setWriting('new')} className="inline-flex min-h-[44px] shrink-0 items-center gap-1.5 rounded-2xl bg-gold px-4 text-xs font-bold text-ink">
            <Plus size={14} aria-hidden="true" /> Article
          </button>
        )}
      </div>
      {note && <p role="status" className="text-xs text-text-mid">{note}</p>}

      {status === 'loading' && entries.length === 0 && (
        <div role="status" className="cloud-card p-8 text-center text-sm text-text-low">
          Loading…
        </div>
      )}
      {status === 'error' && (
        <div role="alert" className="cloud-card p-8 text-center text-sm text-text-low">
          Could not load the knowledge base. Try again in a moment.
        </div>
      )}
      {status === 'ready' && entries.length === 0 && (
        <div className="cloud-card p-8 text-center text-sm text-text-low">
          {query.trim()
            ? 'Nothing here matches your search.'
            : 'Nothing has been settled here yet. An answer shows up once its asker accepts it and two members upvote it; editors can also write articles.'}
        </div>
      )}

      <ul className="space-y-3" aria-busy={status === 'loading'}>
        {entries.map((entry) => (
          <li key={entry.id} className="cloud-card space-y-3 p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Provenance entry={entry} />
              {entry.needs_review && (
                <span className="inline-flex items-center gap-1 rounded-full border border-amber-400/30 bg-amber-500/10 px-2.5 py-0.5 text-[11px] font-semibold text-amber-200">
                  <AlertTriangle size={12} aria-hidden="true" /> Not reviewed for over {reviewDays} days
                </span>
              )}
              {entry.votes > 0 && (
                <span className="inline-flex items-center gap-1 text-xs text-text-low">
                  <ArrowUp size={12} aria-hidden="true" /> {entry.votes} {entry.votes === 1 ? 'upvote' : 'upvotes'}
                </span>
              )}
            </div>
            <h3 className="text-base font-semibold leading-snug text-text-hi">{entry.question}</h3>
            <RichText text={entry.answer} />
            {(entry.source_links?.length ?? 0) > 0 && (
              <div>
                <p className="text-xs font-semibold text-text-hi">Sources given by contributors</p>
                <ul className="mt-1 space-y-0.5">
                  {entry.source_links!.map((l) => (
                    <li key={l.url}>
                      <a href={l.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 text-xs text-gold-soft hover:underline">
                        <ExternalLink size={11} aria-hidden="true" /> {l.label ?? l.url}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <p className="text-xs text-text-low">
              {names(entry) && <>By {names(entry)} · </>}
              Published {dateOf(entry.published_at ?? entry.created_at)}
              {entry.updated_at && <> · updated {dateOf(entry.updated_at)}</>}
              {entry.reviewed_at && <> · reviewed {dateOf(entry.reviewed_at)}</>}
              {entry.tags?.length ? <> · {entry.tags.map((t) => `#${t}`).join(' ')}</> : null}
            </p>
            <div className="flex flex-wrap gap-2 border-t border-white/6 pt-3">
              {entry.sources.map((threadId, index) => (
                <button key={threadId} type="button" onClick={() => onOpenThread(threadId)} className="inline-flex min-h-[40px] items-center rounded-xl border border-white/10 bg-white/4 px-3 text-xs font-semibold text-gold hover:border-gold/40">
                  {entry.sources.length > 1 ? `Read discussion ${index + 1}` : 'Read the discussion'}
                </button>
              ))}
              {entry.origin === 'curated' && entry.status !== 'draft' && (
                <button type="button" onClick={() => setSuggesting(entry)} className="inline-flex min-h-[40px] items-center rounded-xl px-3 text-xs font-semibold text-text-mid hover:text-text-hi">
                  Suggest a correction
                </button>
              )}
              {entry.origin === 'curated' && (
                <button type="button" onClick={() => setReporting(entry.id)} aria-label="Report this article" className="inline-flex min-h-[40px] items-center rounded-xl px-2 text-text-low hover:text-red-300">
                  <Flag size={13} aria-hidden="true" />
                </button>
              )}
              {canEdit && entry.origin === 'curated' && (
                <>
                  <button type="button" onClick={() => setWriting(entry)} className="inline-flex min-h-[40px] items-center gap-1 rounded-xl px-3 text-xs font-semibold text-text-mid hover:text-text-hi">
                    <Pencil size={12} aria-hidden="true" /> Edit or review
                  </button>
                  <button type="button" onClick={() => setHistory(entry)} className="inline-flex min-h-[40px] items-center gap-1 rounded-xl px-3 text-xs font-semibold text-text-mid hover:text-text-hi">
                    <History size={12} aria-hidden="true" /> History and suggestions
                  </button>
                  {entry.status === 'draft' && (
                    <button
                      type="button"
                      onClick={() =>
                        void kaluta.forums.article
                          .publish(entry.id)
                          .then(() => {
                            setNote('Published. You vouch that it reflects its sources, not that it is true.')
                            reload()
                          })
                          .catch((err) => setNote(err instanceof ApiError ? err.message : 'Could not publish'))
                      }
                      className="inline-flex min-h-[40px] items-center rounded-xl bg-gold px-3 text-xs font-bold text-ink"
                    >
                      I checked it: publish
                    </button>
                  )}
                </>
              )}
            </div>
          </li>
        ))}
      </ul>

      {writing && (
        <ArticleEditor
          forumId={forumId}
          entry={writing === 'new' ? null : writing}
          onClose={() => setWriting(null)}
          onSaved={(said) => {
            setWriting(null)
            setNote(said)
            reload()
          }}
        />
      )}
      {history && <ArticleHistory entry={history} onClose={() => setHistory(null)} />}
      {suggesting && (
        <SuggestDialog
          entry={suggesting}
          onClose={() => setSuggesting(null)}
          onSent={() => {
            setSuggesting(null)
            setNote('Thank you. The forum’s editors will look at it.')
          }}
        />
      )}
      {reporting && <ReportDialog kind="knowledge" id={reporting} onClose={() => setReporting(null)} />}
    </section>
  )
}

function ArticleEditor({ forumId, entry, onClose, onSaved }: { forumId: string; entry: KnowledgeEntry | null; onClose: () => void; onSaved: (said: string) => void }) {
  const [question, setQuestion] = useState(entry?.question ?? '')
  const [answer, setAnswer] = useState(entry?.answer ?? '')
  const [threads, setThreads] = useState((entry?.sources ?? []).join(', '))
  const [links, setLinks] = useState((entry?.source_links ?? []).map((l) => (l.label ? `${l.label} | ${l.url}` : l.url)).join('\n'))
  const [tags, setTags] = useState((entry?.tags ?? []).join(', '))
  const [changeNote, setChangeNote] = useState('')
  const [reviewed, setReviewed] = useState(false)
  const [publish, setPublish] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const parsedLinks = links
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => (l.includes('|') ? { label: l.split('|')[0].trim(), url: l.split('|')[1].trim() } : { url: l }))
  const save = () => {
    setError(null)
    const done = entry
      ? kaluta.forums.article.edit(entry.id, { question, answer, source_links: parsedLinks, tags: tags.split(',').map((t) => t.trim()).filter(Boolean), note: changeNote.trim() || undefined, mark_reviewed: reviewed }).then(() => 'Saved. The change is in the article’s history.')
      : kaluta.forums.article
          .create(forumId, {
            question,
            answer,
            source_thread_ids: threads.split(',').map((t) => (t.includes('thread=') ? t.split('thread=')[1].split('&')[0] : t).trim()).filter(Boolean),
            source_links: parsedLinks,
            tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
            publish,
          })
          .then((r) => (r.status === 'draft' ? 'Saved as a draft.' : 'Published.'))
    done.then(onSaved).catch((err) => setError(err instanceof ApiError ? err.message : 'Could not save the article'))
  }
  return (
    <Sheet title={entry ? 'Edit or review the article' : 'New knowledge article'} onClose={onClose} wide>
      <div className="space-y-2">
        {entry?.ai_generated && <p className="rounded-xl border border-amber-400/30 bg-amber-500/10 p-2 text-xs text-amber-200">An AI wrote this from the cited answers. Check every statement against them; remove anything they do not say.</p>}
        <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={500} placeholder="The question it answers" aria-label="Question" className={field} />
        <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={8} maxLength={40000} placeholder="The answer. # headings, - lists and links work." aria-label="Answer" className={`${field} resize-y`} />
        {!entry && <input value={threads} onChange={(e) => setThreads(e.target.value)} placeholder="Discussions it comes from: their links or ids, separated by commas" aria-label="Source discussions" className={field} />}
        <textarea value={links} onChange={(e) => setLinks(e.target.value)} rows={3} placeholder={'Sources contributors gave, one per line:\nLabel | https://…'} aria-label="Source links" className={`${field} resize-none`} />
        <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="Topics, separated by commas" aria-label="Topics" className={field} />
        {entry ? (
          <>
            <input value={changeNote} onChange={(e) => setChangeNote(e.target.value)} maxLength={500} placeholder="What you changed (kept in the history)" aria-label="What you changed" className={field} />
            <label className="flex items-center gap-2 text-xs text-text-mid">
              <input type="checkbox" className="accent-[#E5B556]" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
              I checked that it is still accurate (sets the review date)
            </label>
          </>
        ) : (
          <label className="flex items-center gap-2 text-xs text-text-mid">
            <input type="checkbox" className="accent-[#E5B556]" checked={publish} onChange={(e) => setPublish(e.target.checked)} />
            Publish now (otherwise it is a draft only editors see)
          </label>
        )}
        <p className="text-xs text-text-low">The authors of the discussions it cites are credited, and it can only be read by people who can read all of them.</p>
        {error && <p role="alert" className="text-xs text-red-200">{error}</p>}
        <button type="button" onClick={save} disabled={question.trim().length < 3 || answer.trim().length < 10} className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-50">
          Save
        </button>
      </div>
    </Sheet>
  )
}

function ArticleHistory({ entry, onClose }: { entry: KnowledgeEntry; onClose: () => void }) {
  const [revisions, setRevisions] = useState<Awaited<ReturnType<typeof kaluta.forums.article.revisions>>['items'] | null>(null)
  const [suggestions, setSuggestions] = useState<Awaited<ReturnType<typeof kaluta.forums.article.suggestions>>['items'] | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const load = useCallback(() => {
    kaluta.forums.article.revisions(entry.id).then((r) => setRevisions(r.items)).catch(() => setRevisions([]))
    kaluta.forums.article.suggestions(entry.id).then((r) => setSuggestions(r.items)).catch(() => setSuggestions([]))
  }, [entry.id])
  useEffect(load, [load])
  return (
    <Sheet title="History and suggestions" onClose={onClose} wide>
      <h3 className="text-sm font-bold text-text-hi">Suggestions from members</h3>
      <ul className="mt-2 space-y-2">
        {(suggestions ?? []).map((s) => (
          <li key={s.id} className="rounded-xl border border-white/10 p-3 text-sm">
            <p className="text-xs font-semibold text-gold-soft">{s.kind === 'outdated' ? 'May be out of date' : 'Correction'} · {s.by ?? 'A member'} · {dateOf(s.created_at)}</p>
            <p className="mt-1 whitespace-pre-wrap text-text-mid">{s.note}</p>
            {s.status === 'open' ? (
              <button type="button" onClick={() => void kaluta.forums.article.resolveSuggestion(s.id).then(load)} className="mt-1 text-xs font-semibold text-gold-soft underline">
                Mark as handled
              </button>
            ) : (
              <p className="mt-1 text-xs text-text-low">Handled</p>
            )}
          </li>
        ))}
        {suggestions && suggestions.length === 0 && <li className="text-xs text-text-low">No suggestion.</li>}
      </ul>
      <h3 className="mt-5 text-sm font-bold text-text-hi">Revisions</h3>
      <ul className="mt-2 space-y-2">
        {(revisions ?? []).map((r) => (
          <li key={r.id} className="rounded-xl border border-white/10 p-3 text-sm">
            <button type="button" onClick={() => setOpen((v) => (v === r.id ? null : r.id))} className="w-full text-start">
              <span className="text-xs font-semibold text-text-hi">{r.note ?? 'Edited'}</span>
              <span className="block text-xs text-text-low">{r.editor ?? 'An editor'} · {new Date(r.created_at).toLocaleString()}</span>
            </button>
            {open === r.id && (
              <div className="mt-2 border-t border-white/10 pt-2">
                <p className="font-semibold text-text-hi">{r.question}</p>
                <RichText text={r.answer} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </Sheet>
  )
}

function SuggestDialog({ entry, onClose, onSent }: { entry: KnowledgeEntry; onClose: () => void; onSent: () => void }) {
  const [kind, setKind] = useState<'correction' | 'outdated'>('correction')
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <Sheet title="Suggest a correction" onClose={onClose}>
      <div role="radiogroup" className="flex gap-2">
        {(
          [
            ['correction', 'Something is wrong'],
            ['outdated', 'It may be out of date'],
          ] as const
        ).map(([id, label]) => (
          <button key={id} type="button" role="radio" aria-checked={kind === id} onClick={() => setKind(id)} className={`rounded-full border px-3 py-1 text-xs font-semibold ${kind === id ? 'border-gold/60 bg-gold/15 text-gold-soft' : 'border-white/10 text-text-mid'}`}>
            {label}
          </button>
        ))}
      </div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={4000} placeholder="What should change, and how you know" aria-label="Your suggestion" className={`${field} mt-3 resize-none`} />
      {error && <p role="alert" className="mt-2 text-xs text-red-200">{error}</p>}
      <button
        type="button"
        disabled={text.trim().length < 5}
        onClick={() => void kaluta.forums.article.suggest(entry.id, kind, text.trim()).then(onSent).catch((err) => setError(err instanceof ApiError ? err.message : 'Could not send it'))}
        className="mt-3 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-50"
      >
        Send to the editors
      </button>
    </Sheet>
  )
}
