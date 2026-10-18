import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { Globe, Lock, Plus, Search, UsersRound } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import CommunityView from '@/components/community/CommunityView'
import MemberQueue from '@/components/community/MemberQueue'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta, type Community } from '@/lib/api'
import { cn } from '@/lib/utils'

const KIND_META: Record<string, { label: string; icon: typeof Globe; tone: string; tile: string }> = {
  public: { label: 'Public', icon: Globe, tone: 'text-text-mid', tile: 'bg-sky/15 text-sky' },
  private: { label: 'Private · approval needed', icon: Lock, tone: 'text-amber-200', tile: 'bg-coral/15 text-coral' },
  paid: { label: 'Paid', icon: Lock, tone: 'text-gold-soft', tile: 'bg-gold/20 text-gold-soft' },
}

const pageHeader = (
  <header className="mb-6">
    <p className="mono-data text-[0.72rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Communities</p>
    <h1 className="mt-2 text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
      Find your people
    </h1>
    <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
      Public, private, secret or paid spaces. Secret ones never appear in this list — they are reachable by direct link only.
    </p>
  </header>
)

export default function Communities() {
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const list = useApi(() => kaluta.communities.list({ q: search || undefined }), [search])

  const [name, setName] = useState('')
  const [kind, setKind] = useState('public')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [managing, setManaging] = useState<string | null>(null)
  // Which community is open comes from the URL, not from state: a group you
  // cannot link to is a group you cannot share, and the back button has to
  // return to the directory rather than leave the page.
  const { slug } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()

  const create = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    setError(null)
    try {
      await kaluta.communities.create({ name: name.trim(), kind })
      setName('')
      list.reload()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the community')
    } finally {
      setCreating(false)
    }
  }

  // The answer is shown on the card that was clicked. At the top of the page
  // it was out of sight, and with the button still reading "Join" the click
  // looked like it had done nothing.
  const [cardNote, setCardNote] = useState<{ id: string; text: string } | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const join = async (community: Community) => {
    setCardNote(null)
    setBusyId(community.id)
    try {
      const result = await kaluta.communities.join(community.id)
      setCardNote({
        id: community.id,
        text:
          result.status === 'pending'
            ? 'Request sent. A moderator has to approve it.'
            : `You joined ${community.name}.`,
      })
      list.reload()
    } catch (err) {
      // A paid community answers 402; that is information, not a failure.
      setCardNote({ id: community.id, text: err instanceof ApiError ? err.message : 'Could not join' })
    } finally {
      setBusyId(null)
    }
  }

  const leave = async (community: Community) => {
    setCardNote(null)
    setBusyId(community.id)
    try {
      await kaluta.communities.leave(community.id)
      setCardNote({ id: community.id, text: `You left ${community.name}.` })
      list.reload()
    } catch (err) {
      setCardNote({ id: community.id, text: err instanceof ApiError ? err.message : 'Could not leave' })
    } finally {
      setBusyId(null)
    }
  }

  if (slug) {
    const view = (
      <CommunityView
        handle={slug}
        onBack={() => navigate('/communities')}
        onChanged={() => list.reload()}
      />
    )
    // A visitor gets the page without the member shell. The shell would either
    // bounce them to sign in - which is what made "public" meaningless here -
    // or show them a sidebar of their circles and pinned modules, which they
    // do not have.
    if (!user) {
      return <section className="mx-auto w-full max-w-3xl px-6 py-12">{view}</section>
    }
    return (
      <AppShell>{view}</AppShell>
    )
  }

  return (
    <AppShell>
      {pageHeader}
      <div className="flex flex-col gap-4 lg:flex-row">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setSearch(query.trim())
          }}
          className="flex flex-1 gap-2"
        >
          <label className="flex w-full items-center gap-3 rounded-full border border-[var(--cloud-border)] bg-[var(--cloud)] px-5 py-3.5 shadow-[0_15px_35px_-22px_rgba(76,62,43,0.35)] transition-colors focus-within:border-gold/60">
            <Search size={18} className="shrink-0 text-text-low" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search communities…"
              aria-label="Search communities"
              className="w-full bg-transparent text-base text-text-hi placeholder:text-text-low focus:outline-none"
            />
          </label>
        </form>
      </div>

      <form onSubmit={create} className="cloud-card mt-4 p-5 md:p-6">
        <p className="mono-data mb-3 text-[0.7rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Start a community</p>
        <div className="flex flex-wrap gap-2">
          {(['public', 'private', 'paid'] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className={cn(
                'rounded-full px-4 py-2 text-sm font-semibold capitalize transition-colors',
                kind === k
                  ? 'bg-gold/20 text-gold-soft'
                  : 'text-text-mid hover:bg-text-hi/[0.07] hover:text-text-hi',
              )}
            >
              {k}
            </button>
          ))}
        </div>
        <div className="mt-3 flex gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Start a community — Kigoma Farmers"
            aria-label="Community name"
            className="w-full rounded-full border border-transparent bg-text-hi/[0.07] px-5 py-3 text-[0.95rem] text-text-hi placeholder:text-text-low focus:border-gold/50 focus:bg-transparent focus:outline-none"
          />
          <button
            type="submit"
            disabled={!name.trim() || creating}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-6 py-3 text-sm font-bold text-ink disabled:opacity-40"
          >
            <Plus size={14} aria-hidden="true" />
            Create
          </button>
        </div>
        {kind === 'paid' && (
          <p className="caption mt-2 text-amber-200">
            A paid community needs a price before it can be created.
          </p>
        )}
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-200">
            {error}
          </p>
        )}
      </form>


      <div className="mt-6 grid gap-5 md:grid-cols-2">
        {list.loading && <p className="text-sm text-text-low">Loading communities…</p>}
        {list.error && <p className="text-sm text-amber-200">{list.error}</p>}
        {list.data?.items.length === 0 && (
          <div className="cloud-card px-6 py-12 text-center md:col-span-2">
            <p className="text-base font-semibold text-text-hi">No community matches</p>
            <p className="mt-1 text-sm text-text-low">Start the first one with the form above.</p>
          </div>
        )}
        {(list.data?.items ?? []).map((community) => {
          const meta = KIND_META[community.kind] ?? KIND_META.public
          return (
            <article key={community.id} className="cloud-card flex flex-col p-5 md:p-6">
              <div className="flex items-start gap-3.5">
                <span
                  aria-hidden="true"
                  className={cn('flex h-12 w-12 shrink-0 items-center justify-center rounded-[14px]', meta.tile)}
                >
                  <UsersRound size={21} />
                </span>
                <div className="min-w-0">
                  <h2 className="truncate text-lg font-bold leading-tight tracking-[-0.02em] text-text-hi">
                    <Link to={`/communities/${community.slug}`} className="hover:text-gold-soft">
                      {community.name}
                    </Link>
                  </h2>
                  <p className={cn('mt-1 inline-flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs', meta.tone)}>
                    <meta.icon size={11} aria-hidden="true" />
                    {meta.label}
                    {community.price_usd && ` · $${community.price_usd}`}
                    {' · '}
                    {community.members_count} member{community.members_count === 1 ? '' : 's'}
                  </p>
                </div>
              </div>
              {community.description && (
                <p className="mt-3.5 line-clamp-2 text-[0.92rem] leading-relaxed text-text-mid">{community.description}</p>
              )}
              <div className="mt-auto flex flex-wrap gap-2 pt-5">
                <Link
                  to={`/communities/${community.slug}`}
                  className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink"
                >
                  Open
                </Link>
                {community.my_status === 'active' ? (
                  community.my_role === 'owner' ? (
                    <span className="inline-flex items-center rounded-full bg-success/10 px-4 py-2.5 text-sm font-semibold text-success">
                      Your community
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => leave(community)}
                      disabled={busyId === community.id}
                      title="Leave this community"
                      className="group rounded-full bg-success/10 px-5 py-2.5 text-sm font-semibold text-success hover:bg-red-400/10 hover:text-red-200 disabled:opacity-40"
                    >
                      <span className="group-hover:hidden">Joined</span>
                      <span className="hidden group-hover:inline">Leave</span>
                    </button>
                  )
                ) : community.my_status === 'pending' ? (
                  <span className="inline-flex items-center rounded-full bg-amber-400/10 px-4 py-2.5 text-sm font-semibold text-amber-200">
                    Request sent
                  </span>
                ) : community.my_status === 'banned' ? null : (
                  <button
                    type="button"
                    onClick={() => join(community)}
                    disabled={busyId === community.id}
                    className="rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft disabled:opacity-40"
                  >
                    {busyId === community.id
                      ? 'Joining…'
                      : community.kind === 'private' ? 'Request to join' : community.kind === 'paid' ? 'Buy access' : 'Join'}
                  </button>
                )}
                {/* Only those who can manage see Manage: for anybody else it
                    opened an empty desk. */}
                {(community.my_role === 'owner' || community.my_role === 'moderator') && (
                  <button
                    type="button"
                    onClick={() => setManaging(managing === community.id ? null : community.id)}
                    className="rounded-full px-4 py-2.5 text-sm font-semibold text-text-low hover:bg-text-hi/[0.07] hover:text-sky"
                  >
                    {managing === community.id ? 'Close' : 'Manage'}
                  </button>
                )}
              </div>
              {cardNote?.id === community.id && (
                <p role="status" className="mt-3 text-sm text-gold-soft">
                  {cardNote.text}
                </p>
              )}

              {/* The queue is only fetched when opened, and the panel hides
                  itself if the API says you are not a steward — a governance
                  desk that 403s on every action is worse than none. */}
              {managing === community.id && (
                <div className="mt-3">
                  <MemberQueue communityId={community.id} canModerate />
                </div>
              )}
            </article>
          )
        })}
      </div>
    </AppShell>
  )
}
