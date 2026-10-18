import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { BadgeCheck, Check, Lock, Pencil, Plus, Search, Sparkles, Trash2, UserMinus, UserPlus, Users, X } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useApi } from '@/hooks/useApi'
import {
  ApiError,
  kaluta,
  type Circle,
  type CircleDetail,
  type CircleRule,
  type PersonBrief,
} from '@/lib/api'
import { announce } from '@/lib/live'
import { cn } from '@/lib/utils'

/** Blueprint module D — the named circle kinds, a free-form one, and Smart. */
const KINDS = [
  { id: 'family', label: 'Family' },
  { id: 'close_friends', label: 'Close friends' },
  { id: 'business', label: 'Business' },
  { id: 'customers', label: 'Customers' },
  { id: 'custom', label: 'Custom' },
  { id: 'smart', label: 'Smart' },
] as const

const KIND_LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.id, k.label]))

const SOURCES: Array<{ id: CircleRule['source']; label: string }> = [
  { id: 'connections', label: 'My connections' },
  { id: 'followers', label: 'My followers' },
  { id: 'following', label: 'People I follow' },
  { id: 'mutuals', label: 'Mutual follows' },
]

/** "Your followers who live in Kigoma, KE" — the rule, read back in words. */
function describeRule(rule: CircleRule): string {
  const who = {
    connections: 'Your connections',
    followers: 'Your followers',
    following: 'People you follow',
    mutuals: 'People you follow who follow you back',
  }[rule.source]
  const place = [rule.city, rule.country].filter(Boolean).join(', ')
  return place ? `${who} who live in ${place}` : who
}

const field =
  'w-full rounded-full border border-transparent bg-text-hi/[0.07] px-5 py-3 text-[0.95rem] text-text-hi placeholder:text-text-low focus:border-gold/50 focus:bg-transparent focus:outline-none'
const chip = (on: boolean) =>
  cn(
    'rounded-full px-4 py-2 text-sm font-semibold transition-colors',
    on ? 'bg-gold/20 text-gold-soft' : 'text-text-mid hover:bg-text-hi/[0.07] hover:text-text-hi',
  )
const label = 'mono-data text-[0.7rem] font-bold uppercase tracking-[0.15em] text-gold-soft'

/** One tile colour per circle kind, so the list reads at a glance. */
const KIND_TILE: Record<string, string> = {
  family: 'bg-coral/15 text-coral',
  close_friends: 'bg-gold/20 text-gold-soft',
  business: 'bg-sky/15 text-sky',
  customers: 'bg-success/15 text-success',
  custom: 'bg-text-hi/[0.08] text-text-mid',
  smart: 'bg-indigo/15 text-indigo',
}

