import { useState } from 'react'
import { Link, useLocation } from 'react-router'
import { ArrowLeft, Globe, Link2, Lock, LogOut, Pin, PinOff, UsersRound } from 'lucide-react'
import Composer from '@/components/social/Composer'
import LinkQr from '@/components/ui-kit/LinkQr'
import PostCard from '@/components/social/PostCard'
import MemberQueue from '@/components/community/MemberQueue'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta, type Post } from '@/lib/api'
import { copyLink, siteOrigin } from '@/lib/share'
import { announce } from '@/lib/live'
import { cn } from '@/lib/utils'

const KIND_LABEL: Record<string, string> = {
  public: 'Public — anyone can read and join',
  private: 'Private — a moderator approves who joins',
  secret: 'Secret — unlisted, opened only by a shared link',
  paid: 'Paid — access is bought',
}

/** The choices a steward has. Paid is a price, not a privacy setting. */
const PRIVACY_CHOICES: Array<{ kind: 'public' | 'private' | 'secret'; label: string; hint: string }> = [
  { kind: 'public', label: 'Public', hint: 'Anyone can read it and join.' },
  { kind: 'private', label: 'Private', hint: 'Anyone can find it; a moderator approves who joins.' },
  { kind: 'secret', label: 'Secret', hint: 'Unlisted; only a shared link opens it, and joining still needs approval.' },
]

/**
 * One community, opened: what has been posted in it, and the box to add to it.
 *
 * This is what made a community a community rather than a row in a directory.
 * Before it, a post addressed to a community was accepted, hidden from every
 * feed and listed by nothing — written into a group and read by its author
 * alone.
 *
 * Who sees what follows the kind of community, and the page says which kind it
 * is rather than leaving a member to infer it from what is missing. A public
 * one can be read before joining, because a group nobody can look into cannot
 * be judged worth joining. A private or secret one answers 404 to an outsider,
 * so the page offers the join button instead of an error.
 */
