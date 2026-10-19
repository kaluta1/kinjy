import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Globe, Lock, Plus, Search, UsersRound } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import CommunityView from '@/components/community/CommunityView'
import { Sheet } from '@/components/community/CommunityDialogs'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta, type Community } from '@/lib/api'
import { cn } from '@/lib/utils'

const KIND_LABEL: Record<string, string> = {
  public: 'Public',
  private: 'Private',
  secret: 'Secret',
  paid: 'Paid',
}

/** The banner when a community has no cover yet: one colour family per kind. */
const KIND_BANNER: Record<string, string> = {
  public: 'bg-gradient-to-r from-[#C9D6F2] via-[#E3ECF7] to-[#F6EBD3]',
  private: 'bg-gradient-to-r from-[#F2B8A2] via-[#F7E1D8] to-[#F0C878]',
  secret: 'bg-gradient-to-r from-[#F0C878] via-[#F6EBD3] to-[#F2B8A2]',
  paid: 'bg-gradient-to-r from-[#E8C46A] via-[#F6EBD3] to-[#C9D6F2]',
}

/**
 * The communities: yours first, then the most followed - or, before you have
 * joined any, the most followed alone. A search replaces both with its
 * results. Each card shows the community's cover and photo; the whole card
 * opens it. Starting one is a button and a window, not a form on the page.
 */
export default function Communities() {
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const { slug } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()

  const mine = useApi(
    () => (user && !search ? kaluta.communities.list({ mine: true, limit: 50 }) : Promise.resolve(null)),
    [user?.id, search],
  )
  const popular = useApi(() => kaluta.communities.list({ q: search || undefined, limit: 30 }), [search])
  const reload = () => {
    mine.reload()
    popular.reload()
  }

  const [creating, setCreating] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [cardNote, setCardNote] = useState<{ id: string; text: string } | null>(null)

  const join = async (community: Community) => {
    setCardNote(null)
    setBusyId(community.id)
    try {
      const result = await kaluta.communities.join(community.id)
      setCardNote({
        id: community.id,
        text: result.status === 'pending' ? 'Request sent. The owner has to approve it.' : `You joined ${community.name}.`,
      })
      reload()
    } catch (err) {
      // A paid community answers 402; that is information, not a failure.
      setCardNote({ id: community.id, text: err instanceof ApiError ? err.message : 'Could not join' })
    } finally {
      setBusyId(null)
    }
  }

  if (slug) {
    const view = <CommunityView handle={slug} onBack={() => navigate('/communities')} onChanged={reload} />
    // A visitor gets the page without the member shell, which would bounce
    // them to sign in or show a sidebar they do not have.
    if (!user) return <section className="mx-auto w-full max-w-3xl px-6 py-12">{view}</section>
    return <AppShell>{view}</AppShell>
  }

  const myItems = mine.data?.items ?? []
  const myIds = new Set(myItems.map((c) => c.id))
  // Most followed first (the server's order); the ones already yours are above.
  const others = (popular.data?.items ?? []).filter((c) => search || !myIds.has(c.id))

  const card = (community: Community) => (
    <CommunityCard
      key={community.id}
      community={community}
      busy={busyId === community.id}
      note={cardNote?.id === community.id ? cardNote.text : null}
      onJoin={() => join(community)}
    />
  )

  return (
    <AppShell>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setSearch(query.trim())
          }}
          className="min-w-0 flex-1"
        >
          <label className="flex w-full items-center gap-3 rounded-full border border-[var(--cloud-border)] bg-[var(--cloud)] px-5 py-3 transition-colors focus-within:border-gold/60">
            <Search size={18} className="shrink-0 text-text-low" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                if (!e.target.value.trim()) setSearch('')
              }}
              placeholder="Search communities"
              aria-label="Search communities"
              className="w-full bg-transparent text-[0.95rem] text-text-hi placeholder:text-text-low focus:outline-none"
            />
          </label>
        </form>
        {user && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-3 text-sm font-bold text-ink"
          >
            <Plus size={16} aria-hidden="true" />
            New community
          </button>
        )}
      </div>

      {search ? (
        <Section title={`Results for “${search}”`} loading={popular.loading} error={popular.error}
                 empty="No community matches.">
          {others.map(card)}
        </Section>
      ) : (
        <>
          {myItems.length > 0 && (
            <Section title="Your communities" loading={false} error={null} empty="">
              {myItems.map(card)}
            </Section>
          )}
          <Section
            title={myItems.length > 0 ? 'Discover' : 'Most followed'}
            loading={popular.loading}
            error={popular.error}
            empty={myItems.length > 0 ? 'You are in every community there is.' : 'No community yet. Start the first one.'}
          >
            {others.map(card)}
          </Section>
        </>
      )}

      {creating && (
        <NewCommunity
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            setCreating(false)
            reload()
            navigate(`/communities/${created.slug}`)
          }}
        />
      )}
    </AppShell>
  )
}

