import { useEffect, useState } from 'react'
import { Search, Trash2 } from 'lucide-react'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { ApiError, kaluta, type ForumDetail, type ForumModerationQueue, type ForumPolicy, type PersonBrief } from '@/lib/api'
import { Sheet } from './ForumDialogs'
import { POLICY, VISIBILITY, field } from './forumText'

/** A forum's settings, moderators and expert sessions: for whoever runs it. */
export function ForumSettingsDialog({ forum, onClose, onChanged }: { forum: ForumDetail; onClose: () => void; onChanged: () => void }) {
  const [name, setName] = useState(forum.name)
  const [description, setDescription] = useState(forum.description ?? '')
  const [category, setCategory] = useState(forum.category ?? '')
  const [tags, setTags] = useState((forum.tags ?? []).join(', '))
  const [lang, setLang] = useState(forum.lang ?? '')
  const [country, setCountry] = useState(forum.country ?? '')
  const [visibility, setVisibility] = useState(forum.visibility_setting)
  const [postPolicy, setPostPolicy] = useState<ForumPolicy>(forum.post_policy ?? 'everyone')
  const [replyPolicy, setReplyPolicy] = useState<ForumPolicy>(forum.reply_policy ?? 'everyone')
  const [rules, setRules] = useState(forum.rules ?? '')
  const [reviewDays, setReviewDays] = useState(String(forum.review_days))
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<Array<PersonBrief & { user_id: string }>>([])
  const [removing, setRemoving] = useState<string | null>(null)
  const inCommunity = Boolean(forum.community)

  useEffect(() => {
    if (query.trim().length < 2) {
      setFound([])
      return
    }
    const t = window.setTimeout(() => void kaluta.people.search(query.trim(), 6).then((r) => setFound(r.items)).catch(() => setFound([])), 200)
    return () => window.clearTimeout(t)
  }, [query])

  const run = async (action: () => Promise<unknown>, said: string) => {
    setBusy(true)
    setNote(null)
    try {
      await action()
      setNote(said)
      onChanged()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'That did not work')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet title="Forum settings" onClose={onClose} wide>
      {forum.can_configure && (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            void run(
              () =>
                kaluta.forums.update(forum.id, {
                  name: name.trim(),
                  description: description.trim(),
                  category: category.trim(),
                  tags: tags.split(',').map((t) => t.trim()).filter(Boolean),
                  ...(lang.trim() ? { lang: lang.trim() } : {}),
                  ...(country.trim() ? { country: country.trim().toUpperCase() } : {}),
                  visibility,
                  post_policy: postPolicy,
                  reply_policy: replyPolicy,
                  rules: rules.trim(),
                  review_days: Number(reviewDays) || 180,
                }),
              'Saved.',
            )
          }}
        >
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={140} aria-label="Name" placeholder="Name" className={field} />
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} maxLength={4000} aria-label="What it is about and who it is for" placeholder="What it is about, and who it is for" className={`${field} resize-none`} />
          <div className="grid gap-2 sm:grid-cols-2">
            <input value={category} onChange={(e) => setCategory(e.target.value)} maxLength={60} aria-label="Subject" placeholder="Subject (e.g. Farming)" className={field} />
            <input value={tags} onChange={(e) => setTags(e.target.value)} aria-label="Tags, separated by commas" placeholder="Tags, separated by commas" className={field} />
            <input value={lang} onChange={(e) => setLang(e.target.value)} maxLength={5} aria-label="Usual language (e.g. en, fr)" placeholder="Usual language (en, fr…)" className={field} />
            <input value={country} onChange={(e) => setCountry(e.target.value)} maxLength={2} aria-label="Country code (e.g. CI)" placeholder="Country code (CI, KE…)" className={field} />
          </div>
          <label className="block text-xs font-semibold text-text-hi">
            Who can read it
            <select value={visibility} onChange={(e) => setVisibility(e.target.value as ForumDetail['visibility_setting'])} className={`${field} mt-1`}>
              <option value="inherit">As its community allows{inCommunity ? '' : ' (public)'}</option>
              <option value="public">{VISIBILITY.public.label}</option>
              {inCommunity && <option value="members">{VISIBILITY.members.label}</option>}
              <option value="private">{VISIBILITY.private.label}</option>
            </select>
          </label>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block text-xs font-semibold text-text-hi">
              Who can start threads
              <select value={postPolicy} onChange={(e) => setPostPolicy(e.target.value as ForumPolicy)} className={`${field} mt-1`}>
                {Object.entries(POLICY).map(([id, label]) => (inCommunity || id !== 'members') && <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
            <label className="block text-xs font-semibold text-text-hi">
              Who can reply
              <select value={replyPolicy} onChange={(e) => setReplyPolicy(e.target.value as ForumPolicy)} className={`${field} mt-1`}>
                {Object.entries(POLICY).map(([id, label]) => (inCommunity || id !== 'members') && <option key={id} value={id}>{label}</option>)}
              </select>
            </label>
          </div>
          <textarea value={rules} onChange={(e) => setRules(e.target.value)} rows={4} maxLength={8000} aria-label="Forum rules" placeholder="Forum rules - shown to everybody who writes a thread" className={`${field} resize-none`} />
          <label className="block text-xs font-semibold text-text-hi">
            Flag knowledge articles not reviewed for (days)
            <input type="number" min={7} max={3650} value={reviewDays} onChange={(e) => setReviewDays(e.target.value)} className={`${field} mt-1 w-32`} />
          </label>
          <p className="text-xs text-text-low">
            Following or reading a forum never makes anybody a member of its community, and a public forum of a private
            community opens only this forum. A secret or paid community’s forum cannot be public.
          </p>
          <button type="submit" disabled={busy || !name.trim()} className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-50">
            Save
          </button>
        </form>
      )}

      {forum.can_configure && (
        <section className="mt-6 border-t border-[var(--cloud-border)] pt-4">
          <h3 className="text-sm font-bold text-text-hi">Moderators</h3>
          <p className="text-xs text-text-low">They pin, lock, hide, move and merge threads and review reports. Knowledge editors write and review articles.</p>
          <ul className="mt-2 space-y-1">
            {forum.moderators.map((m) => (
              <li key={m.user_id} className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate text-text-hi">{m.display_name ?? m.user_id}</span>
                <label className="flex items-center gap-1 text-xs text-text-mid">
                  <input type="checkbox" className="accent-[#E5B556]" checked={m.can_moderate} onChange={(e) => void run(() => kaluta.forums.moderators.add(forum.id, { user_id: m.user_id, can_moderate: e.target.checked, can_edit_knowledge: m.can_edit_knowledge }), 'Saved.')} />
                  Moderates
                </label>
                <label className="flex items-center gap-1 text-xs text-text-mid">
                  <input type="checkbox" className="accent-[#E5B556]" checked={m.can_edit_knowledge} onChange={(e) => void run(() => kaluta.forums.moderators.add(forum.id, { user_id: m.user_id, can_moderate: m.can_moderate, can_edit_knowledge: e.target.checked }), 'Saved.')} />
                  Knowledge
                </label>
                <button type="button" onClick={() => setRemoving(m.user_id)} aria-label={`Remove ${m.display_name ?? 'moderator'}`} className="rounded-full p-1.5 text-text-low hover:text-red-300">
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </li>
            ))}
            {forum.moderators.length === 0 && <li className="text-xs text-text-low">{inCommunity ? 'The community’s owner and moderators moderate it.' : 'You moderate it.'}</li>}
          </ul>
          <label className="relative mt-2 flex items-center">
            <Search size={14} className="pointer-events-none absolute start-3 text-text-low" aria-hidden="true" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={inCommunity ? 'Appoint a community member…' : 'Appoint a member…'} aria-label="Search for a member to appoint" className={`${field} ps-9`} />
          </label>
          <ul className="mt-1">
            {found.map((p) => (
              <li key={p.user_id}>
                <button type="button" onClick={() => void run(() => kaluta.forums.moderators.add(forum.id, { user_id: p.user_id }).then(() => setQuery('')), `${p.display_name} is now a moderator.`)} className="w-full rounded-xl px-2 py-1.5 text-start text-sm text-text-hi hover:bg-text-hi/[0.05]">
                  {p.display_name} <span className="text-text-low">@{p.handle}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {forum.can_moderate && <SessionForm forumId={forum.id} onChanged={onChanged} />}
      {note && <p role="status" className="mt-3 text-xs text-text-mid">{note}</p>}
      <ConfirmDialog
        open={Boolean(removing)}
        onOpenChange={(open) => !open && setRemoving(null)}
        title="Remove this moderator?"
        description="They keep reading and writing in the forum as any member does, without the moderator's tools."
        confirmLabel="Remove"
        onConfirm={() => {
          const id = removing
          setRemoving(null)
          if (id) void run(() => kaluta.forums.moderators.remove(forum.id, id), 'Removed.')
        }}
      />
    </Sheet>
  )
}

function SessionForm({ forumId, onChanged }: { forumId: string; onChanged: () => void }) {
  const [title, setTitle] = useState('')
  const [intro, setIntro] = useState('')
  const [starts, setStarts] = useState('')
  const [ends, setEnds] = useState('')
  const [expert, setExpert] = useState<(PersonBrief & { user_id: string }) | null>(null)
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<Array<PersonBrief & { user_id: string }>>([])
  const [note, setNote] = useState<string | null>(null)
  useEffect(() => {
    const text = query.trim()
    const t = window.setTimeout(() => {
      if (text.length < 2) setFound([])
      else void kaluta.people.search(text, 6).then((r) => setFound(r.items)).catch(() => setFound([]))
    }, 200)
    return () => window.clearTimeout(t)
  }, [query])
  return (
    <section className="mt-6 border-t border-[var(--cloud-border)] pt-4">
      <h3 className="text-sm font-bold text-text-hi">Organise an expert question session</h3>
      <p className="text-xs text-text-low">Members ask questions during the session; the expert answers in the forum, and their answers are marked.</p>
      <form
        className="mt-2 space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (!expert) return
          kaluta.forums
            .createSession(forumId, { title: title.trim(), expert_id: expert.user_id, expert_intro: intro.trim(), starts_at: new Date(starts).toISOString(), ends_at: new Date(ends).toISOString() })
            .then(() => {
              setNote('The session is planned. The expert has been told.')
              setTitle('')
              onChanged()
            })
            .catch((err) => setNote(err instanceof ApiError ? err.message : 'Could not plan the session'))
        }}
      >
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="Title (e.g. Ask an agronomist)" aria-label="Session title" className={field} />
        {expert ? (
          <p className="flex items-center gap-2 text-sm text-text-hi">
            Expert: {expert.display_name}
            <button type="button" onClick={() => setExpert(null)} className="text-xs text-text-low underline">change</button>
          </p>
        ) : (
          <>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Invite an expert…" aria-label="Search for the expert" className={field} />
            <ul>
              {found.map((p) => (
                <li key={p.user_id}>
                  <button type="button" onClick={() => setExpert(p)} className="w-full rounded-xl px-2 py-1 text-start text-sm text-text-hi hover:bg-text-hi/[0.05]">{p.display_name}</button>
                </li>
              ))}
            </ul>
          </>
        )}
        <textarea value={intro} onChange={(e) => setIntro(e.target.value)} rows={2} maxLength={1000} placeholder="How you introduce the expert (shown as your words, not as a verified credential)" aria-label="Introduction of the expert" className={`${field} resize-none`} />
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-xs text-text-mid">Starts<input type="datetime-local" value={starts} onChange={(e) => setStarts(e.target.value)} className={`${field} mt-1`} /></label>
          <label className="text-xs text-text-mid">Ends<input type="datetime-local" value={ends} onChange={(e) => setEnds(e.target.value)} className={`${field} mt-1`} /></label>
        </div>
        <button type="submit" disabled={!title.trim() || !expert || !starts || !ends} className="rounded-full border border-gold/50 px-4 py-1.5 text-xs font-semibold text-gold-soft disabled:opacity-50">
          Plan the session
        </button>
        {note && <p role="status" className="text-xs text-text-mid">{note}</p>}
      </form>
    </section>
  )
}

/** Reports, review requests, suspected spam and the audit trail of one forum. */
export function ModerationDialog({ forumId, onClose, onOpenThread }: { forumId: string; onClose: () => void; onOpenThread: (id: string) => void }) {
  const [tab, setTab] = useState<'open' | 'closed' | 'audit'>('open')
  const [queue, setQueue] = useState<ForumModerationQueue | null>(null)
  const [audit, setAudit] = useState<Awaited<ReturnType<typeof kaluta.forums.audit>>['items'] | null>(null)
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [error, setError] = useState<string | null>(null)
  const load = () => {
    const done = tab === 'audit' ? kaluta.forums.audit(forumId).then((r) => setAudit(r.items)) : kaluta.forums.moderation(forumId, tab).then(setQueue)
    done.then(() => setError(null)).catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load'))
  }
  useEffect(load, [forumId, tab])
  return (
    <Sheet title="Moderation" onClose={onClose} wide>
      <div role="tablist" aria-label="Moderation" className="mb-3 flex gap-1.5">
        {(
          [
            ['open', 'To review'],
            ['closed', 'Done'],
            ['audit', 'Activity log'],
          ] as const
        ).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)} className={`rounded-full border px-3 py-1 text-xs font-semibold ${tab === id ? 'border-gold/60 bg-gold/15 text-gold-soft' : 'border-white/10 text-text-mid'}`}>
            {label}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="text-sm text-red-200">{error}</p>}
      {tab === 'audit' ? (
        <ul className="space-y-2 text-xs">
          {(audit ?? []).map((a) => (
            <li key={a.id} className="rounded-xl border border-white/10 px-3 py-2">
              <span className="font-semibold text-text-hi">{a.actor ?? 'Someone'}</span> <span className="text-text-mid">{a.action.replace(/_/g, ' ')}</span>
              {a.reason && <span className="block text-text-low">{a.reason}</span>}
              <span className="block text-text-low">{new Date(a.created_at).toLocaleString()}</span>
            </li>
          ))}
          {audit && audit.length === 0 && <li className="text-text-low">Nothing recorded yet.</li>}
        </ul>
      ) : (
        <>
          <ul className="space-y-2">
            {(queue?.reports ?? []).map((r) => (
              <li key={r.id} className="rounded-xl border border-white/10 p-3 text-sm">
                <p className="text-xs font-semibold text-gold-soft">{r.target_kind === 'review' ? 'Review requested by the author' : `${r.reason.replace('_', ' ')} · ${r.target_kind}`}</p>
                {r.target.title && <p className="font-semibold text-text-hi">{r.target.title}</p>}
                {r.target.excerpt && <p className="line-clamp-3 text-text-mid">{r.target.excerpt}</p>}
                {r.target.moderation_reason && <p className="text-xs text-text-low">Hidden because: {r.target.moderation_reason}</p>}
                {r.note && <p className="text-xs text-text-low">Note: {r.note}</p>}
                <p className="text-xs text-text-low">{r.reporter ?? 'A member'} · {new Date(r.created_at).toLocaleString()} · now {r.target.status}</p>
                {r.target.thread_id && r.target.status === 'open' && (
                  <button type="button" onClick={() => { onClose(); onOpenThread(r.target.thread_id!) }} className="mt-1 text-xs font-semibold text-gold-soft underline">
                    Open the thread to act on it
                  </button>
                )}
                {r.status === 'open' ? (
                  <div className="mt-2 flex gap-2">
                    <input value={notes[r.id] ?? ''} onChange={(e) => setNotes((n) => ({ ...n, [r.id]: e.target.value }))} placeholder="What you decided" aria-label="What you decided" className={field} />
                    <button
                      type="button"
                      disabled={!(notes[r.id] ?? '').trim()}
                      onClick={() => void kaluta.forums.resolveReport(r.id, notes[r.id]).then(load).catch((err) => setError(err instanceof ApiError ? err.message : 'Could not close it'))}
                      className="shrink-0 rounded-full border border-gold/50 px-3 text-xs font-semibold text-gold-soft disabled:opacity-50"
                    >
                      Close
                    </button>
                  </div>
                ) : (
                  <p className="mt-1 text-xs text-text-mid">Decision: {r.resolution}</p>
                )}
              </li>
            ))}
            {queue && queue.reports.length === 0 && <li className="text-sm text-text-low">No {tab === 'open' ? 'open' : 'closed'} report.</li>}
          </ul>
          {tab === 'open' && (queue?.suspected_spam.length ?? 0) > 0 && (
            <section className="mt-4">
              <h3 className="text-sm font-bold text-text-hi">Possible spam this week</h3>
              <p className="text-xs text-text-low">Signals for a person to look at, not decisions.</p>
              <ul className="mt-2 space-y-1.5">
                {queue!.suspected_spam.map((s) => (
                  <li key={`${s.id}-${s.signal}`} className="rounded-xl border border-white/10 px-3 py-2 text-xs">
                    <span className="font-semibold text-amber-200">{s.signal}</span>
                    <span className="block text-text-mid">{s.excerpt}</span>
                    {s.thread_id && (
                      <button type="button" onClick={() => { onClose(); onOpenThread(s.thread_id!) }} className="mt-0.5 font-semibold text-gold-soft underline">Open</button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Sheet>
  )
}