export default function CommunityView({
  handle,
  onBack,
  onChanged,
}: {
  /** The slug from the URL, or an id. The API resolves either. */
  handle: string
  onBack: () => void
  /** The directory behind this shows member counts, so it reloads after a join or a leave. */
  onChanged?: () => void
}) {
  const { user } = useAuth()
  const location = useLocation()
  // A secret community's shared link carries `?access=`. Without it the page
  // would be a 404 for the very person the link was sent to.
  const access = new URLSearchParams(location.search).get('access')
  const detail = useApi(() => kaluta.communities.get(handle, access), [handle, access])
  // The posts are fetched by id rather than by the slug in the URL, because
  // they are served by social-service, which knows ids and nothing about
  // community naming. So this waits for the community to resolve.
  const communityId = detail.data?.id ?? ''
  const feed = useApi(
    () => (communityId ? kaluta.communities.feed(communityId) : Promise.resolve(null)),
    [communityId],
  )
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [fresh, setFresh] = useState<Post[]>([])
  const [managing, setManaging] = useState(false)
  const [shareNote, setShareNote] = useState<string | null>(null)
  // The address just copied, kept so it can also be drawn as a code.
  const [sharedUrl, setSharedUrl] = useState<string | null>(null)
  // Whether the fee is what stands between this member and another pin, so
  // the refusal can carry the way to pay it.
  const [pinNeedsKyc, setPinNeedsKyc] = useState(false)

  const community = detail.data
  const isMember = community?.my_status === 'active'
  // Whether this community hands its content to anybody, and whether this
  // reader may answer it. The server decides both; the page only draws them,
  // and `can_engage` is the server's own answer rather than a guess from the
  // membership fields.
  const publishesPublicly = Boolean(community?.public_content ?? feed.data?.community?.public_content)
  const canEngage = feed.data?.can_engage ?? isMember
  const isPending = community?.my_status === 'pending'
  const isOwner = community?.my_role === 'owner'
  const isSteward = isMember && (isOwner || community?.my_role === 'moderator')

  const join = async () => {
    if (!community) return
    setBusy(true)
    setNote(null)
    try {
      const result = await kaluta.communities.join(communityId, access)
      setNote(
        result.status === 'pending'
          ? 'Request sent — a moderator has to approve it.'
          : `You joined ${community.name}.`,
      )
      detail.reload()
      feed.reload()
      onChanged?.()
    } catch (err) {
      // A paid community answers 402. That is information, not a failure.
      setNote(err instanceof ApiError ? err.message : 'Could not join')
    } finally {
      setBusy(false)
    }
  }

  const leave = async () => {
    setBusy(true)
    setNote(null)
    try {
      await kaluta.communities.leave(communityId)
      setNote('You left this community.')
      setFresh([])
      detail.reload()
      feed.reload()
      onChanged?.()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not leave')
    } finally {
      setBusy(false)
    }
  }

  /**
   * Copy the group's address, whatever its privacy. A public or private group
   * opens from its plain address; a secret one needs the access code, which
   * the server hands to members only. Somebody who arrived on a shared secret
   * link passes on the same link. A signed-in member's referral code rides
   * along, as it does on a shared post.
   */
  const share = async () => {
    if (!community) return
    setShareNote(null)
    try {
      let path = `/communities/${community.slug}`
      if (community.kind === 'secret') {
        if (isMember) path = (await kaluta.communities.shareLink(community.id)).path
        else if (access) path = `${path}?access=${encodeURIComponent(access)}`
      }
      const url = new URL(path, siteOrigin())
      if (user?.referral_code) url.searchParams.set('ref', user.referral_code)
      const result = await copyLink(url.toString())
      setSharedUrl(url.toString())
      setShareNote(result === 'copied' ? 'Link copied.' : `Copy this link: ${url.toString()}`)
    } catch (err) {
      setShareNote(err instanceof ApiError ? err.message : 'Could not get the link')
    }
  }

  const togglePin = async () => {
    if (!community) return
    setBusy(true)
    setNote(null)
    setPinNeedsKyc(false)
    try {
      if (community.my_pinned) {
        await kaluta.communities.unpin(community.id)
        setNote('Unpinned from your sidebar.')
      } else {
        await kaluta.communities.pin(community.id)
        setNote('Pinned to your sidebar.')
      }
      announce('pins')
      detail.reload()
    } catch (err) {
      // 402: three pins are used and the KYC fee is not paid this year.
      if (err instanceof ApiError && err.status === 402) setPinNeedsKyc(true)
      setNote(err instanceof ApiError ? err.message : 'Could not change the pin')
    } finally {
      setBusy(false)
    }
  }

  const changePublicContent = async (next: boolean) => {
    if (!community) return
    setBusy(true)
    setNote(null)
    try {
      await kaluta.communities.setPublicContent(community.id, next)
      setNote(
        next
          ? 'Anyone can read this community now. Only members can post, like and comment.'
          : 'This community is for its members now. Links to its posts no longer open for anybody else.',
      )
      detail.reload()
      feed.reload()
      onChanged?.()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not change who can read this')
    } finally {
      setBusy(false)
    }
  }

  const changePrivacy = async (kind: 'public' | 'private' | 'secret') => {
    if (!community || kind === community.kind) return
    setBusy(true)
    setNote(null)
    try {
      const result = await kaluta.communities.setPrivacy(community.id, kind)
      setNote(
        kind === 'public'
          ? result.admitted
            ? `This group is public now. ${result.admitted} waiting request${result.admitted === 1 ? ' was' : 's were'} admitted.`
            : 'This group is public now.'
          : kind === 'private'
            ? 'This group is private now. Members stay; newcomers ask to join.'
            : 'This group is secret now. It is unlisted, and only a shared link opens it.',
      )
      detail.reload()
      feed.reload()
      onChanged?.()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not change the privacy')
    } finally {
      setBusy(false)
    }
  }

  const posts = [...fresh, ...(feed.data?.items ?? [])]

  return (
    <div className="space-y-5">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
      >
        <ArrowLeft size={13} aria-hidden="true" />
        All communities
      </button>

      {detail.loading && <p className="caption">Loading…</p>}
      {detail.error && <p className="text-sm text-red-300">{detail.error}</p>}

      {community && (
        <>
          <header className="cloud-card overflow-hidden">
            <div
              aria-hidden="true"
              className={cn(
                'h-28 sm:h-36',
                community.kind === 'public'
                  ? 'bg-gradient-to-r from-[#C9D6F2] via-[#E3ECF7] to-[#F6EBD3]'
                  : community.kind === 'private'
                    ? 'bg-gradient-to-r from-[#F2B8A2] via-[#F7E1D8] to-[#F0C878]'
                    : 'bg-gradient-to-r from-[#F0C878] via-[#F6EBD3] to-[#F2B8A2]',
              )}
            />
            <div className="px-5 pb-6 sm:px-7">
            <div className="-mt-9 flex items-end gap-4">
              <span
                aria-hidden="true"
                className="flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-[20px] border-4 border-[var(--cloud)] bg-gold/25 text-gold-soft"
              >
                <UsersRound size={30} />
              </span>
            </div>
            <div className="mt-3 min-w-0">
                <h1 className="text-[clamp(28px,3.4vw,38px)] font-bold leading-[1.05] tracking-[-0.035em] text-text-hi">{community.name}</h1>
                <p className="mt-2 inline-flex items-center gap-1.5 text-sm text-text-low">
                  {community.kind === 'public' ? (
                    <Globe size={13} aria-hidden="true" />
                  ) : (
                    <Lock size={13} aria-hidden="true" />
                  )}
                  {KIND_LABEL[community.kind] ?? community.kind}
                  {' · '}
                  {community.members_count} member{community.members_count === 1 ? '' : 's'}
                </p>
                {community.description && (
                  <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-mid">{community.description}</p>
                )}
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-2.5">
              {!user ? (
                // Not signed in. The link carries this page so they come back
                // to the community they were reading, not to a dashboard.
                <Link
                  to={`/join?mode=signin&next=${encodeURIComponent(location.pathname + location.search)}`}
                  className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink"
                >
                  Sign in to join
                </Link>
              ) : (
                !isMember &&
                !isPending && (
                  <button
                    type="button"
                    onClick={join}
                    disabled={busy}
                    className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40"
                  >
                    {community.kind === 'private' || community.kind === 'secret'
                      ? 'Request to join'
                      : community.kind === 'paid'
                        ? 'Buy access'
                        : 'Join'}
                  </button>
                )
              )}
              {isPending && (
                <span className="rounded-full bg-amber-500/10 px-5 py-2.5 text-sm font-semibold text-amber-200">
                  Waiting for a moderator
                </span>
              )}
              {isMember && !isOwner && (
                <button
                  type="button"
                  onClick={leave}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-mid hover:border-red-400/50 hover:text-red-200 disabled:opacity-40"
                >
                  <LogOut size={12} aria-hidden="true" />
                  Leave
                </button>
              )}
              {(isOwner || community.my_role === 'moderator') && (
                <button
                  type="button"
                  onClick={() => setManaging((m) => !m)}
                  className="rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-low hover:border-sky/50 hover:text-sky"
                >
                  {managing ? 'Close' : 'Manage members'}
                </button>
              )}
              {isMember && (
                <button
                  type="button"
                  onClick={togglePin}
                  disabled={busy}
                  aria-pressed={Boolean(community.my_pinned)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft disabled:opacity-40"
                >
                  {community.my_pinned ? <PinOff size={13} aria-hidden="true" /> : <Pin size={13} aria-hidden="true" />}
                  {community.my_pinned ? 'Unpin' : 'Pin'}
                </button>
              )}
              <button
                type="button"
                onClick={share}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft"
              >
                <Link2 size={13} aria-hidden="true" />
                Share link
              </button>
            </div>

            {shareNote && (
              <div className="mt-3 flex flex-wrap items-center gap-4">
                <p role="status" className="min-w-0 flex-1 break-all text-sm text-text-mid">
                  {shareNote}
                </p>
                {/* The address that was just copied, as a code for somebody
                    in the room. For a secret community it carries the access
                    code too, exactly as the link does. */}
                {sharedUrl && (
                  <LinkQr url={sharedUrl} label={`the ${community.name} community`} filename={`kinjy-${community.slug}`} size={104} />
                )}
              </div>
            )}

            {isSteward && community.kind !== 'paid' && (
              <fieldset className="mt-5">
                <legend className="text-sm font-semibold text-text-hi">Privacy</legend>
                <div role="radiogroup" className="mt-2 flex flex-wrap gap-2">
                  {PRIVACY_CHOICES.map((choice) => (
                    <button
                      key={choice.kind}
                      type="button"
                      role="radio"
                      aria-checked={community.kind === choice.kind}
                      disabled={busy}
                      onClick={() => changePrivacy(choice.kind)}
                      className={cn(
                        'inline-flex items-center gap-1.5 rounded-full border px-4 py-2 text-sm font-semibold disabled:opacity-40',
                        community.kind === choice.kind
                          ? 'border-gold/60 bg-gold/15 text-gold-soft'
                          : 'border-[var(--cloud-border)] text-text-mid hover:border-gold/40',
                      )}
                    >
                      {choice.kind === 'public' ? <Globe size={13} aria-hidden="true" /> : <Lock size={13} aria-hidden="true" />}
                      {choice.label}
                    </button>
                  ))}
                </div>
                <p className="mt-2 text-xs text-text-low">
                  {PRIVACY_CHOICES.find((c) => c.kind === community.kind)?.hint}
                </p>

                {/* Only on an open community: the other kinds are defined by
                    their content being for members, so there is nothing here
                    to choose and offering it would suggest otherwise. */}
                {community.kind === 'public' && (
                  <label className="mt-4 flex items-start gap-2.5">
                    <input
                      type="checkbox"
                      checked={publishesPublicly}
                      disabled={busy}
                      onChange={(event) => void changePublicContent(event.target.checked)}
                      className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--gold)]"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-text-hi">
                        Anyone can read this community
                      </span>
                      <span className="mt-0.5 block text-xs text-text-low">
                        Its posts open for people who have not joined, and a link to one
                        shows a preview when it is shared outside Kinjy. Joining is still
                        what lets somebody post, like or comment.
                      </span>
                    </span>
                  </label>
                )}
              </fieldset>
            )}

            {note && (
              <p role="status" className="mt-3 text-sm text-text-mid">
                {note}
                {pinNeedsKyc && (
                  <>
                    {' '}
                    <Link to="/dashboard?tab=verification" className="font-semibold text-gold-soft hover:underline">
                      Pay the KYC fee
                    </Link>
                  </>
                )}
              </p>
            )}

            {managing && (
              <div className="mt-4">
                <MemberQueue communityId={communityId} canModerate />
              </div>
            )}
            </div>
          </header>

          {isMember ? (
            <Composer
              community={{ id: community.id, name: community.name }}
              onPosted={(post) => setFresh((current) => [post, ...current])}
            />
          ) : (
            <p className="cloud-card p-4 text-sm text-text-mid">
              {/* Said before anything is tapped. Somebody reading an open
                  community can see every post and answer none of them, and
                  being told that up front is kinder than a refusal after. */}
              {publishesPublicly
                ? user
                  ? isPending
                    ? 'You can read this community while your request is answered. Joining is what lets you post, like and comment.'
                    : 'This community is open — anyone can read it. Join to post, like and comment.'
                  : 'This community is open, so you can read it without an account. Sign in and join to take part.'
                : !user
                  ? 'Sign in and join to post in this community.'
                  : isPending
                    ? 'You can post here once your request is approved.'
                    : 'Join this community to post in it.'}
            </p>
          )}

          {/* A 404 here is the private-community answer, not a broken page: the
              posts exist and are not for this viewer yet. */}
          {feed.error ? (
            <p className="cloud-card p-4 text-sm text-text-mid">
              {isMember
                ? feed.error
                : !user
                  ? 'The posts in this community are for its members. Sign in and join to read them.'
                  : 'The posts in this community are for its members. Join to read them.'}
            </p>
          ) : feed.loading ? (
            <p className="caption">Loading posts…</p>
          ) : posts.length === 0 ? (
            <p className="cloud-card p-4 text-sm text-text-mid">
              {isMember
                ? 'Nothing has been posted here yet. Be the first.'
                : 'Nothing has been posted here yet.'}
            </p>
          ) : (
            <div className={cn('space-y-4')}>
              {posts.map((post) => (
                <PostCard
                  key={post.id}
                  post={post}
                  algorithmId="chronological"
                  mode="new"
                  isOwn={post.author_id === user?.id}
                  currentUserId={user?.id ?? ''}
                  onHidden={(id) => setFresh((c) => c.filter((p) => p.id !== id))}
                  onChangeAlgorithm={() => {}}
                  canEngage={canEngage}
                  onRefused={setNote}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