function Section({
  title,
  loading,
  error,
  empty,
  children,
}: {
  title: string
  loading: boolean
  error: string | null
  empty: string
  children: React.ReactNode[]
}) {
  return (
    <section className="mt-7">
      <h2 className="mb-3 text-lg font-bold tracking-[-0.02em] text-text-hi">{title}</h2>
      {loading && <p className="text-sm text-text-low">Loading…</p>}
      {error && <p className="text-sm text-amber-200">{error}</p>}
      {!loading && !error && children.length === 0 && empty && (
        <p className="cloud-card px-5 py-8 text-center text-sm text-text-mid">{empty}</p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{children}</div>
    </section>
  )
}

function CommunityCard({
  community,
  busy,
  note,
  onJoin,
}: {
  community: Community
  busy: boolean
  note: string | null
  onJoin: () => void
}) {
  const status = community.my_status
  return (
    <article className="cloud-card group relative flex flex-col overflow-hidden transition-shadow hover:shadow-[0_18px_40px_-24px_rgba(76,62,43,0.45)]">
      <Link to={`/communities/${community.slug}`} className="absolute inset-0 z-0" aria-label={`Open ${community.name}`} />
      {community.cover_url ? (
        <img src={community.cover_url} alt="" className="h-24 w-full object-cover" />
      ) : (
        <div aria-hidden="true" className={cn('h-24 w-full', KIND_BANNER[community.kind] ?? KIND_BANNER.public)} />
      )}
      <div className="flex flex-1 flex-col px-4 pb-4">
        <div className="-mt-7 flex items-end justify-between gap-2">
          {community.avatar_url ? (
            <img
              src={community.avatar_url}
              alt=""
              className="h-14 w-14 rounded-2xl border-4 border-[var(--cloud)] object-cover"
            />
          ) : (
            <span className="grid h-14 w-14 place-items-center rounded-2xl border-4 border-[var(--cloud)] bg-gold/25 text-gold-soft">
              <UsersRound size={22} aria-hidden="true" />
            </span>
          )}
          {/* Above the card's link, so the button is the button. */}
          <div className="relative z-10 pb-1">
            {status === 'active' ? (
              <span className="rounded-full bg-success/10 px-3 py-1.5 text-xs font-semibold text-success">
                {community.my_role === 'owner' ? 'Yours' : 'Joined'}
              </span>
            ) : status === 'pending' ? (
              <span className="rounded-full bg-amber-400/10 px-3 py-1.5 text-xs font-semibold text-amber-200">
                Request sent
              </span>
            ) : status === 'banned' ? null : (
              <button
                type="button"
                onClick={onJoin}
                disabled={busy}
                className="rounded-full border border-[var(--cloud-border)] bg-[var(--cloud)] px-4 py-1.5 text-xs font-semibold text-text-hi hover:border-gold/60 hover:text-gold-soft disabled:opacity-40"
              >
                {busy ? 'Joining…' : community.kind === 'private' ? 'Ask to join' : community.kind === 'paid' ? 'Buy access' : 'Join'}
              </button>
            )}
          </div>
        </div>
        <h3 className="mt-2 truncate text-[1.05rem] font-bold tracking-[-0.015em] text-text-hi group-hover:text-gold-soft">
          {community.name}
        </h3>
        <p className="mt-0.5 inline-flex items-center gap-1.5 text-xs text-text-low">
          {community.kind === 'public' ? <Globe size={12} aria-hidden="true" /> : <Lock size={12} aria-hidden="true" />}
          {KIND_LABEL[community.kind] ?? community.kind} · {community.members_count} member
          {community.members_count === 1 ? '' : 's'}
        </p>
        {community.description && (
          <p className="mt-2 line-clamp-2 text-sm leading-relaxed text-text-mid">{community.description}</p>
        )}
        {note && (
          <p role="status" className="relative z-10 mt-2 text-xs text-gold-soft">
            {note}
          </p>
        )}
      </div>
    </article>
  )
}

function NewCommunity({ onClose, onCreated }: { onClose: () => void; onCreated: (c: { slug: string }) => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [kind, setKind] = useState<'public' | 'private'>('public')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <Sheet title="New community" onClose={onClose}>
      <form
        onSubmit={async (event) => {
          event.preventDefault()
          if (name.trim().length < 2) return
          setBusy(true)
          setError(null)
          try {
            const created = await kaluta.communities.create({ name: name.trim(), kind, description: description.trim() })
            onCreated(created)
          } catch (err) {
            setError(err instanceof ApiError ? err.message : 'Could not create the community')
          } finally {
            setBusy(false)
          }
        }}
        className="space-y-4"
      >
        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-text-mid">Name</span>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={140}
            placeholder="Kigoma Farmers"
            className="w-full rounded-[14px] border border-[var(--cloud-border)] bg-text-hi/[0.05] px-4 py-2.5 text-[0.95rem] text-text-hi focus:border-gold/60 focus:outline-none"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-sm font-semibold text-text-mid">Description</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            maxLength={4000}
            className="w-full resize-y rounded-[14px] border border-[var(--cloud-border)] bg-text-hi/[0.05] px-4 py-2.5 text-[0.95rem] text-text-hi focus:border-gold/60 focus:outline-none"
          />
        </label>
        <div role="radiogroup" aria-label="Who can join" className="grid gap-2 sm:grid-cols-2">
          {([
            ['public', 'Public', 'Anyone can join.', Globe],
            ['private', 'Private', 'You approve who joins.', Lock],
          ] as const).map(([value, label, hint, Icon]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={kind === value}
              onClick={() => setKind(value)}
              className={cn(
                'rounded-2xl border p-3 text-start',
                kind === value ? 'border-gold/60 bg-gold/10' : 'border-[var(--cloud-border)] hover:border-gold/40',
              )}
            >
              <span className="flex items-center gap-1.5 text-sm font-semibold text-text-hi">
                <Icon size={14} aria-hidden="true" /> {label}
              </span>
              <span className="mt-0.5 block text-xs text-text-low">{hint}</span>
            </button>
          ))}
        </div>
        <p className="text-xs text-text-low">You can add a cover and a photo once it is created.</p>
        {error && (
          <p role="alert" className="text-sm text-red-200">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || name.trim().length < 2}
          className="w-full rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-3 text-sm font-bold text-ink disabled:opacity-40"
        >
          {busy ? 'Creating…' : 'Create community'}
        </button>
      </form>
    </Sheet>
  )
}
