import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Check, MessageCircle, Search, UserMinus, UserPlus, Users, X } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type ConnectionEntry, type PersonSuggestion } from '@/lib/api'
import { announce, onChange } from '@/lib/live'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { cn } from '@/lib/utils'

type Tab = 'incoming' | 'outgoing' | 'accepted'

const TABS: Array<{ id: Tab; label: string; empty: string }> = [
  { id: 'incoming', label: 'Received', empty: 'No invitations waiting for you.' },
  { id: 'outgoing', label: 'Sent', empty: 'You have no invitations waiting for an answer.' },
  { id: 'accepted', label: 'Connections', empty: 'No connections yet. Invite someone from their profile.' },
]

const isTab = (value: string | null): value is Tab => TABS.some((t) => t.id === value)

/**
 * Everything about the people you are connected to, in one place.
 *
 * A connection is mutual — an accepted invitation — and it is what opens
 * messaging. That made it worth a page of its own rather than a panel buried in
 * the dashboard's privacy tab, which only showed the lists without letting you
 * cancel an invitation or end a connection. Who may invite you stays a privacy
 * setting, so this page links there instead of repeating it.
 */
export default function Connections() {
  const connections = useApi(() => kaluta.connections.list(), [])
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  // A row stays disabled until the refreshed lists arrive — not merely until
  // the request returns — so a second click cannot answer an invitation twice.
  const [pending, setPending] = useState<{ userId: string; data: unknown } | null>(null)
  const busy = pending && pending.data === connections.data ? pending.userId : null
  const [error, setError] = useState<string | null>(null)
  // The connection awaiting a yes, so the dialog can name the person.
  const [confirmRemove, setConfirmRemove] = useState<ConnectionEntry | null>(null)

  const requested = params.get('tab')
  const incoming = connections.data?.incoming ?? []
  // Land on what needs an answer; otherwise on the connections themselves.
  const tab: Tab = isTab(requested) ? requested : incoming.length > 0 ? 'incoming' : 'accepted'

  useEffect(() => onChange('connections', connections.reload), [connections.reload])

  const run = async (entry: ConnectionEntry, action: () => Promise<unknown>, failure: string) => {
    setPending({ userId: entry.user_id, data: connections.data })
    setError(null)
    try {
      await action()
      announce('connections')
      // Counts elsewhere (the top bar, the profile card) read the profile topic.
      announce('profile')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : failure)
      setPending(null)
    }
  }

  const message = async (entry: ConnectionEntry) => {
    setPending({ userId: entry.user_id, data: connections.data })
    setError(null)
    try {
      const conversation = await kaluta.messages.start([entry.user_id])
      navigate(`/messages?c=${conversation.id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not open a conversation')
      setPending(null)
    }
  }

  const remove = (entry: ConnectionEntry) => {
    setConfirmRemove(null)
    void run(entry, () => kaluta.connections.remove(entry.user_id), 'Could not remove the connection')
  }

  const actionsFor = (entry: ConnectionEntry) => {
    const disabled = busy === entry.user_id
    const name = entry.profile?.display_name ?? 'this person'
    if (tab === 'incoming') {
      return (
        <>
          <IconButton
            label={`Accept ${name}`}
            disabled={disabled}
            tone="good"
            onClick={() => run(entry, () => kaluta.connections.respond(entry.user_id, true), 'Could not accept')}
          >
            <Check size={17} />
          </IconButton>
          <IconButton
            label={`Decline ${name}`}
            disabled={disabled}
            tone="bad"
            onClick={() => run(entry, () => kaluta.connections.respond(entry.user_id, false), 'Could not decline')}
          >
            <X size={17} />
          </IconButton>
        </>
      )
    }
    if (tab === 'outgoing') {
      return (
        <button
          type="button"
          disabled={disabled}
          onClick={() => run(entry, () => kaluta.connections.remove(entry.user_id), 'Could not cancel the invitation')}
          className="rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-red-400/50 hover:text-red-200 disabled:opacity-40"
        >
          Cancel
        </button>
      )
    }
    return (
      <>
        <IconButton label={`Message ${name}`} disabled={disabled} tone="neutral" onClick={() => message(entry)}>
          <MessageCircle size={17} />
        </IconButton>
        <IconButton label={`Remove ${name}`} disabled={disabled} tone="bad" onClick={() => setConfirmRemove(entry)}>
          <UserMinus size={17} />
        </IconButton>
      </>
    )
  }

  const current = TABS.find((t) => t.id === tab)!
  const rows = connections.data?.[tab] ?? []

  // Hoisted into a local: TypeScript drops the `connections.data &&`
  // narrowing inside the map callback, because nothing guarantees the field
  // is still set by the time the callback runs. The binding is what is
  // actually constant here, so it is the thing to check.
  const groups = connections.data

  return (
    <AppShell>
      <PeopleSearch
        standingOf={(id) =>
          groups?.accepted.some((e) => e.user_id === id)
            ? 'connected'
            : groups?.outgoing.some((e) => e.user_id === id)
              ? 'invited'
              : groups?.incoming.some((e) => e.user_id === id)
                ? 'invites_you'
                : 'none'
        }
      />

      {groups && (
        <div
          role="group"
          aria-label="Show"
          className="mt-6 inline-flex max-w-full gap-1 overflow-x-auto rounded-full border border-[var(--cloud-border)] bg-text-hi/[0.04] p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {TABS.map((t) => {
            const count = groups[t.id].length
            return (
              <button
                key={t.id}
                type="button"
                aria-pressed={tab === t.id}
                onClick={() => setParams({ tab: t.id }, { replace: true })}
                className={cn(
                  'inline-flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-2 text-sm font-semibold transition-colors sm:gap-2 sm:px-5',
                  tab === t.id
                    ? 'bg-gradient-to-br from-gold-soft to-gold text-ink shadow-[0_8px_20px_-10px_rgba(166,120,57,0.6)]'
                    : 'text-text-mid hover:text-text-hi',
                )}
              >
                {t.label}
                <span
                  className={cn(
                    'mono-data rounded-full px-2 py-0.5 text-xs',
                    tab === t.id ? 'bg-ink/15' : 'bg-text-hi/[0.08]',
                  )}
                >
                  {count}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {error && (
        <p role="alert" className="mt-4 rounded-xl bg-red-500/10 px-4 py-3 text-sm text-red-200">
          {error}
        </p>
      )}

      <div className="mt-6">
        {connections.loading && <p className="text-sm text-text-low">Loading your connections…</p>}
        {connections.error && <p className="text-sm text-amber-200">{connections.error}</p>}
        {connections.data && rows.length === 0 && (
          <div className="cloud-card px-6 py-14 text-center">
            <span className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-gold/15 text-gold-soft">
              <Users size={26} aria-hidden="true" />
            </span>
            <p className="mx-auto mt-5 max-w-sm text-base font-semibold text-text-hi">{current.empty}</p>
          </div>
        )}
        <ul className="grid gap-4 sm:grid-cols-2">
          {rows.map((entry) => (
            <li key={entry.id} className="cloud-card flex items-center gap-4 p-4">
              <MemberAvatar
                handle={entry.profile?.handle}
                displayName={entry.profile?.display_name}
                avatarUrl={entry.profile?.avatar_url}
                size={56}
              />
              <div className="min-w-0 flex-1">
                {entry.profile ? (
                  <Link
                    to={`/u/${entry.profile.handle}`}
                    className="block truncate text-[1.05rem] font-bold tracking-[-0.015em] text-text-hi hover:text-gold-soft"
                    title={`@${entry.profile.handle}`}
                  >
                    {entry.profile.display_name}
                  </Link>
                ) : (
                  <p className="truncate text-[1.05rem] font-bold text-text-hi">Unknown member</p>
                )}
                {tab === 'incoming' && entry.message && (
                  <p className="mt-2 line-clamp-2 rounded-xl bg-text-hi/[0.05] px-3 py-2 text-sm leading-relaxed text-text-mid">
                    “{entry.message}”
                  </p>
                )}
              </div>
              <div className="flex shrink-0 gap-2">{actionsFor(entry)}</div>
            </li>
          ))}
        </ul>
      </div>

      <PeopleYouMayKnow />
      <ConfirmDialog
        open={Boolean(confirmRemove)}
        onOpenChange={(open) => !open && setConfirmRemove(null)}
        title="Remove this connection?"
        description={
          <>
            {confirmRemove?.profile?.display_name ?? 'This person'} will be removed from your
            connections, and you will no longer be able to message each other. You can connect
            again later.
          </>
        }
        confirmLabel="Remove connection"
        onConfirm={() => confirmRemove && remove(confirmRemove)}
      />
    </AppShell>
  )
}

const TONES = {
  good: 'bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25',
  bad: 'bg-text-hi/[0.07] text-text-mid hover:bg-red-500/15 hover:text-red-200',
  neutral: 'bg-text-hi/[0.07] text-text-mid hover:bg-gold/20 hover:text-gold-soft',
} as const

function IconButton({
  label,
  tone,
  disabled,
  onClick,
  children,
}: {
  label: string
  tone: keyof typeof TONES
  disabled: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={cn('grid h-10 w-10 place-items-center rounded-full transition-colors disabled:opacity-40', TONES[tone])}
    >
      {children}
    </button>
  )
}


/** What the Connect button does for somebody, given where you stand with them. */
type Standing = 'none' | 'invited' | 'invites_you' | 'connected'

function ConnectButton({ userId, name, standing: initial }: { userId: string; name: string; standing: Standing }) {
  const [standing, setStanding] = useState<Standing>(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async () => {
    setBusy(true)
    setError(null)
    try {
      if (standing === 'invites_you') {
        // Accepting is answering their invitation, not sending a new one.
        await kaluta.connections.respond(userId, true)
        setStanding('connected')
      } else {
        const result = await kaluta.connections.invite(userId)
        setStanding(result.status === 'accepted' ? 'connected' : 'invited')
      }
      announce('connections')
      announce('profile')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not connect')
    } finally {
      setBusy(false)
    }
  }
  if (standing === 'connected') {
    return <span className="rounded-full bg-success/10 px-3.5 py-2 text-xs font-semibold text-success">Connected</span>
  }
  if (standing === 'invited') {
    return <span className="rounded-full bg-text-hi/[0.07] px-3.5 py-2 text-xs font-semibold text-text-mid">Invited</span>
  }
  return (
    <span className="flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={act}
        disabled={busy}
        aria-label={standing === 'invites_you' ? `Accept ${name}` : `Connect with ${name}`}
        className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-3.5 py-2 text-xs font-bold text-ink disabled:opacity-40"
      >
        <UserPlus size={14} aria-hidden="true" />
        {standing === 'invites_you' ? 'Accept' : 'Connect'}
      </button>
      {error && <span className="max-w-[12rem] text-end text-[0.7rem] text-red-200">{error}</span>}
    </span>
  )
}

/** Find somebody by name, and connect from the result. */
function PeopleSearch({ standingOf }: { standingOf: (userId: string) => Standing }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Array<{ user_id: string; handle: string; display_name: string; avatar_url?: string | null; city?: string | null }> | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) return
    const id = window.setTimeout(() => {
      setBusy(true)
      kaluta.people
        .search(q, 12)
        .then((page) => setResults(page.items))
        .catch(() => setResults([]))
        .finally(() => setBusy(false))
    }, 250)
    return () => window.clearTimeout(id)
  }, [query])

  return (
    <div>
      <label className="flex w-full items-center gap-3 rounded-full border border-[var(--cloud-border)] bg-[var(--cloud)] px-5 py-3 transition-colors focus-within:border-gold/60">
        <Search size={18} className="shrink-0 text-text-low" aria-hidden="true" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search people by name"
          aria-label="Search people"
          className="w-full bg-transparent text-[0.95rem] text-text-hi placeholder:text-text-low focus:outline-none"
        />
        {query && (
          <button type="button" onClick={() => setQuery('')} aria-label="Clear the search" className="text-text-low hover:text-text-hi">
            <X size={16} aria-hidden="true" />
          </button>
        )}
      </label>
      {results !== null && query.trim().length >= 2 && (
        <div className="cloud-card mt-3 p-2">
          {busy && results.length === 0 ? (
            <p className="px-3 py-3 text-sm text-text-low">Searching…</p>
          ) : results.length === 0 ? (
            <p className="px-3 py-3 text-sm text-text-mid">Nobody found.</p>
          ) : (
            <ul>
              {results.map((person) => (
                <li key={person.user_id} className="flex items-center gap-3 rounded-xl px-3 py-2 hover:bg-text-hi/[0.04]">
                  <MemberAvatar handle={person.handle} displayName={person.display_name} avatarUrl={person.avatar_url} size={40} />
                  <Link to={`/u/${person.handle}`} className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-text-hi hover:text-gold-soft">{person.display_name}</span>
                    {person.city && <span className="block truncate text-xs text-text-low">{person.city}</span>}
                  </Link>
                  <ConnectButton
                    key={`${person.user_id}-${standingOf(person.user_id)}`}
                    userId={person.user_id}
                    name={person.display_name}
                    standing={standingOf(person.user_id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

/** Suggestions: people near you or near the people you know. */
function PeopleYouMayKnow() {
  const suggestions = useApi(() => kaluta.suggestions.people(12), [])
  const items: PersonSuggestion[] = (suggestions.data?.items ?? []).filter((p) => p.connection !== 'connected')
  if (!suggestions.loading && items.length === 0) return null
  return (
    <section className="mt-10">
      <h2 className="mb-3 text-lg font-bold tracking-[-0.02em] text-text-hi">People you may know</h2>
      {suggestions.loading ? (
        <p className="text-sm text-text-low">Loading…</p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {items.map((person) => (
            <li key={person.user_id} className="cloud-card flex flex-col items-center p-5 text-center">
              <MemberAvatar handle={person.handle} displayName={person.display_name} avatarUrl={person.avatar_url} size={72} />
              <Link
                to={`/u/${person.handle}`}
                className="mt-3 block max-w-full truncate text-[1rem] font-bold text-text-hi hover:text-gold-soft"
              >
                {person.display_name}
              </Link>
              <span className="mt-0.5 block max-w-full truncate text-xs text-text-low">
                {[person.city, `${person.followers_count} follower${person.followers_count === 1 ? '' : 's'}`]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
              <span className="mt-4">
                <ConnectButton userId={person.user_id} name={person.display_name} standing={person.connection ?? 'none'} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
