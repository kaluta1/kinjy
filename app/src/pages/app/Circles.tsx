import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import {
  Archive,
  ArchiveRestore,
  BadgeCheck,
  Check,
  ChevronDown,
  Copy,
  Eye,
  Lock,
  Pencil,
  PenLine,
  Plus,
  Search,
  Sparkles,
  Trash2,
  UserMinus,
  UserPlus,
  Users,
  X,
} from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useApi } from '@/hooks/useApi'
import {
  ApiError,
  kaluta,
  type AudiencePerson,
  type Circle,
  type CircleDetail,
  type CircleRule,
  type CircleSort,
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

const SORTS: Array<{ id: CircleSort; label: string }> = [
  { id: 'created', label: 'Oldest first' },
  { id: 'name', label: 'Name' },
  { id: 'recent', label: 'Recently used' },
  { id: 'size', label: 'Size' },
]

/** A small picture for a circle, so a list of them reads at a glance. */
const ICONS = ['👪', '❤️', '💼', '🛒', '⭐', '🎓', '⚽', '🙏', '🎵', '🏠', '🌍', '🤝']

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
const area =
  'w-full rounded-2xl border border-transparent bg-text-hi/[0.07] px-5 py-3 text-[0.95rem] text-text-hi placeholder:text-text-low focus:border-gold/50 focus:bg-transparent focus:outline-none'
const chip = (on: boolean) =>
  cn(
    'rounded-full px-4 py-2 text-sm font-semibold transition-colors',
    on ? 'bg-gold/20 text-gold-soft' : 'text-text-mid hover:bg-text-hi/[0.07] hover:text-text-hi',
  )
const heading = 'text-base font-bold tracking-[-0.015em] text-text-hi'
const ghost =
  'inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'

/** One tile colour per circle kind, so the list reads at a glance. */
const KIND_TILE: Record<string, string> = {
  family: 'bg-coral/15 text-coral',
  close_friends: 'bg-gold/20 text-gold-soft',
  business: 'bg-sky/15 text-sky',
  customers: 'bg-success/15 text-success',
  custom: 'bg-text-hi/[0.08] text-text-mid',
  smart: 'bg-indigo/15 text-indigo',
}

function CircleTile({ circle, size = 48 }: { circle: Pick<Circle, 'kind' | 'icon'>; size?: number }) {
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size }}
      className={cn('flex shrink-0 items-center justify-center rounded-[14px] text-xl', KIND_TILE[circle.kind] ?? KIND_TILE.custom)}
    >
      {circle.icon ? circle.icon : circle.kind === 'smart' ? <Sparkles size={19} /> : <Users size={20} />}
    </span>
  )
}

function IconPicker({ value, onChange }: { value: string | null; onChange: (icon: string | null) => void }) {
  return (
    <div role="group" aria-label="Icon" className="flex flex-wrap gap-1.5">
      <button type="button" aria-pressed={!value} onClick={() => onChange(null)} className={cn('h-9 rounded-full px-3 text-xs', chip(!value))}>
        None
      </button>
      {ICONS.map((icon) => (
        <button
          key={icon}
          type="button"
          aria-pressed={value === icon}
          aria-label={`Icon ${icon}`}
          onClick={() => onChange(icon)}
          className={cn('grid h-9 w-9 place-items-center rounded-full text-lg', value === icon ? 'bg-gold/25 ring-1 ring-gold/60' : 'hover:bg-text-hi/[0.07]')}
        >
          {icon}
        </button>
      ))}
    </div>
  )
}

