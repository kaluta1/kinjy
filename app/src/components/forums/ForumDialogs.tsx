import { useEffect, useState, type ReactNode } from 'react'
import { BadgeCheck, Flag, X } from 'lucide-react'
import { ApiError, kaluta, type ForumPrefs } from '@/lib/api'
import { FORUM_REPORT_REASONS, field } from './forumText'

/** A sheet on a phone, a dialog on a desktop. */
export function Sheet({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-[85] flex items-end justify-center bg-black/55 sm:items-center sm:p-6" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className={`cloud-card flex max-h-[92svh] w-full flex-col overflow-hidden rounded-t-3xl sm:rounded-3xl ${wide ? 'max-w-2xl' : 'max-w-md'}`}
      >
        <header className="flex items-center gap-2 border-b border-[var(--cloud-border)] px-5 py-3">
          <h2 className="flex-1 text-base font-bold text-text-hi">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-full p-1.5 text-text-mid hover:bg-text-hi/[0.07]">
            <X size={16} aria-hidden="true" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
      </div>
    </div>
  )
}

/** Report a thread, a reply or a knowledge article to the forum's moderators. */
export function ReportDialog({ kind, id, onClose }: { kind: 'thread' | 'reply' | 'knowledge'; id: string; onClose: () => void }) {
  const [reason, setReason] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle')
  const [error, setError] = useState<string | null>(null)
  const label = kind === 'thread' ? 'this thread' : kind === 'reply' ? 'this reply' : 'this article'
  return (
    <Sheet title={`Report ${label}`} onClose={onClose}>
      {state === 'done' ? (
        <>
          <p className="text-sm text-text-mid">Thank you. The forum’s moderators will look at it. Who reported it is not shown to the author.</p>
          <button type="button" onClick={onClose} className="mt-4 w-full rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink">
            Close
          </button>
        </>
      ) : (
        <>
          <div role="radiogroup" aria-label="Why" className="space-y-1">
            {FORUM_REPORT_REASONS.map((r) => (
              <button
                key={r.id}
                type="button"
                role="radio"
                aria-checked={reason === r.id}
                onClick={() => setReason(r.id)}
                className={`flex w-full rounded-xl border px-3 py-2 text-start text-sm ${reason === r.id ? 'border-gold/60 bg-gold/10 text-text-hi' : 'border-white/10 text-text-mid'}`}
              >
                {r.label}
              </button>
            ))}
          </div>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={2000} placeholder="Anything to add (optional)" aria-label="Anything to add" className={`${field} mt-3 resize-none`} />
          {error && <p role="alert" className="mt-2 text-xs text-red-200">{error}</p>}
          <button
            type="button"
            disabled={!reason || state === 'busy'}
            onClick={() => {
              setState('busy')
              setError(null)
              kaluta.forums
                .report(kind, id, reason!, note.trim() || undefined)
                .then(() => setState('done'))
                .catch((err) => {
                  setError(err instanceof ApiError ? err.message : 'Could not send the report')
                  setState('idle')
                })
            }}
            className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-full bg-red-500/90 px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
          >
            <Flag size={14} aria-hidden="true" />
            Send the report
          </button>
        </>
      )}
    </Sheet>
  )
}

/** A contributor's forum profile: what they say about themselves, and what they did. */
export function ContributorDialog({ userId, onClose }: { userId: string; onClose: () => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof kaluta.forums.profile>> | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    kaluta.forums
      .profile(userId)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the profile'))
  }, [userId])
  return (
    <Sheet title={data?.display_name ?? 'Contributor'} onClose={onClose}>
      {error && <p className="text-sm text-text-mid">{error}</p>}
      {!data && !error && <p className="text-sm text-text-low">Loading…</p>}
      {data && (
        <div className="space-y-4 text-sm">
          {data.account_verified && (
            <p className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-300">
              <BadgeCheck size={13} aria-hidden="true" />
              Kinjy account verified
            </p>
          )}
          <div>
            <p className="text-xs font-semibold text-text-hi">Background</p>
            {data.background ? (
              <>
                <p className="mt-1 whitespace-pre-wrap text-text-mid">{data.background}</p>
                <p className="mt-1 text-xs text-text-low">{data.background_note}</p>
              </>
            ) : (
              <p className="mt-1 text-text-low">Not described.</p>
            )}
          </div>
          {data.stats ? (
            <div>
              <p className="text-xs font-semibold text-text-hi">In forums you can read</p>
              <ul className="mt-1 grid grid-cols-2 gap-2">
                <li className="rounded-xl bg-text-hi/[0.05] p-2"><span className="block text-lg font-bold text-text-hi">{data.stats.answers}</span>replies</li>
                <li className="rounded-xl bg-text-hi/[0.05] p-2"><span className="block text-lg font-bold text-text-hi">{data.stats.accepted}</span>accepted answers</li>
                <li className="rounded-xl bg-text-hi/[0.05] p-2"><span className="block text-lg font-bold text-text-hi">{data.stats.helpful_votes}</span>helpful votes</li>
                <li className="rounded-xl bg-text-hi/[0.05] p-2"><span className="block text-lg font-bold text-text-hi">{data.stats.knowledge}</span>knowledge articles</li>
              </ul>
              {data.stats.badges.length > 0 && (
                <p className="mt-2 flex flex-wrap gap-1.5">
                  {data.stats.badges.map((b) => (
                    <span key={b.key} className="rounded-full border border-gold/40 bg-gold/10 px-2.5 py-0.5 text-xs font-semibold text-gold-soft">
                      {b.label}
                    </span>
                  ))}
                </p>
              )}
              <p className="mt-2 text-xs text-text-low">{data.recognition_note}</p>
            </div>
          ) : (
            <p className="text-text-low">This member keeps their forum activity private.</p>
          )}
        </div>
      )}
    </Sheet>
  )
}

