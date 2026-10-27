import { useState } from 'react'
import { Link } from 'react-router'
import { BellOff, ChevronDown, EyeOff, Globe2, Handshake, Lock, LogOut, UserRound, Users } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { Sheet } from '@/components/community/CommunityDialogs'
import { useApi } from '@/hooks/useApi'
import AudiencePicker from './AudiencePicker'
import { audienceIsEmpty, EMPTY_AUDIENCE, type AudienceDraft } from '@/lib/audience'
import MemberAvatar from './MemberAvatar'
import {
  ApiError,
  kaluta,
  type AudienceChange,
  type AudienceControl,
  type AudienceWidening,
  type PostAudience,
} from '@/lib/api'
import { cn } from '@/lib/utils'

const CHOICES: Array<{ id: AudienceChange['visibility']; label: string; icon: typeof Globe2 }> = [
  { id: 'public', label: 'Anyone', icon: Globe2 },
  { id: 'followers', label: 'Followers', icon: Users },
  { id: 'connections', label: 'Connections', icon: Handshake },
  { id: 'circle', label: 'Circles & people', icon: Lock },
  { id: 'private', label: 'Only me', icon: UserRound },
]

const LABEL: Record<string, string> = Object.fromEntries(CHOICES.map((c) => [c.id, c.label]))

/** The 409 an audience change answers with when it would let new people in. */
function wideningOf(err: unknown): AudienceWidening | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null
  const detail = (err.detail as { detail?: unknown } | null)?.detail
  return detail && typeof detail === 'object' && 'becomes' in detail ? (detail as AudienceWidening) : null
}

/**
 * For the author: who a post goes to, who can see it now, and a way to change
 * it. Anything that lets new people in is shown and confirmed before it applies.
 */