/** Source, country and city of a smart circle. */
function RuleFields({ rule, onChange }: { rule: CircleRule; onChange: (rule: CircleRule) => void }) {
  return (
    <div className="mt-4 space-y-3 rounded-2xl bg-text-hi/[0.05] p-4">
      <p className="text-sm text-text-low">Members are worked out each time someone reads — nobody to add by hand.</p>
      <div className="flex flex-wrap gap-2">
        {SOURCES.map((s) => (
          <button key={s.id} type="button" aria-pressed={rule.source === s.id} onClick={() => onChange({ ...rule, source: s.id })} className={chip(rule.source === s.id)}>
            {s.label}
          </button>
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-[170px_1fr]">
        <input
          value={rule.country ?? ''}
          onChange={(e) => onChange({ ...rule, country: e.target.value.toUpperCase().slice(0, 2) || null })}
          placeholder="Country (KE)"
          aria-label="Country code, optional"
          className={field}
        />
        <input
          value={rule.city ?? ''}
          onChange={(e) => onChange({ ...rule, city: e.target.value || null })}
          placeholder="City — optional"
          aria-label="City, optional"
          className={field}
        />
      </div>
    </div>
  )
}

/** The rule as the API wants it: empty place fields left out rather than sent blank. */
function cleanRule(rule: CircleRule): CircleRule {
  return {
    source: rule.source,
    ...(rule.country?.trim().length === 2 ? { country: rule.country.trim().toUpperCase() } : {}),
    ...(rule.city?.trim() ? { city: rule.city.trim() } : {}),
  }
}

// The primary buttons here are solid gold-soft rather than the gold gradient used
// elsewhere: in light mode the gradient's lighter end leaves the label at 3.5:1,
// and gold-soft alone holds 4.7:1 in light and 12:1 in the dark modes.
function CreateCircle({ onCreated }: { onCreated: (circle: CircleDetail) => void }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<string>('family')
  const [rule, setRule] = useState<CircleRule>({ source: 'connections' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const countryInvalid = kind === 'smart' && Boolean(rule.country) && rule.country!.length !== 2

  const create = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!name.trim() || countryInvalid) return
    setBusy(true)
    setError(null)
    try {
      const created = await kaluta.circles.create({
        name: name.trim(),
        kind,
        ...(kind === 'smart' ? { rule: cleanRule(rule) } : {}),
      })
      setName('')
      onCreated(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the circle')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={create} className="cloud-card p-5 md:p-6">
      <h2 className={cn(label, 'mb-3')}>New circle</h2>
      <div className="flex flex-wrap gap-2">
        {KINDS.map((k) => (
          <button key={k.id} type="button" aria-pressed={kind === k.id} onClick={() => setKind(k.id)} className={chip(kind === k.id)}>
            {k.id === 'smart' && <Sparkles size={11} className="me-1 inline" aria-hidden="true" />}
            {k.label}
          </button>
        ))}
      </div>
      {kind === 'smart' && <RuleFields rule={rule} onChange={setRule} />}
      <div className="mt-4 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={kind === 'smart' ? 'Name it — Friends in Nairobi' : 'Name this circle — Kinjy family'}
          aria-label="Circle name"
          maxLength={80}
          className={field}
        />
        <button
          type="submit"
          disabled={!name.trim() || busy || countryInvalid}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-gold-soft px-5 py-3 text-sm font-bold text-ink disabled:opacity-40"
        >
          <Plus size={15} aria-hidden="true" />
          Create
        </button>
      </div>
      {countryInvalid && <p className="mt-2 text-xs text-warning">A country is two letters, like KE or FR.</p>}
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  )
}

/** Find people and add them — static circles only. */
function AddPeople({ circle, onAdded }: { circle: CircleDetail; onAdded: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Array<PersonBrief & { user_id: string }>>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const latest = useRef(0)
  const q = query.trim()

  // Debounced, and answers for an older query are dropped — the same pattern
  // as Explore, which this search shares its endpoint with.
  useEffect(() => {
    if (q.length < 2) return
    const ticket = ++latest.current
    const timer = window.setTimeout(() => {
      kaluta.people
        .search(q, 8)
        .then((r) => {
          if (ticket === latest.current) setResults(r.items)
        })
        .catch(() => {
          if (ticket === latest.current) setResults([])
        })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [q])

  const inCircle = new Set(circle.members.map((m) => m.user_id))

  const add = async (person: PersonBrief & { user_id: string }) => {
    setBusy(person.user_id)
    setNote(null)
    try {
      await kaluta.circles.addMember(circle.id, person.user_id)
      onAdded()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not add them')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mt-5">
      <label className="flex items-center gap-3 rounded-full bg-text-hi/[0.07] px-5 py-3 focus-within:ring-1 focus-within:ring-gold/50">
        <Search size={16} className="shrink-0 text-text-low" aria-hidden="true" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Add people — type a name"
          aria-label="Search people to add"
          className="w-full bg-transparent text-[0.95rem] text-text-hi placeholder:text-text-low focus:outline-none"
        />
        {query && (
          <button type="button" onClick={() => setQuery('')} aria-label="Clear search" className="text-text-low hover:text-text-hi">
            <X size={14} />
          </button>
        )}
      </label>
      {q.length >= 2 && (
        <ul className="mt-2 space-y-1">
          {results.length === 0 && <li className="caption px-2">No member whose name starts with “{q}”.</li>}
          {results.map((person) => (
            <li key={person.user_id} className="flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-text-hi/[0.05]">
              <MemberAvatar handle={person.handle} displayName={person.display_name} avatarUrl={person.avatar_url} size={40} />
              <span className="min-w-0 flex-1" title={`@${person.handle}`}>
                <span className="block truncate text-sm text-text-hi">{person.display_name}</span>
              </span>
              {inCircle.has(person.user_id) ? (
                <span className="inline-flex items-center gap-1 text-xs text-text-mid">
                  <Check size={12} aria-hidden="true" /> In circle
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => void add(person)}
                  disabled={busy === person.user_id}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-gold/50 px-4 py-1.5 text-sm font-semibold text-text-hi hover:bg-gold/10 disabled:opacity-40"
                >
                  <UserPlus size={12} aria-hidden="true" /> Add
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {note && <p className="mt-2 text-xs text-warning">{note}</p>}
    </div>
  )
}

/** Rename, change kind or rule — one small form rather than three buttons. */
function EditCircle({
  circle,
  onSaved,
  onCancel,
}: {
  circle: CircleDetail
  onSaved: (circle: CircleDetail) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(circle.name)
  const [kind, setKind] = useState(circle.kind)
  const [rule, setRule] = useState<CircleRule>(circle.rule ?? { source: 'connections' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const losesMembers = circle.kind !== 'smart' && kind === 'smart' && circle.members.length > 0
  const countryInvalid = kind === 'smart' && Boolean(rule.country) && rule.country!.length !== 2

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!name.trim() || countryInvalid) return
    setBusy(true)
    setError(null)
    try {
      onSaved(
        await kaluta.circles.update(circle.id, {
          name: name.trim(),
          kind,
          ...(kind === 'smart' ? { rule: cleanRule(rule) } : {}),
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the circle')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={save} className="space-y-3">
      <input value={name} onChange={(e) => setName(e.target.value)} aria-label="Circle name" maxLength={80} className={field} />
      <div className="flex flex-wrap gap-2">
        {KINDS.map((k) => (
          <button key={k.id} type="button" aria-pressed={kind === k.id} onClick={() => setKind(k.id)} className={chip(kind === k.id)}>
            {k.label}
          </button>
        ))}
      </div>
      {kind === 'smart' && <RuleFields rule={rule} onChange={setRule} />}
      {losesMembers && (
        <p className="text-xs text-warning">
          A smart circle is filled by its rule: the {circle.members.length} people you added will be removed.
        </p>
      )}
      {circle.kind === 'smart' && kind !== 'smart' && (
        <p className="text-xs text-warning">It will start empty — add people by hand afterwards.</p>
      )}
      {countryInvalid && <p className="text-xs text-warning">A country is two letters, like KE or FR.</p>}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || busy || countryInvalid}
          className="rounded-full bg-gold-soft px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40"
        >
          Save
        </button>
        <button type="button" onClick={onCancel} className="rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-mid hover:text-text-hi">
          Cancel
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  )
}

function CirclePanel({ id, onChanged, onDeleted }: { id: string; onChanged: () => void; onDeleted: () => void }) {
  const detail = useApi<CircleDetail>(() => kaluta.circles.get(id), [id])
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const changed = () => {
    detail.reload()
    onChanged()
  }

  const removeMember = async (memberId: string) => {
    setError(null)
    try {
      await kaluta.circles.removeMember(id, memberId)
      changed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not remove them')
    }
  }

  const deleteCircle = async () => {
    setError(null)
    try {
      await kaluta.circles.remove(id)
      onDeleted()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete the circle')
    }
  }

  if (detail.loading && !detail.data) return <p className="text-sm text-text-mid">Opening the circle…</p>
  if (detail.error || !detail.data) {
    return <p className="text-sm text-warning">{detail.error ?? 'This circle could not be opened.'}</p>
  }
  const circle = detail.data
  const smart = circle.kind === 'smart'

  return (
    <div>
      {editing ? (
        <EditCircle
          circle={circle}
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            changed()
          }}
        />
      ) : (
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[clamp(26px,3vw,34px)] font-bold leading-tight tracking-[-0.035em] text-text-hi">{circle.name}</h2>
            <p className="mt-1.5 text-sm text-text-low">
              {KIND_LABEL[circle.kind] ?? circle.kind} · reaches {circle.members_count}{' '}
              {circle.members_count === 1 ? 'person' : 'people'}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi"
          >
            <Pencil size={13} aria-hidden="true" /> Edit
          </button>
          {confirmDelete ? (
            <span className="inline-flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => void deleteCircle()}
                className="rounded-full bg-red-600 px-4 py-2 text-sm font-bold text-white hover:bg-red-700"
              >
                Delete it
              </button>
              <button type="button" onClick={() => setConfirmDelete(false)} className="text-sm text-text-mid hover:text-text-hi">
                Keep
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              aria-label={`Delete ${circle.name}`}
              className="rounded-full p-2 text-text-low hover:text-danger"
            >
              <Trash2 size={15} />
            </button>
          )}
        </div>
      )}

      <p className="mt-5 flex items-start gap-3 rounded-2xl bg-text-hi/[0.05] px-4 py-3 text-sm leading-relaxed text-text-mid">
        <Lock size={15} className="mt-0.5 shrink-0 text-gold" aria-hidden="true" />
        Only you see this list. Members are not told they are in it, and a post you share here is read by
        them and you — nobody else, and it cannot be reshared.
      </p>
      {confirmDelete && (
        <p className="mt-2 text-xs text-warning">
          Posts already shared to this circle will then be visible to you alone.
        </p>
      )}

      {smart && circle.rule && (
        <p className="mt-4 inline-flex items-center gap-2 text-sm text-text-hi">
          <Sparkles size={14} className="text-gold" aria-hidden="true" />
          {describeRule(circle.rule)}
        </p>
      )}

      {!smart && !editing && <AddPeople circle={circle} onAdded={changed} />}

      <h3 className={cn(label, 'mt-7')}>
        {smart ? 'Reached right now' : 'Members'}
      </h3>
      {circle.members.length === 0 ? (
        <p className="mt-2 text-sm text-text-mid">
          {smart
            ? 'Nobody matches this rule yet. It updates by itself as people connect, follow or move.'
            : 'Nobody yet. Search above to add people.'}
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-[var(--cloud-border)]">
          {circle.members.map((m) => (
            <li key={m.user_id} className="flex items-center gap-3.5 py-3">
              <MemberAvatar handle={m.handle} displayName={m.display_name} avatarUrl={m.avatar_url} size={44} />
              <div className="min-w-0 flex-1">
                {m.handle ? (
                  <Link
                    to={`/u/${m.handle}`}
                    title={`@${m.handle}`}
                    className="flex items-center gap-1 text-sm font-semibold text-text-hi underline-offset-2 hover:underline"
                  >
                    <span className="truncate">{m.display_name ?? 'Member'}</span>
                    {m.verified && <BadgeCheck size={13} className="shrink-0 text-gold" aria-label="Verified" />}
                  </Link>
                ) : (
                  <span className="text-sm text-text-mid">A former member</span>
                )}
                {m.city && <span className="caption block truncate">{m.city}</span>}
              </div>
              {!m.active && (
                <span
                  className="rounded-full bg-warning/15 px-2 py-0.5 text-[0.65rem] font-semibold text-warning"
                  title="Your posts to this circle no longer reach them."
                >
                  Not reached
                </span>
              )}
              {!smart && (
                <button
                  type="button"
                  onClick={() => void removeMember(m.user_id)}
                  aria-label={`Remove ${m.display_name ?? m.handle ?? 'member'}`}
                  title="Remove from this circle"
                  className="shrink-0 rounded-full p-2 text-text-low hover:text-danger"
                >
                  <UserMinus size={15} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

/**
 * Circles — private, filtered audiences (blueprint module D).
 *
 * The open circle lives in the URL (`?open=<id>`), so the profile card's circle
 * chips land straight on it and a reload keeps your place.
 */
export default function Circles() {
  const circles = useApi<Circle[]>(() => kaluta.circles.list(), [])
  const [params, setParams] = useSearchParams()
  const openId = params.get('open')

  const open = (id: string | null) => setParams(id ? { open: id } : {}, { replace: true })

  const refresh = () => {
    circles.reload()
    announce('circles')
  }

  return (
    <AppShell>
      <header className="mb-6">
        <p className="mono-data text-[0.72rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Circles</p>
        <h1 className="mt-2 text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
          Share with the right people
        </h1>
        <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          Private, filtered networks. A post shared to a circle is visible to that circle only.
        </p>
      </header>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <CreateCircle
            onCreated={(created) => {
              refresh()
              open(created.id)
            }}
          />

          <div className="space-y-3">
            {circles.loading && !circles.data && <p className="text-sm text-text-mid">Loading your circles…</p>}
            {circles.error && <p className="text-sm text-warning">{circles.error}</p>}
            {circles.data?.length === 0 && (
              <div className="cloud-card px-6 py-10 text-center">
                <p className="text-base font-semibold text-text-hi">No circles yet</p>
                <p className="mt-1 text-sm leading-relaxed text-text-low">
                  Create one above, then choose it in the composer to share privately.
                </p>
              </div>
            )}
            {(circles.data ?? []).map((circle) => (
              <button
                key={circle.id}
                type="button"
                onClick={() => open(circle.id)}
                aria-current={openId === circle.id ? 'true' : undefined}
                className={cn(
                  'cloud-card flex w-full items-center gap-3.5 p-4 text-start transition-colors',
                  openId === circle.id ? '!border-gold/60 ring-1 ring-gold/40' : 'hover:border-gold/40',
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn('flex h-12 w-12 shrink-0 items-center justify-center rounded-[14px]', KIND_TILE[circle.kind] ?? KIND_TILE.custom)}
                >
                  {circle.kind === 'smart' ? <Sparkles size={19} /> : <Users size={20} />}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-[1.05rem] font-bold tracking-[-0.015em] text-text-hi">{circle.name}</span>
                  <span className="block text-sm text-text-low">
                    {KIND_LABEL[circle.kind] ?? circle.kind} · {circle.members_count}{' '}
                    {circle.members_count === 1 ? 'person' : 'people'}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>

        <section className="cloud-card min-h-[320px] p-6 md:p-8" aria-label="Circle details">
          {openId ? (
            <CirclePanel
              key={openId}
              id={openId}
              onChanged={refresh}
              onDeleted={() => {
                refresh()
                open(null)
              }}
            />
          ) : (
            <div className="flex h-full min-h-[260px] flex-col items-center justify-center text-center">
              <span className="grid h-14 w-14 place-items-center rounded-2xl bg-gold/15 text-gold-soft">
                <Users size={26} aria-hidden="true" />
              </span>
              <p className="mt-5 text-lg font-bold tracking-[-0.02em] text-text-hi">Open a circle</p>
              <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-text-low">
                See who it reaches, add people, or change its rule.
              </p>
            </div>
          )}
        </section>
      </div>
    </AppShell>
  )
}