/** A member's forum notifications, their background, and the topics they follow. */
export function ForumPrefsDialog({ onClose }: { onClose: () => void }) {
  const [prefs, setPrefs] = useState<ForumPrefs | null>(null)
  const [background, setBackground] = useState('')
  const [note, setNote] = useState<string | null>(null)
  useEffect(() => {
    kaluta.forums
      .prefs()
      .then((p) => {
        setPrefs(p)
        setBackground(p.background ?? '')
      })
      .catch(() => setNote('Could not load your settings'))
  }, [])
  const save = (input: Partial<ForumPrefs>) =>
    kaluta.forums
      .setPrefs(input)
      .then((p) => {
        setPrefs((current) => ({ ...p, topics_followed: current?.topics_followed }))
        setNote('Saved.')
      })
      .catch((err) => setNote(err instanceof ApiError ? err.message : 'Could not save'))
  const toggles: Array<[keyof ForumPrefs, string]> = [
    ['replies', 'Replies to my threads and answers'],
    ['mentions', 'When somebody @mentions me'],
    ['followed_threads', 'New replies in threads I follow'],
    ['topics', 'New threads on topics I follow'],
    ['digests', 'A weekly digest of the forums I follow'],
    ['show_activity', 'Show my forum activity on my contributor profile'],
  ]
  return (
    <Sheet title="Forum settings" onClose={onClose}>
      {!prefs ? (
        <p className="text-sm text-text-low">{note ?? 'Loading…'}</p>
      ) : (
        <div className="space-y-4 text-sm">
          <fieldset className="space-y-2">
            <legend className="mb-1 text-xs font-semibold text-text-hi">Notify me about</legend>
            {toggles.map(([key, label]) => (
              <label key={key} className="flex items-center gap-2 text-text-mid">
                <input type="checkbox" className="accent-[#E5B556]" checked={Boolean(prefs[key])} onChange={(e) => void save({ [key]: e.target.checked })} />
                {label}
              </label>
            ))}
          </fieldset>
          <label className="block text-xs font-semibold text-text-hi">
            My background (optional)
            <textarea
              value={background}
              onChange={(e) => setBackground(e.target.value)}
              onBlur={() => background !== (prefs.background ?? '') && void save({ background })}
              rows={3}
              maxLength={1000}
              placeholder="e.g. Nurse for 10 years; grow cassava on 2 hectares"
              className={`${field} mt-1 resize-none font-normal`}
            />
            <span className="mt-1 block font-normal text-text-low">Shown next to your posts as something you say about yourself. Kinjy does not check it.</span>
          </label>
          {(prefs.topics_followed?.length ?? 0) > 0 && (
            <div>
              <p className="text-xs font-semibold text-text-hi">Topics I follow</p>
              <ul className="mt-1 flex flex-wrap gap-1.5">
                {prefs.topics_followed!.map((t) => (
                  <li key={t}>
                    <button
                      type="button"
                      onClick={() =>
                        void kaluta.forums.unfollowTopic(t).then(() => setPrefs((p) => p && { ...p, topics_followed: p.topics_followed?.filter((x) => x !== t) }))
                      }
                      className="rounded-full bg-text-hi/[0.07] px-2.5 py-1 text-xs text-text-mid hover:text-red-300"
                      aria-label={`Stop following ${t}`}
                    >
                      #{t} ×
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {note && <p role="status" className="text-xs text-text-low">{note}</p>}
        </div>
      )}
    </Sheet>
  )
}