/** Who a smart rule reaches right now, asked as the rule changes. */
function SmartPreview({ rule }: { rule: CircleRule }) {
  const [result, setResult] = useState<{ count: number; items: AudiencePerson[] } | null>(null)
  const [open, setOpen] = useState(false)
  const key = JSON.stringify(cleanRule(rule))

  useEffect(() => {
    let live = true
    const timer = window.setTimeout(() => {
      kaluta.circles
        .smartPreview(JSON.parse(key))
        .then((r) => live && setResult(r))
        .catch(() => live && setResult(null))
    }, 300)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [key])

  if (!result) return null
  return (
    <div className="text-sm">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="inline-flex items-center gap-1.5 font-semibold text-text-hi">
        <Eye size={14} className="text-gold" aria-hidden="true" />
        Matches {result.count} {result.count === 1 ? 'person' : 'people'} right now
        {result.count > 0 && <ChevronDown size={14} className={cn('transition-transform', open && 'rotate-180')} aria-hidden="true" />}
      </button>
      {open && result.items.length > 0 && (
        <p className="mt-1.5 text-xs leading-relaxed text-text-low">
          {result.items.map((p) => p.display_name ?? p.handle ?? 'Member').join(', ')}
          {result.count > result.items.length && '…'}
        </p>
      )}
    </div>
  )
}

/** Source, country and city of a smart circle. */
function RuleFields({ rule, onChange }: { rule: CircleRule; onChange: (rule: CircleRule) => void }) {
  return (
    <div className="mt-4 space-y-3 rounded-2xl bg-text-hi/[0.05] p-4">
      <p className="text-sm text-text-low">
        A smart circle is a rule, not a list: who it reaches is worked out each time someone reads, so it changes by
        itself as people connect, follow or move. You can still leave particular people out.
      </p>
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
      <SmartPreview rule={rule} />
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
function CreateCircle({ onCreated, first }: { onCreated: (circle: CircleDetail) => void; first: boolean }) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<string>('family')
  const [rule, setRule] = useState<CircleRule>({ source: 'connections' })
  const [description, setDescription] = useState('')
  const [icon, setIcon] = useState<string | null>(null)
  const [more, setMore] = useState(false)
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
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(icon ? { icon } : {}),
      })
      setName('')
      setDescription('')
      setIcon(null)
      onCreated(created)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the circle')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={create} className="cloud-card p-5 md:p-6">
      <h2 className={cn(heading, 'mb-3')}>{first ? 'Create your first circle' : 'New circle'}</h2>
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
          placeholder={kind === 'smart' ? 'Name it — Friends in Nairobi' : 'Name this circle — Demo family'}
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
      <button
        type="button"
        aria-expanded={more}
        onClick={() => setMore((v) => !v)}
        className="mt-3 inline-flex items-center gap-1 text-sm font-semibold text-text-mid hover:text-text-hi"
      >
        Description and icon
        <ChevronDown size={14} className={cn('transition-transform', more && 'rotate-180')} aria-hidden="true" />
      </button>
      {more && (
        <div className="mt-3 space-y-3">
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What it is for — only you see this"
            aria-label="Description"
            maxLength={500}
            rows={2}
            className={area}
          />
          <IconPicker value={icon} onChange={setIcon} />
        </div>
      )}
      {countryInvalid && <p className="mt-2 text-xs text-warning">A country is two letters, like KE or FR.</p>}
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
    </form>
  )
}

/**
 * Find people and add them — static circles only. Pick one or several, then
 * add them together.
 */
