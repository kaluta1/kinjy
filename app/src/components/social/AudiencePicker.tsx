import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { Bell, Check, Eye, Search, Sparkles, UserMinus, UserPlus, Users, X } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { Sheet } from '@/components/community/CommunityDialogs'
import MemberAvatar from './MemberAvatar'
import { audienceIsEmpty, MODE_TEXT, type AudienceDraft } from '@/lib/audience'
import {
  ApiError,
  kaluta,
  type AudiencePerson,
  type AudiencePreview,
  type Circle,
} from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Find a member by name, debounced; answers for an older query are dropped.
 * `taken` marks who is already chosen.
 */
export function PersonSearch({
  placeholder,
  taken,
  actionLabel,
  onPick,
}: {
  placeholder: string
  taken: Set<string>
  actionLabel: string
  onPick: (person: AudiencePerson) => void
}) {
  const { tok } = useAppTheme()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<AudiencePerson[]>([])
  const latest = useRef(0)
  const q = query.trim()

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

  return (
    <div>
      <label className={cn('flex items-center gap-2 rounded-full px-3 py-1.5 focus-within:ring-1 focus-within:ring-gold/50', tok.input)}>
        <Search size={13} className={tok.low} aria-hidden="true" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={placeholder}
          aria-label={placeholder}
          className={cn('w-full bg-transparent text-xs focus:outline-none', tok.text)}
        />
        {query && (
          <button type="button" onClick={() => setQuery('')} aria-label="Clear search" className={tok.low}>
            <X size={12} />
          </button>
        )}
      </label>
      {q.length >= 2 && (
        <ul className="mt-1 max-h-56 space-y-0.5 overflow-y-auto">
          {results.length === 0 && <li className={cn('px-2 py-1 text-xs', tok.low)}>Nobody found for “{q}”.</li>}
          {results.map((person) => (
            <li key={person.user_id} className="flex items-center gap-2 rounded-lg px-2 py-1.5">
              <MemberAvatar handle={person.handle} displayName={person.display_name} avatarUrl={person.avatar_url} size={28} />
              <span className={cn('min-w-0 flex-1 truncate text-xs', tok.text)}>{person.display_name ?? person.handle}</span>
              {taken.has(person.user_id) ? (
                <Check size={12} className={tok.low} aria-label="Already chosen" />
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    onPick(person)
                    setQuery('')
                  }}
                  className="rounded-full border border-gold/50 px-2.5 py-0.5 text-[0.68rem] font-semibold text-gold-soft hover:bg-gold/10"
                >
                  {actionLabel}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function PersonChip({ person, onRemove, tone }: { person: AudiencePerson; onRemove: () => void; tone: 'in' | 'out' }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full py-0.5 pe-1 ps-2 text-[0.68rem] font-semibold',
        tone === 'in' ? 'bg-gold/15 text-gold-soft' : 'bg-danger/10 text-danger',
      )}
    >
      {tone === 'out' && <span className="sr-only">Left out:</span>}
      {person.display_name ?? person.handle ?? 'Member'}
      <button type="button" onClick={onRemove} aria-label={`Remove ${person.display_name ?? 'person'}`} className="rounded-full p-0.5 hover:bg-black/10">
        <X size={10} />
      </button>
    </span>
  )
}

/** Who a selection reaches, before posting: count, searchable list, who is left out. */
export function AudiencePreviewSheet({
  draft,
  onClose,
}: {
  draft: Pick<AudienceDraft, 'circleIds' | 'include' | 'exclude' | 'mode'>
  onClose: () => void
}) {
  const { tok } = useAppTheme()
  const [query, setQuery] = useState('')
  const [preview, setPreview] = useState<AudiencePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const selection = {
    circle_ids: draft.circleIds,
    include_ids: draft.include.map((p) => p.user_id),
    exclude_ids: draft.exclude.map((p) => p.user_id),
  }
  const key = JSON.stringify(selection)

  useEffect(() => {
    let live = true
    const timer = window.setTimeout(() => {
      kaluta.circles
        .audiencePreview(JSON.parse(key), query.trim() || undefined)
        .then((r) => live && (setPreview(r), setError(null)))
        .catch((err) => live && setError(err instanceof ApiError ? err.message : 'Could not work out the audience'))
    }, query ? 250 : 0)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [key, query])

  return (
    <Sheet title="Preview audience" onClose={onClose}>
      <div className="space-y-4 p-5">
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
        {!preview && !error && <p className={cn('text-sm', tok.mid)}>Working out who this reaches…</p>}
        {preview && (
          <>
            <p className={cn('text-2xl font-bold tracking-[-0.03em]', tok.text)}>
              {preview.count} {preview.count === 1 ? 'person' : 'people'}
              <span className={cn('ms-2 text-sm font-normal', tok.low)}>besides you</span>
            </p>
            <p className={cn('text-xs leading-relaxed', tok.mid)}>
              {MODE_TEXT[draft.mode].body}
              {preview.includes_smart_circle &&
                ' A smart circle follows its rule: who it reaches can change as people connect, follow or move.'}
            </p>
            {preview.archived_circles.length > 0 && (
              <p className="text-xs text-warning">An archived circle is chosen — restore it on the Circles page to post to it.</p>
            )}
            {preview.refused.length > 0 && (
              <p className="text-xs text-warning">
                {preview.refused.length} {preview.refused.length === 1 ? 'person' : 'people'} you added cannot receive it
                (a block or an age rule) and {preview.refused.length === 1 ? 'is' : 'are'} not counted.
              </p>
            )}
            <label className={cn('flex items-center gap-2 rounded-full px-3 py-1.5', tok.input)}>
              <Search size={13} className={tok.low} aria-hidden="true" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search this audience"
                aria-label="Search this audience"
                className={cn('w-full bg-transparent text-xs focus:outline-none', tok.text)}
              />
            </label>
            <ul className="max-h-72 space-y-1 overflow-y-auto">
              {preview.people.length === 0 && (
                <li className={cn('text-xs', tok.low)}>{query ? 'Nobody by that name in this audience.' : 'Nobody yet — only you would see it.'}</li>
              )}
              {preview.people.map((p) => (
                <li key={p.user_id} className="flex items-center gap-2.5 py-1">
                  <MemberAvatar handle={p.handle} displayName={p.display_name} avatarUrl={p.avatar_url} size={30} />
                  <span className={cn('truncate text-sm', tok.text)}>{p.display_name ?? p.handle ?? 'Member'}</span>
                </li>
              ))}
            </ul>
            {preview.count > preview.people.length && !query && (
              <p className={cn('text-xs', tok.low)}>Showing the first {preview.people.length}. Search to find someone.</p>
            )}
            {preview.excluded.length > 0 && (
              <div>
                <p className={cn('text-xs font-semibold', tok.mid)}>Left out ({preview.excluded.length})</p>
                <p className={cn('mt-1 text-xs', tok.low)}>
                  {preview.excluded.map((p) => p.display_name ?? p.handle ?? 'Member').join(', ')}
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </Sheet>
  )
}

/**
 * Choose one or more circles, add or leave out people by name, and say whether
 * the audience follows the circles or is fixed at publication.
 */
export default function AudiencePicker({
  circles,
  value,
  onChange,
  scheduled,
}: {
  circles: Circle[] | null
  value: AudienceDraft
  onChange: (next: AudienceDraft) => void
  scheduled: boolean
}) {
  const { tok } = useAppTheme()
  const [previewing, setPreviewing] = useState(false)
  const [addingPeople, setAddingPeople] = useState(value.include.length > 0)
  const [leavingOut, setLeavingOut] = useState(value.exclude.length > 0)
  const active = (circles ?? []).filter((c) => !c.archived)
  const set = (patch: Partial<AudienceDraft>) => onChange({ ...value, ...patch })
  const toggleCircle = (id: string) =>
    set({ circleIds: value.circleIds.includes(id) ? value.circleIds.filter((c) => c !== id) : [...value.circleIds, id] })
  const chosen = new Set([...value.include, ...value.exclude].map((p) => p.user_id))
  const smartChosen = active.some((c) => value.circleIds.includes(c.id) && c.kind === 'smart')

  return (
    <div className={cn('mt-3 space-y-3 rounded-card-sm p-3', tok.subtleBg)}>
      <p className={cn('text-xs leading-relaxed', tok.mid)}>
        Only the people you choose see this post. They are not shown the names of your circles or who else is in them,
        and it cannot be reshared — though nobody can stop a screenshot.
      </p>

      {active.length === 0 ? (
        <p className="rounded-card-sm border border-amber-400/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          You have no circles yet —{' '}
          <Link to="/circles" className="font-semibold underline underline-offset-2">
            create one
          </Link>
          , or add people by name below.
        </p>
      ) : (
        <div role="group" aria-label="Circles" className="flex flex-wrap gap-1.5">
          {active.map((c) => {
            const on = value.circleIds.includes(c.id)
            return (
              <button
                key={c.id}
                type="button"
                aria-pressed={on}
                onClick={() => toggleCircle(c.id)}
                className={cn(
                  'inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[0.72rem] font-semibold',
                  on ? 'border border-transparent bg-gold/20 text-gold-soft' : cn('border border-[var(--cloud-border)]', tok.mid, tok.hoverBg),
                )}
              >
                {on ? <Check size={11} aria-hidden="true" /> : c.kind === 'smart' ? <Sparkles size={11} aria-hidden="true" /> : <Users size={11} aria-hidden="true" />}
                {c.icon && <span aria-hidden="true">{c.icon}</span>}
                {c.name}
                <span className={cn('font-normal', on ? '' : tok.low)}>· {c.members_count}</span>
              </button>
            )
          })}
        </div>
      )}

      {value.include.length + value.exclude.length > 0 && (
      <div className="flex flex-wrap gap-1.5">
        {value.include.map((p) => (
          <PersonChip key={p.user_id} person={p} tone="in" onRemove={() => set({ include: value.include.filter((x) => x.user_id !== p.user_id) })} />
        ))}
        {value.exclude.map((p) => (
          <PersonChip key={p.user_id} person={p} tone="out" onRemove={() => set({ exclude: value.exclude.filter((x) => x.user_id !== p.user_id) })} />
        ))}
      </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          aria-expanded={addingPeople}
          onClick={() => setAddingPeople((v) => !v)}
          className={cn('inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[0.72rem] font-semibold', tok.mid, tok.hoverBg)}
        >
          <UserPlus size={11} aria-hidden="true" /> Add people
        </button>
        <button
          type="button"
          aria-expanded={leavingOut}
          onClick={() => setLeavingOut((v) => !v)}
          className={cn('inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[0.72rem] font-semibold', tok.mid, tok.hoverBg)}
        >
          <UserMinus size={11} aria-hidden="true" /> Leave someone out
        </button>
      </div>
      {addingPeople && (
        <PersonSearch
          placeholder="Add a person by name"
          actionLabel="Add"
          taken={chosen}
          onPick={(p) => set({ include: [...value.include, p] })}
        />
      )}
      {leavingOut && (
        <div>
          <PersonSearch
            placeholder="Leave out a person by name"
            actionLabel="Leave out"
            taken={chosen}
            onPick={(p) => set({ exclude: [...value.exclude, p] })}
          />
          <p className={cn('mt-1 text-[0.68rem]', tok.low)}>Someone left out never sees the post, even if one of the chosen circles includes them.</p>
        </div>
      )}

      <fieldset className="space-y-1.5">
        <legend className={cn('mb-1 text-xs font-semibold', tok.text)}>Who counts</legend>
        {(['live', 'snapshot'] as const).map((mode) => (
          <label key={mode} className={cn('flex cursor-pointer items-start gap-2 rounded-card-sm px-2 py-1.5', value.mode === mode && 'bg-gold/10')}>
            <input type="radio" name="audience-mode" checked={value.mode === mode} onChange={() => set({ mode })} className="mt-0.5 accent-[var(--color-gold,#d4a24c)]" />
            <span>
              <span className={cn('block text-xs font-semibold', tok.text)}>{MODE_TEXT[mode].title}</span>
              <span className={cn('block text-[0.68rem] leading-relaxed', tok.low)}>
                {MODE_TEXT[mode].body}
                {mode === 'snapshot' && scheduled && ' For a scheduled post, the list is taken when it publishes.'}
              </span>
            </span>
          </label>
        ))}
        {smartChosen && value.mode === 'live' && (
          <p className={cn('text-[0.68rem]', tok.low)}>A smart circle follows its rule, so who it reaches changes by itself.</p>
        )}
      </fieldset>

      <label className="flex cursor-pointer items-start gap-2">
        <input type="checkbox" checked={value.notify} onChange={(e) => set({ notify: e.target.checked })} className="mt-0.5" />
        <span>
          <span className={cn('inline-flex items-center gap-1 text-xs font-semibold', tok.text)}>
            <Bell size={11} aria-hidden="true" /> Let them know
          </span>
          <span className={cn('block text-[0.68rem]', tok.low)}>
            They get “shared a post with you” — never the name of a circle. People who muted your audience are not told.
          </span>
        </span>
      </label>

      <button
        type="button"
        onClick={() => setPreviewing(true)}
        disabled={audienceIsEmpty(value)}
        className="inline-flex items-center gap-1.5 rounded-full border border-gold/50 px-3 py-1.5 text-xs font-semibold text-gold-soft hover:bg-gold/10 disabled:opacity-40"
      >
        <Eye size={12} aria-hidden="true" /> Preview audience
      </button>
      {previewing && <AudiencePreviewSheet draft={value} onClose={() => setPreviewing(false)} />}
    </div>
  )
}