export function AuthorAudienceSheet({
  postId,
  onClose,
  onChanged,
}: {
  postId: string
  onClose: () => void
  onChanged: (visibility: string) => void
}) {
  const { tok } = useAppTheme()
  const audience = useApi<PostAudience>(() => kaluta.posts.audience(postId), [postId])
  const circles = useApi(() => kaluta.circles.list({ sort: 'recent', archived: false }), [])
  const [changing, setChanging] = useState(false)
  const [showEligible, setShowEligible] = useState(false)
  const [visibility, setVisibility] = useState<AudienceChange['visibility']>('circle')
  const [draft, setDraft] = useState<AudienceDraft>(EMPTY_AUDIENCE)
  const [widening, setWidening] = useState<AudienceWidening | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const current = audience.data

  const startChange = () => {
    if (!current) return
    setVisibility((current.visibility as AudienceChange['visibility']) ?? 'circle')
    setDraft({
      circleIds: (current.circles ?? []).filter((c) => !c.deleted).map((c) => c.id),
      include: current.include ?? [],
      exclude: current.exclude ?? [],
      mode: current.audience_mode,
      notify: false,
    })
    setWidening(null)
    setChanging(true)
  }

  const save = async (confirm: boolean, override?: AudienceDraft) => {
    const d = override ?? draft
    if (visibility === 'circle' && audienceIsEmpty(d)) {
      setError('Pick at least one circle or person.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await kaluta.posts.changeAudience(postId, {
        visibility,
        circle_ids: visibility === 'circle' ? d.circleIds : [],
        include_ids: visibility === 'circle' ? d.include.map((p) => p.user_id) : [],
        exclude_ids: visibility === 'circle' ? d.exclude.map((p) => p.user_id) : [],
        audience_mode: d.mode,
        confirm_added: confirm,
      })
      setWidening(null)
      setChanging(false)
      setDone(
        result.removed > 0
          ? `Saved. ${result.removed} ${result.removed === 1 ? 'person' : 'people'} can no longer see it, and their notifications about it were withdrawn.`
          : 'Saved.',
      )
      audience.reload()
      onChanged(result.visibility)
    } catch (err) {
      const w = wideningOf(err)
      if (w) setWidening(w)
      else setError(err instanceof ApiError ? err.message : 'Could not change the audience')
    } finally {
      setBusy(false)
    }
  }

  /** Leave one person out straight from the list: it only narrows, so no confirmation. */
  const leaveOut = (person: { user_id: string; display_name: string | null; handle: string | null }) => {
    if (!current) return
    const next: AudienceDraft = {
      circleIds: (current.circles ?? []).filter((c) => !c.deleted).map((c) => c.id),
      include: (current.include ?? []).filter((p) => p.user_id !== person.user_id),
      exclude: [...(current.exclude ?? []), person],
      mode: current.audience_mode,
      notify: false,
    }
    setVisibility('circle')
    void save(false, next)
  }

  return (
    <Sheet title="Audience" onClose={onClose}>
      <div className="space-y-4 p-5">
        {audience.loading && !current && <p className={cn('text-sm', tok.mid)}>Opening…</p>}
        {audience.error && <p className="text-sm text-warning">{audience.error}</p>}
        {done && <p className="rounded-card-sm bg-success/10 px-3 py-2 text-xs text-success">{done}</p>}
        {current && !changing && (
          <>
            <p className={cn('text-lg font-bold', tok.text)}>{LABEL[current.visibility] ?? current.visibility}</p>
            {current.visibility === 'circle' && (
              <>
                {current.note && <p className={cn('text-xs leading-relaxed', tok.mid)}>{current.note}</p>}
                <ul className="flex flex-wrap gap-1.5">
                  {(current.circles ?? []).map((c) => (
                    <li
                      key={c.id}
                      className={cn('rounded-full px-2.5 py-1 text-xs font-semibold', c.deleted ? 'bg-text-hi/[0.06] text-text-low line-through' : 'bg-gold/15 text-gold-soft')}
                      title={c.deleted ? 'This circle was deleted: its members no longer get the post.' : undefined}
                    >
                      {c.name ?? 'Deleted circle'}
                    </li>
                  ))}
                  {(current.include ?? []).map((p) => (
                    <li key={p.user_id} className="rounded-full bg-gold/10 px-2.5 py-1 text-xs text-gold-soft">
                      + {p.display_name ?? p.handle}
                    </li>
                  ))}
                  {(current.exclude ?? []).map((p) => (
                    <li key={p.user_id} className="rounded-full bg-danger/10 px-2.5 py-1 text-xs text-danger">
                      − {p.display_name ?? p.handle}
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  aria-expanded={showEligible}
                  onClick={() => setShowEligible((v) => !v)}
                  className={cn('flex w-full items-center justify-between rounded-card-sm px-3 py-2 text-sm font-semibold', tok.subtleBg, tok.text)}
                >
                  Can see it now: {current.eligible_count ?? 0} {current.eligible_count === 1 ? 'person' : 'people'}
                  <ChevronDown size={14} className={cn('transition-transform', showEligible && 'rotate-180')} aria-hidden="true" />
                </button>
                {showEligible && (
                  <ul className="max-h-64 space-y-1 overflow-y-auto">
                    {(current.eligible ?? []).map((p) => (
                      <li key={p.user_id} className="flex items-center gap-2.5 py-1">
                        <MemberAvatar handle={p.handle} displayName={p.display_name} avatarUrl={p.avatar_url} size={30} />
                        <span className={cn('min-w-0 flex-1 truncate text-sm', tok.text)}>{p.display_name ?? p.handle ?? 'Member'}</span>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => leaveOut(p)}
                          className="rounded-full px-2 py-0.5 text-[0.7rem] font-semibold text-text-low hover:text-danger disabled:opacity-40"
                        >
                          Leave out
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {typeof current.opted_out_count === 'number' && (
                  <p className={cn('text-xs', tok.low)}>
                    {current.opted_out_count} people opted out of content from these circles. Who they are is not shown.
                  </p>
                )}
              </>
            )}
            {current.visibility !== 'community' && (
              <button
                type="button"
                onClick={startChange}
                className="rounded-full bg-gold-soft px-4 py-2 text-sm font-bold text-ink"
              >
                Change audience
              </button>
            )}
          </>
        )}

        {current && changing && !widening && (
          <>
            <div role="group" aria-label="Audience" className="flex flex-wrap gap-1.5">
              {CHOICES.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  aria-pressed={visibility === c.id}
                  onClick={() => setVisibility(c.id)}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold',
                    visibility === c.id ? 'bg-gold/15 text-gold-soft' : cn(tok.mid, tok.hoverBg),
                  )}
                >
                  <c.icon size={11} aria-hidden="true" />
                  {c.label}
                </button>
              ))}
            </div>
            {visibility === 'circle' && (
              <AudiencePicker circles={circles.data} value={draft} onChange={setDraft} scheduled={false} />
            )}
            <p className={cn('text-xs leading-relaxed', tok.low)}>
              People who lose access stop seeing the post and its notifications. What they already read cannot be taken back.
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void save(false)}
                className="rounded-full bg-gold-soft px-4 py-2 text-sm font-bold text-ink disabled:opacity-40"
              >
                Save
              </button>
              <button type="button" onClick={() => setChanging(false)} className={cn('rounded-full px-4 py-2 text-sm font-semibold', tok.mid)}>
                Cancel
              </button>
            </div>
          </>
        )}

        {widening && (
          <div className="space-y-3 rounded-card-sm border border-warning/40 bg-warning/10 p-4">
            <p className={cn('text-sm font-semibold', tok.text)}>More people will be able to see this post</p>
            <p className={cn('text-xs leading-relaxed', tok.mid)}>
              {widening.added_count === null
                ? `It becomes visible to ${LABEL[widening.becomes]?.toLowerCase() ?? widening.becomes}.`
                : `${widening.added_count} ${widening.added_count === 1 ? 'person' : 'people'} who cannot see it now will be able to.`}
            </p>
            {widening.added.length > 0 && (
              <p className={cn('text-xs', tok.low)}>
                {widening.added.map((p) => p.display_name ?? p.handle ?? 'Member').join(', ')}
                {widening.added_count !== null && widening.added_count > widening.added.length && '…'}
              </p>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => void save(true)}
                className="rounded-full bg-gold-soft px-4 py-2 text-sm font-bold text-ink disabled:opacity-40"
              >
                Yes, show it to them
              </button>
              <button type="button" onClick={() => setWidening(null)} className={cn('rounded-full px-4 py-2 text-sm font-semibold', tok.mid)}>
                Go back
              </button>
            </div>
          </div>
        )}
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      </div>
    </Sheet>
  )
}

/**
 * For someone who received a circle post: mute, hide or opt out of the private
 * audience it came through. They see a label, never the circle's name or members.
 */
export function ReaderAudienceSheet({
  postId,
  onClose,
  onHidden,
}: {
  postId: string
  onClose: () => void
  onHidden: () => void
}) {
  const { tok } = useAppTheme()
  const controls = useApi(() => kaluta.posts.audienceControls(postId), [postId])
  // What the reader changed here, laid over what the server sent.
  const [changed, setChanged] = useState<Record<string, Partial<AudienceControl>>>({})
  const items = (controls.data?.items ?? []).map((i) => ({ ...i, ...changed[i.token] }))
  const [confirmOptOut, setConfirmOptOut] = useState<string | null>(null)
  const [past, setPast] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apply = async (token: string, change: { muted?: boolean; hidden?: boolean; opt_out?: boolean; opt_out_past?: boolean }) => {
    setError(null)
    try {
      const state = await kaluta.posts.setAudiencePref({ post_id: postId, token, ...change })
      setChanged((all) => ({ ...all, [token]: state }))
      setConfirmOptOut(null)
      if (change.hidden || change.opt_out) onHidden()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save your choice')
    }
  }

  return (
    <Sheet title="Manage content from this audience" onClose={onClose}>
      <div className="space-y-4 p-5">
        {controls.loading && !controls.data && <p className={cn('text-sm', tok.mid)}>Opening…</p>}
        {controls.error && <p className="text-sm text-warning">{controls.error}</p>}
        {controls.data?.explanation && <p className={cn('text-xs leading-relaxed', tok.mid)}>{controls.data.explanation}</p>}
        {controls.data && items.length === 0 && (
          <p className={cn('text-sm', tok.mid)}>There is nothing to manage on this post.</p>
        )}
        {items.map((item) => (
          <div key={item.token} className={cn('space-y-2 rounded-card-sm p-3', tok.subtleBg)}>
            <p className={cn('text-sm font-semibold', tok.text)}>{item.label}</p>
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={item.muted} onChange={(e) => void apply(item.token, { muted: e.target.checked })} className="mt-0.5" />
              <span>
                <span className={cn('inline-flex items-center gap-1 text-xs font-semibold', tok.text)}><BellOff size={11} aria-hidden="true" /> Mute notifications</span>
                <span className={cn('block text-[0.68rem]', tok.low)}>You can still see these posts; you are just not told about them.</span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={item.hidden} onChange={(e) => void apply(item.token, { hidden: e.target.checked })} className="mt-0.5" />
              <span>
                <span className={cn('inline-flex items-center gap-1 text-xs font-semibold', tok.text)}><EyeOff size={11} aria-hidden="true" /> Hide from my feeds</span>
                <span className={cn('block text-[0.68rem]', tok.low)}>Kept out of your feeds; still reachable from a direct link or the author’s profile.</span>
              </span>
            </label>
            {item.opted_out ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className={cn('inline-flex items-center gap-1 text-xs font-semibold', tok.text)}>
                  <LogOut size={11} aria-hidden="true" /> You opted out{item.opt_out_past ? ', past posts included' : ''}.
                </span>
                <button type="button" onClick={() => void apply(item.token, { opt_out: false })} className="text-xs font-semibold text-gold-soft underline underline-offset-2">
                  Opt back in
                </button>
              </div>
            ) : confirmOptOut === item.token ? (
              <div className="space-y-2 rounded-card-sm border border-warning/40 p-3">
                <p className={cn('text-xs leading-relaxed', tok.mid)}>
                  You will stop receiving posts shared through this audience, even if another of the author’s lists also includes you.
                  The author is not told; at most they see an anonymous count.
                </p>
                <label className="flex items-center gap-2 text-xs">
                  <input type="checkbox" checked={past} onChange={(e) => setPast(e.target.checked)} />
                  <span className={tok.text}>Also stop seeing what was shared before</span>
                </label>
                <div className="flex gap-2">
                  <button type="button" onClick={() => void apply(item.token, { opt_out: true, opt_out_past: past })} className="rounded-full bg-gold-soft px-3 py-1.5 text-xs font-bold text-ink">
                    Opt out
                  </button>
                  <button type="button" onClick={() => setConfirmOptOut(null)} className={cn('rounded-full px-3 py-1.5 text-xs font-semibold', tok.mid)}>
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => {
                  setPast(false)
                  setConfirmOptOut(item.token)
                }}
                className={cn('inline-flex items-center gap-1 text-xs font-semibold', tok.mid, 'hover:text-danger')}
              >
                <LogOut size={11} aria-hidden="true" /> Opt out of this audience…
              </button>
            )}
          </div>
        ))}
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        <p className={cn('text-xs', tok.low)}>
          Every choice can be undone in{' '}
          <Link to="/settings/audiences" className="font-semibold text-gold-soft underline underline-offset-2">
            Private audiences
          </Link>
          .
        </p>
      </div>
    </Sheet>
  )
}