function AddPeople({ circle, onAdded }: { circle: CircleDetail; onAdded: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<AudiencePerson[]>([])
  const [picked, setPicked] = useState<AudiencePerson[]>([])
  const [busy, setBusy] = useState(false)
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
  const isPicked = (id: string) => picked.some((p) => p.user_id === id)
  const toggle = (person: AudiencePerson) =>
    setPicked((list) => (isPicked(person.user_id) ? list.filter((p) => p.user_id !== person.user_id) : [...list, person]))

  const addAll = async () => {
    if (picked.length === 0) return
    setBusy(true)
    setNote(null)
    try {
      const r = await kaluta.circles.bulk(circle.id, { add: picked.map((p) => p.user_id) })
      setPicked([])
      setQuery('')
      if (r.refused.length > 0) {
        setNote(
          `${r.added.length} added. ${r.refused.length} could not be added — they may have blocked you, be out of reach for their age, or the circle is full.`,
        )
      }
      onAdded()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not add them')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-5">
      <label className="flex items-center gap-3 rounded-full bg-text-hi/[0.07] px-5 py-3 focus-within:ring-1 focus-within:ring-gold/50">
        <UserPlus size={16} className="shrink-0 text-text-low" aria-hidden="true" />
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
              <span className="min-w-0 flex-1" title={person.handle ? `@${person.handle}` : undefined}>
                <span className="block truncate text-sm text-text-hi">{person.display_name}</span>
              </span>
              {inCircle.has(person.user_id) ? (
                <span className="inline-flex items-center gap-1 text-xs text-text-mid">
                  <Check size={12} aria-hidden="true" /> In circle
                </span>
              ) : (
                <button
                  type="button"
                  aria-pressed={isPicked(person.user_id)}
                  onClick={() => toggle(person)}
                  className={cn(
                    'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-4 py-1.5 text-sm font-semibold',
                    isPicked(person.user_id) ? 'border-gold bg-gold/15 text-gold-soft' : 'border-gold/50 text-text-hi hover:bg-gold/10',
                  )}
                >
                  {isPicked(person.user_id) ? <Check size={12} aria-hidden="true" /> : <Plus size={12} aria-hidden="true" />}
                  {isPicked(person.user_id) ? 'Picked' : 'Pick'}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {picked.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {picked.map((p) => (
            <span key={p.user_id} className="inline-flex items-center gap-1 rounded-full bg-gold/15 py-1 pe-1.5 ps-3 text-xs font-semibold text-gold-soft">
              {p.display_name ?? p.handle}
              <button type="button" onClick={() => toggle(p)} aria-label={`Unpick ${p.display_name ?? 'person'}`} className="rounded-full p-0.5">
                <X size={11} />
              </button>
            </span>
          ))}
          <button
            type="button"
            onClick={() => void addAll()}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-full bg-gold-soft px-4 py-1.5 text-sm font-bold text-ink disabled:opacity-40"
          >
            <UserPlus size={13} aria-hidden="true" /> Add {picked.length} {picked.length === 1 ? 'person' : 'people'}
          </button>
        </div>
      )}
      {note && <p className="mt-2 text-xs text-warning">{note}</p>}
    </div>
  )
}

/** Rename, describe, change kind, icon or rule — one small form rather than many buttons. */
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
  const [description, setDescription] = useState(circle.description ?? '')
  const [icon, setIcon] = useState<string | null>(circle.icon ?? null)
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
          description: description.trim() || null,
          icon,
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
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="What it is for — only you see this"
        aria-label="Description"
        maxLength={500}
        rows={2}
        className={area}
      />
      <IconPicker value={icon} onChange={setIcon} />
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

/** People left out of a smart circle whatever its rule says, and a way to bring them back. */
function Exclusions({ circleId, version, onChanged }: { circleId: string; version: number; onChanged: () => void }) {
  const list = useApi(() => kaluta.circles.exclusions(circleId), [circleId, version])
  const items = list.data?.items ?? []
  if (items.length === 0) return null
  return (
    <div className="mt-6">
      <h3 className={heading}>Left out ({items.length})</h3>
      <p className="mt-1 text-sm text-text-low">They match the rule but never get what you share here.</p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {items.map((p) => (
          <li key={p.user_id} className="inline-flex items-center gap-1.5 rounded-full bg-text-hi/[0.07] py-1 pe-1.5 ps-3 text-sm text-text-hi">
            {p.display_name ?? p.handle ?? 'Member'}
            <button
              type="button"
              onClick={() => void kaluta.circles.unexclude(circleId, p.user_id).then(onChanged)}
              className="rounded-full px-2 py-0.5 text-xs font-semibold text-gold-soft hover:bg-gold/10"
            >
              Bring back
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** What I shared with this circle, scheduled posts included, and how many opted out. */
function SharedHere({ circleId }: { circleId: string }) {
  const [open, setOpen] = useState(false)
  const posts = useApi(() => (open ? kaluta.circles.posts(circleId) : Promise.resolve(null)), [circleId, open])
  return (
    <div className="mt-7">
      <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className={cn(heading, 'inline-flex items-center gap-1.5')}>
        Shared with this circle
        <ChevronDown size={15} className={cn('transition-transform', open && 'rotate-180')} aria-hidden="true" />
      </button>
      {open && (
        <div className="mt-2">
          {posts.loading && !posts.data && <p className="text-sm text-text-mid">Loading…</p>}
          {posts.data && posts.data.items.length === 0 && <p className="text-sm text-text-mid">Nothing shared here yet.</p>}
          <ul className="divide-y divide-[var(--cloud-border)]">
            {(posts.data?.items ?? []).map((p) => (
              <li key={p.id} className="py-2.5">
                <Link to={`/p/${p.id}`} className="block text-sm text-text-hi hover:underline">
                  {p.excerpt.replace(/<[^>]+>/g, ' ').trim() || 'A post with media'}
                </Link>
                <span className="caption">
                  {p.status === 'scheduled' && p.publish_at
                    ? `Scheduled for ${new Date(p.publish_at).toLocaleString()}`
                    : new Date(p.created_at).toLocaleDateString()}
                  {' · '}
                  {p.audience_mode === 'snapshot' ? 'Fixed audience' : 'Follows the circle'}
                </span>
              </li>
            ))}
          </ul>
          {typeof posts.data?.opted_out_count === 'number' && (
            <p className="mt-2 text-xs text-text-low">
              {posts.data.opted_out_count} people opted out of what you share here. Who they are is not shown.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function CirclePanel({
  id,
  onChanged,
  onDeleted,
  onOpen,
}: {
  id: string
  onChanged: () => void
  onDeleted: () => void
  onOpen: (id: string) => void
}) {
  const [memberQuery, setMemberQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(memberQuery.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [memberQuery])
  const detail = useApi<CircleDetail>(() => kaluta.circles.get(id, debounced || undefined), [id, debounced])
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [duplicating, setDuplicating] = useState(false)
  const [copyName, setCopyName] = useState('')
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [version, setVersion] = useState(0)

  const changed = () => {
    detail.reload()
    setVersion((v) => v + 1)
    onChanged()
  }

  const run = async (action: () => Promise<unknown>, failure: string) => {
    setError(null)
    try {
      await action()
      changed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failure)
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

  const duplicate = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!copyName.trim()) return
    setError(null)
    try {
      const copy = await kaluta.circles.duplicate(id, copyName.trim())
      setDuplicating(false)
      onChanged()
      onOpen(copy.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not duplicate the circle')
    }
  }

  const removeSelected = () =>
    void run(async () => {
      await kaluta.circles.bulk(id, { remove: [...selected] })
      setSelected(new Set())
      setSelecting(false)
    }, 'Could not remove them')

  if (detail.loading && !detail.data) return <p className="text-sm text-text-mid">Opening the circle…</p>
  if (detail.error || !detail.data) {
    return <p className="text-sm text-warning">{detail.error ?? 'This circle could not be opened.'}</p>
  }
  const circle = detail.data
  const smart = circle.kind === 'smart'
  const archived = Boolean(circle.archived)

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
        <>
          <div className="flex flex-wrap items-start gap-3">
            <CircleTile circle={circle} size={56} />
            <div className="min-w-0 flex-1">
              <h2 className="truncate text-[clamp(26px,3vw,34px)] font-bold leading-tight tracking-[-0.035em] text-text-hi">{circle.name}</h2>
              <p className="mt-1.5 text-sm text-text-low">
                {KIND_LABEL[circle.kind] ?? circle.kind} · reaches {circle.members_count}{' '}
                {circle.members_count === 1 ? 'person' : 'people'}
                {archived && ' · archived'}
              </p>
              {circle.description && <p className="mt-2 text-sm leading-relaxed text-text-mid">{circle.description}</p>}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {!archived && (
              <Link
                to={`/hub?share=${circle.id}`}
                className="inline-flex items-center gap-1.5 rounded-full bg-gold-soft px-4 py-2 text-sm font-bold text-ink"
              >
                <PenLine size={13} aria-hidden="true" /> Share a post with this circle
              </Link>
            )}
            <button type="button" onClick={() => setEditing(true)} className={ghost}>
              <Pencil size={13} aria-hidden="true" /> Edit
            </button>
            <button
              type="button"
              onClick={() => {
                setCopyName(`${circle.name} (copy)`.slice(0, 80))
                setDuplicating((v) => !v)
              }}
              aria-expanded={duplicating}
              className={ghost}
            >
              <Copy size={13} aria-hidden="true" /> Duplicate
            </button>
            <button
              type="button"
              onClick={() => void run(() => kaluta.circles.update(id, { archived: !archived }), 'Could not change it')}
              className={ghost}
              title={archived ? undefined : 'Hidden from the composer; what you shared keeps its audience.'}
            >
              {archived ? <ArchiveRestore size={13} aria-hidden="true" /> : <Archive size={13} aria-hidden="true" />}
              {archived ? 'Restore' : 'Archive'}
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
          {duplicating && (
            <form onSubmit={duplicate} className="mt-3 flex gap-2">
              <input value={copyName} onChange={(e) => setCopyName(e.target.value)} aria-label="Name of the copy" maxLength={80} className={field} />
              <button type="submit" disabled={!copyName.trim()} className="shrink-0 rounded-full bg-gold-soft px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40">
                Copy
              </button>
            </form>
          )}
        </>
      )}

      {confirmDelete && (
        <p className="mt-3 text-sm text-warning">
          Deleting cannot be undone. Posts shared only with this circle will then be seen by you alone; posts shared with
          other circles or people too keep those. Archiving instead keeps everything as it is.
        </p>
      )}

      <p className="mt-5 flex items-start gap-3 rounded-2xl bg-text-hi/[0.05] px-4 py-3 text-sm leading-relaxed text-text-mid">
        <Lock size={15} className="mt-0.5 shrink-0 text-gold" aria-hidden="true" />
        Only you see this list. Members are not told they are in it, never see its name or who else is in it, and a post
        you share here cannot be reshared. They can mute or opt out of what you share through it.
      </p>

      {smart && circle.rule && (
        <div className="mt-4 space-y-1">
          <p className="inline-flex items-center gap-2 text-sm text-text-hi">
            <Sparkles size={14} className="text-gold" aria-hidden="true" />
            {describeRule(circle.rule)}
          </p>
          <p className="text-xs text-text-low">Updated by itself. Use “Leave out” on someone to keep them out whatever the rule says.</p>
        </div>
      )}

      {!smart && !editing && !archived && <AddPeople circle={circle} onAdded={changed} />}

      <div className="mt-7 flex flex-wrap items-center gap-2">
        <h3 className={cn(heading, 'me-auto')}>{smart ? 'Reached right now' : 'Members'}</h3>
        {!smart && circle.members.length > 0 && (
          <button
            type="button"
            onClick={() => {
              setSelecting((v) => !v)
              setSelected(new Set())
            }}
            className="text-sm font-semibold text-text-mid hover:text-text-hi"
          >
            {selecting ? 'Cancel' : 'Select'}
          </button>
        )}
      </div>
      {(circle.members_count > 8 || debounced) && (
        <label className="mt-3 flex items-center gap-3 rounded-full bg-text-hi/[0.07] px-5 py-2.5">
          <Search size={15} className="text-text-low" aria-hidden="true" />
          <input
            value={memberQuery}
            onChange={(e) => setMemberQuery(e.target.value)}
            placeholder="Search members"
            aria-label="Search members"
            className="w-full bg-transparent text-sm text-text-hi placeholder:text-text-low focus:outline-none"
          />
        </label>
      )}
      {selecting && selected.size > 0 && (
        <button
          type="button"
          onClick={removeSelected}
          className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-red-600 px-4 py-2 text-sm font-bold text-white hover:bg-red-700"
        >
          <UserMinus size={13} aria-hidden="true" /> Remove {selected.size}
        </button>
      )}
      {circle.members.length === 0 ? (
        <p className="mt-2 text-sm text-text-mid">
          {debounced
            ? 'Nobody by that name in this circle.'
            : smart
              ? 'Nobody matches this rule yet. It updates by itself as people connect, follow or move.'
              : 'Nobody yet. Search above to add people.'}
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-[var(--cloud-border)]">
          {circle.members.map((m) => (
            <li key={m.user_id} className="flex items-center gap-3.5 py-3">
              {selecting && (
                <input
                  type="checkbox"
                  aria-label={`Select ${m.display_name ?? 'member'}`}
                  checked={selected.has(m.user_id)}
                  onChange={(e) =>
                    setSelected((set) => {
                      const next = new Set(set)
                      if (e.target.checked) next.add(m.user_id)
                      else next.delete(m.user_id)
                      return next
                    })
                  }
                />
              )}
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
              {smart ? (
                <button
                  type="button"
                  onClick={() => void run(() => kaluta.circles.exclude(id, m.user_id), 'Could not leave them out')}
                  title="Keep them out whatever the rule says"
                  className="shrink-0 rounded-full px-3 py-1 text-xs font-semibold text-text-low hover:text-danger"
                >
                  Leave out
                </button>
              ) : (
                !selecting && (
                  <button
                    type="button"
                    onClick={() => void run(() => kaluta.circles.removeMember(id, m.user_id), 'Could not remove them')}
                    aria-label={`Remove ${m.display_name ?? m.handle ?? 'member'}`}
                    title="Remove from this circle"
                    className="shrink-0 rounded-full p-2 text-text-low hover:text-danger"
                  >
                    <UserMinus size={15} />
                  </button>
                )
              )}
            </li>
          ))}
        </ul>
      )}
      {smart && <Exclusions circleId={id} version={version} onChanged={changed} />}
      <SharedHere circleId={id} />
      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

/** Shown until the first circle exists: what a circle is for, in three steps. */
function FirstCircleGuide() {
  const steps = [
    ['Make a circle', 'Family, close friends, clients — or a smart circle that fills itself by a rule.'],
    ['Add people', 'Nobody is told they are in it, and nobody else sees the list.'],
    ['Share with it', 'In the composer choose “Circles & people”: only they will see the post.'],
  ]
  return (
    <div className="cloud-card px-6 py-6">
      <p className="text-base font-semibold text-text-hi">How circles work</p>
      <ol className="mt-3 space-y-3">
        {steps.map(([title, body], i) => (
          <li key={title} className="flex gap-3">
            <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-gold/15 text-sm font-bold text-gold-soft">{i + 1}</span>
            <span>
              <span className="block text-sm font-semibold text-text-hi">{title}</span>
              <span className="block text-sm leading-relaxed text-text-low">{body}</span>
            </span>
          </li>
        ))}
      </ol>
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
  const [sort, setSort] = useState<CircleSort>('created')
  const [showArchived, setShowArchived] = useState(false)
  const circles = useApi<Circle[]>(() => kaluta.circles.list({ sort }), [sort])
  const [params, setParams] = useSearchParams()
  const openId = params.get('open')

  const open = (id: string | null) => setParams(id ? { open: id } : {}, { replace: true })

  const refresh = () => {
    circles.reload()
    announce('circles')
  }

  const all = circles.data ?? []
  const active = all.filter((c) => !c.archived)
  const archived = all.filter((c) => c.archived)
  const shown = showArchived ? archived : active

  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
          Share with the right people
        </h1>
        <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          A circle is a private list of people. Share a post with one or several circles — or with people you pick by
          name — and only they see it. Nobody is told which circle they are in, or who else is.{' '}
          <Link to="/settings/audiences" className="font-semibold text-gold-soft underline-offset-2 hover:underline">
            Private audiences you receive
          </Link>
        </p>
      </header>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          {circles.data?.length === 0 && <FirstCircleGuide />}
          <CreateCircle
            first={circles.data?.length === 0}
            onCreated={(created) => {
              refresh()
              open(created.id)
            }}
          />

          {all.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <label className="inline-flex items-center gap-2 text-sm text-text-mid">
                Sort
                <select
                  value={sort}
                  onChange={(e) => setSort(e.target.value as CircleSort)}
                  className="rounded-full bg-text-hi/[0.07] px-3 py-1.5 text-sm text-text-hi"
                >
                  {SORTS.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              {archived.length > 0 && (
                <button
                  type="button"
                  aria-pressed={showArchived}
                  onClick={() => setShowArchived((v) => !v)}
                  className={cn('ms-auto', chip(showArchived))}
                >
                  Archived ({archived.length})
                </button>
              )}
            </div>
          )}

          <div className="space-y-3">
            {circles.loading && !circles.data && <p className="text-sm text-text-mid">Loading your circles…</p>}
            {circles.error && <p className="text-sm text-warning">{circles.error}</p>}
            {shown.map((circle) => (
              <button
                key={circle.id}
                type="button"
                onClick={() => open(circle.id)}
                aria-current={openId === circle.id ? 'true' : undefined}
                className={cn(
                  'cloud-card flex w-full items-center gap-3.5 p-4 text-start transition-colors',
                  openId === circle.id ? '!border-gold/60 ring-1 ring-gold/40' : 'hover:border-gold/40',
                  circle.archived && 'opacity-70',
                )}
              >
                <CircleTile circle={circle} />
                <span className="min-w-0">
                  <span className="block truncate text-[1.05rem] font-bold tracking-[-0.015em] text-text-hi">{circle.name}</span>
                  <span className="block text-sm text-text-low">
                    {KIND_LABEL[circle.kind] ?? circle.kind} · {circle.members_count}{' '}
                    {circle.members_count === 1 ? 'person' : 'people'}
                  </span>
                  {circle.description && <span className="block truncate text-xs text-text-low">{circle.description}</span>}
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
              onOpen={open}
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
                See who it reaches, add people, change its rule, or share a post with it.
              </p>
            </div>
          )}
        </section>
      </div>
    </AppShell>
  )
}
