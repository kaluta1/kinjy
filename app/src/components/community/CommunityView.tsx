import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { ArrowLeft, BarChart3, Bell, BellOff, ClipboardCheck, Coins, Globe, History, Link2, Lock, LogOut, Pencil, Pin, PinOff, ScrollText, UsersRound } from 'lucide-react'
import Composer from '@/components/social/Composer'
import LinkQr from '@/components/ui-kit/LinkQr'
import PostCard from '@/components/social/PostCard'
import { CommunityMembersDialog, CommunitySettingsDialog } from '@/components/community/CommunityDialogs'
import { AuditDialog, PendingPostsDialog } from '@/components/community/CommunityModeration'
import { AnalyticsDialog, PaidAccessDialog } from '@/components/community/CommunityOwnerTools'
import CommunityEvents from '@/components/community/CommunityEvents'
import CommunityMarket from '@/components/community/CommunityMarket'
import CommunityMembership from '@/components/community/CommunityMembership'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta, type CommunityPostSpace, type Post } from '@/lib/api'
import { copyLink, pendingLink, siteOrigin } from '@/lib/share'
import { shortLinkFor } from '@/lib/shortLink'
import { announce } from '@/lib/live'
import { cn } from '@/lib/utils'

/** Set when a visitor starts joining, so the join resumes only for them. */
const JOIN_INTENT = 'kaluta.join_intent'

const KIND_LABEL: Record<string, string> = {
  public: 'Public — anyone can read and join',
  private: 'Private — a moderator approves who joins',
  secret: 'Secret — unlisted, opened only by a shared link',
  paid: 'Paid — access is bought',
}

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
  const navigate = useNavigate()
  // A secret community's shared link carries `?access=`. Without it the page
  // would be a 404 for the very person the link was sent to.
  const access = new URLSearchParams(location.search).get('access')
  const detail = useApi(() => kaluta.communities.get(handle, access), [handle, access])
  // The posts are fetched by id rather than by the slug in the URL, because
  // they are served by social-service, which knows ids and nothing about
  // community naming. So this waits for the community to resolve.
  const communityId = detail.data?.id ?? ''
  // Which space is shown (all of them by default) and in what order.
  const [space, setSpace] = useState<CommunityPostSpace | ''>('')
  // Events and the marketplace are sections of their own, not streams of posts.
  const [section, setSection] = useState<'posts' | 'events' | 'marketplace'>('posts')
  const [showPaid, setShowPaid] = useState(false)
  const [showAnalytics, setShowAnalytics] = useState(false)
  const [sort, setSort] = useState<'latest' | 'top'>('latest')
  const feed = useApi(
    () => (communityId ? kaluta.communities.feed(communityId, 20, 0, space || undefined, sort) : Promise.resolve(null)),
    [communityId, space, sort],
  )
  const [showQueue, setShowQueue] = useState(false)
  const [showAudit, setShowAudit] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [fresh, setFresh] = useState<Post[]>([])
  // The two windows: the owner's settings, and the members (with the
  // requests, for those who answer them).
  const [editing, setEditing] = useState(false)
  const [showMembers, setShowMembers] = useState(false)
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
  const pending = community?.pending_count ?? 0
  // Members see who else is in; anybody may, on a public community - unless
  // the owner switched the directory off (stewards still see it).
  const canSeeMembers = (isMember || community?.kind === 'public') && community?.directory_open !== false
  // The post spaces switched on, and those this reader may post in.
  const postSpaces = (community?.spaces ?? []).filter((s) => s.enabled && s.posters !== null && s.available)
  const myPostSpaces = postSpaces
    .filter((s) => community?.my_can_post?.[s.id as CommunityPostSpace])
    .map((s) => ({ id: s.id as CommunityPostSpace, label: s.label }))

  // The "someone asked to join" notification lands here with ?manage=requests.
  const wantsRequests = new URLSearchParams(location.search).get('manage') === 'requests'
  useEffect(() => {
    if (wantsRequests && isSteward) setShowMembers(true)
  }, [wantsRequests, isSteward])
  // "A post is waiting for approval" lands here with ?manage=posts.
  const wantsPosts = new URLSearchParams(location.search).get('manage') === 'posts'
  useEffect(() => {
    if (wantsPosts && isSteward) setShowQueue(true)
  }, [wantsPosts, isSteward])

  const refresh = () => {
    detail.reload()
    feed.reload()
    onChanged?.()
  }

  const join = async (via?: string | null) => {
    if (!community) return
    setBusy(true)
    setNote(null)
    try {
      // The invitation link they arrived on, if any: who invited them here.
      const result = await kaluta.communities.join(communityId, access, pendingLink() || via || null)
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

  // A visitor who chose "Join this community" comes back from signing up or in
  // with `?join=1`: carry on with the join they asked for. The server applies
  // every rule as it would for a click - approval, payment, a ban - so this
  // can open nothing a click could not. The flag is dropped from the address
  // first, so a reload or the back button never asks twice.
  const resume = new URLSearchParams(location.search)
  const resumeJoin = resume.get('join') === '1'
  // The invitation link held before signing up. Signing up spends the stored
  // one (it must not follow this browser into a second account), so it rides
  // along here for this one join.
  const resumeVia = resume.get('join_via')
  useEffect(() => {
    if (!resumeJoin || !user || !community) return
    const rest = new URLSearchParams(location.search)
    rest.delete('join')
    rest.delete('join_via')
    const query = rest.toString()
    navigate({ pathname: location.pathname, search: query ? `?${query}` : '', hash: location.hash }, { replace: true })
    // Only a join this tab started. A link that merely carries `?join=1`
    // must not make whoever opens it join, or ask to join, anything.
    let started = false
    try {
      started = sessionStorage.getItem(JOIN_INTENT) === community.id
      sessionStorage.removeItem(JOIN_INTENT)
    } catch {
      /* storage switched off: the button below is still there */
    }
    if (isMember || isPending) return
    if (started) void join(resumeVia)
    else setNote('You are signed in. Choose Join to become a member.')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeJoin, user, community?.id])

  const rememberJoinIntent = () => {
    try {
      if (community) sessionStorage.setItem(JOIN_INTENT, community.id)
    } catch {
      /* without storage the join is not resumed; the button stays */
    }
  }

  // Where a visitor goes to sign up or in: back here, with the join to resume.
  const joinReturn = (() => {
    const back = new URLSearchParams(location.search)
    back.delete('join_via')
    back.set('join', '1')
    const held = pendingLink()
    if (held) back.set('join_via', held)
    return encodeURIComponent(`${location.pathname}?${back.toString()}`)
  })()

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
   * link passes on the same link. A signed-in member gets their own short link
   * (kinjy.com/s/...), which credits them the way a shared post does - and the
   * QR code below is drawn from that same address.
   */
  const share = async () => {
    if (!community) return
    setShareNote('Creating your link…')
    setSharedUrl(null)
    try {
      let path = `/communities/${community.slug}`
      if (community.kind === 'secret') {
        if (isMember) path = (await kaluta.communities.shareLink(community.id)).path
        else if (access) path = `${path}?access=${encodeURIComponent(access)}`
      }
      const url = user ? await shortLinkFor(user.id, path) : new URL(path, siteOrigin()).toString()
      const result = await copyLink(url)
      setSharedUrl(url)
      setShareNote(result === 'copied' ? 'Link copied.' : `Copy this link: ${url}`)
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

  const toggleNotifications = async () => {
    if (!community) return
    const on = !community.my_notify_posts
    setBusy(true)
    setNote(null)
    try {
      await kaluta.communities.setPostNotifications(community.id, on)
      setNote(on ? 'You will be told about each new post here.' : 'No more notifications for new posts here.')
      detail.reload()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not change the notifications')
    } finally {
      setBusy(false)
    }
  }

  // Posts just written sit on top until the feed has them; once it does (a
  // reload after a pin or an approval), the feed's copy wins and is shown once.
  const loaded = feed.data?.items ?? []
  const posts = [...fresh.filter((p) => !loaded.some((l) => l.id === p.id)), ...loaded]

  return (
    <div className="space-y-5">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex min-h-10 items-center gap-1.5 text-[0.95rem] font-semibold text-text-mid hover:text-gold-soft"
      >
        <ArrowLeft size={14} aria-hidden="true" />
        All communities
      </button>

      {detail.loading && <p className="caption">Loading…</p>}
      {detail.error && <p className="text-sm text-red-300">{detail.error}</p>}

      {community && (
        <>
          <header className="cloud-card overflow-hidden">
            {community.cover_url ? (
              <img src={community.cover_url} alt="" className="h-36 w-full object-cover sm:h-48" />
            ) : (
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
            )}
            <div className="px-5 pb-7 sm:px-8">
            <div className="-mt-9 flex items-end gap-4 sm:-mt-11">
              {community.avatar_url ? (
                <img
                  src={community.avatar_url}
                  alt=""
                  className="h-[72px] w-[72px] shrink-0 rounded-[20px] border-4 border-[var(--cloud)] object-cover sm:h-[88px] sm:w-[88px]"
                />
              ) : (
                <span
                  aria-hidden="true"
                  className="flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-[20px] border-4 border-[var(--cloud)] bg-gold/25 text-gold-soft sm:h-[88px] sm:w-[88px]"
                >
                  <UsersRound size={30} />
                </span>
              )}
            </div>
            <div className="mt-4 min-w-0">
                <h1 className="text-[clamp(30px,4vw,44px)] font-bold leading-[1.1] tracking-[-0.03em] text-text-hi [overflow-wrap:anywhere]">{community.name}</h1>
                <p className="mt-2.5 text-[0.95rem] leading-relaxed text-text-mid">
                  {community.kind === 'public' ? (
                    <Globe size={14} className="me-1.5 inline-block align-[-2px]" aria-hidden="true" />
                  ) : (
                    <Lock size={14} className="me-1.5 inline-block align-[-2px]" aria-hidden="true" />
                  )}
                  {KIND_LABEL[community.kind] ?? community.kind}
                  {' · '}
                  {community.members_count} member{community.members_count === 1 ? '' : 's'}
                </p>
                {community.description && (
                  <p className="mt-4 max-w-[65ch] whitespace-pre-line text-base leading-[1.7] text-text-mid [overflow-wrap:anywhere] sm:text-[1.0625rem]">{community.description}</p>
                )}
            </div>

            <div className="mt-6 flex flex-wrap items-center gap-2.5">
              {!user ? (
                // Not signed in: an account first, then back here to join.
                // Somebody who has one switches to Sign in, here or on the form.
                <>
                  <Link
                    to={`/join?mode=signup&next=${joinReturn}`}
                    onClick={rememberJoinIntent}
                    className="inline-flex min-h-11 items-center rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-[0.95rem] font-bold text-ink"
                  >
                    Join this community
                  </Link>
                  <Link
                    to={`/join?mode=signin&next=${joinReturn}`}
                    onClick={rememberJoinIntent}
                    className="inline-flex min-h-11 items-center rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-[0.95rem] font-semibold text-text-hi hover:border-gold/50 hover:text-gold-soft"
                  >
                    Already a member? Sign in
                  </Link>
                </>
              ) : (
                !isMember &&
                !isPending && (
                  <button
                    type="button"
                    onClick={() => void join()}
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
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-[0.95rem] font-semibold text-text-mid hover:border-red-400/50 hover:text-red-200 disabled:opacity-40"
                >
                  <LogOut size={12} aria-hidden="true" />
                  Leave
                </button>
              )}
              {isOwner && (
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-[0.95rem] font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft"
                >
                  <Pencil size={13} aria-hidden="true" />
                  Edit community
                </button>
              )}
              {canSeeMembers && (
                <button
                  type="button"
                  onClick={() => setShowMembers(true)}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-[0.95rem] font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft"
                >
                  <UsersRound size={13} aria-hidden="true" />
                  Members
                  {isSteward && pending > 0 && (
                    <span className="ms-0.5 rounded-full bg-gold px-1.5 text-[0.7rem] font-bold text-ink" aria-label={`${pending} waiting`}>
                      {pending}
                    </span>
                  )}
                </button>
              )}
              {isMember && (
                <button
                  type="button"
                  onClick={togglePin}
                  disabled={busy}
                  aria-pressed={Boolean(community.my_pinned)}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-[0.95rem] font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft disabled:opacity-40"
                >
                  {community.my_pinned ? <PinOff size={13} aria-hidden="true" /> : <Pin size={13} aria-hidden="true" />}
                  {community.my_pinned ? 'Unpin' : 'Pin'}
                </button>
              )}
              {isMember && (
                <button
                  type="button"
                  onClick={toggleNotifications}
                  disabled={busy}
                  aria-pressed={Boolean(community.my_notify_posts)}
                  title={community.my_notify_posts ? 'Stop notifications for new posts' : 'Be notified of new posts'}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-[0.95rem] font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft disabled:opacity-40"
                >
                  {community.my_notify_posts ? <Bell size={13} aria-hidden="true" /> : <BellOff size={13} aria-hidden="true" />}
                  {community.my_notify_posts ? 'Notifications on' : 'Notifications off'}
                </button>
              )}
              <button
                type="button"
                onClick={share}
                className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-[0.95rem] font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft"
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

            {isSteward && pending > 0 && (
              <button
                type="button"
                onClick={() => setShowMembers(true)}
                className="mt-3 text-sm font-semibold text-gold-soft hover:underline"
              >
                {pending} {pending === 1 ? 'person is' : 'people are'} asking to join
              </button>
            )}
            </div>
          </header>

          {community.rules && (
            <details className="cloud-card p-4 sm:px-5">
              <summary className="cursor-pointer text-[0.95rem] font-semibold text-text-hi">
                <ScrollText size={13} className="me-1.5 inline" aria-hidden="true" />
                Community rules
              </summary>
              <p className="mt-2.5 max-w-[70ch] whitespace-pre-wrap text-[0.95rem] leading-relaxed text-text-mid">{community.rules}</p>
            </details>
          )}

          {user && community.access_model && community.access_model !== 'free' && (
            <CommunityMembership communityId={community.id} slug={community.slug} onChanged={refresh} />
          )}

          {isSteward && (
            <div className="flex flex-wrap gap-2">
              {isOwner && (
                <button type="button" onClick={() => setShowPaid(true)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft">
                  <Coins size={13} aria-hidden="true" /> Paid access
                </button>
              )}
              <button type="button" onClick={() => setShowAnalytics(true)}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft">
                <BarChart3 size={13} aria-hidden="true" /> Analytics
              </button>
              <button type="button" onClick={() => setShowQueue(true)}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft">
                <ClipboardCheck size={13} aria-hidden="true" /> Posts to approve
              </button>
              <button type="button" onClick={() => setShowAudit(true)}
                className="inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-gold-soft">
                <History size={13} aria-hidden="true" /> Activity log
              </button>
            </div>
          )}

          {section !== 'posts' ? null : isMember && myPostSpaces.length > 0 ? (
            <Composer
              community={{
                id: community.id,
                name: community.name,
                spaces: myPostSpaces,
                requiresApproval: !isSteward && community.post_approval === 'all',
              }}
              onPosted={(post) =>
                post.status === 'published' ? setFresh((current) => [post, ...current]) : feed.reload()
              }
            />
          ) : isMember ? (
            <p className="cloud-card p-4 text-sm text-text-mid">Posting is closed in this community for now.</p>
          ) : (
            <p className="cloud-card p-4 text-[0.95rem] leading-relaxed text-text-mid sm:px-5">
              {/* Said before anything is tapped. Somebody reading an open
                  community can see every post and answer none of them, and
                  being told that up front is kinder than a refusal after. */}
              {publishesPublicly
                ? user
                  ? isPending
                    ? 'You can read this community while your request is answered. Joining is what lets you post, like and comment.'
                    : 'This community is open — anyone can read it. Join to post, like and comment.'
                  : 'This community is open, so you can read it without an account. Join it to take part.'
                : !user
                  ? 'Join this community to post in it.'
                  : isPending
                    ? 'You can post here once your request is approved.'
                    : 'Join this community to post in it.'}
            </p>
          )}

          {(feed.data?.my_queued?.length ?? 0) > 0 && (
            <section aria-label="Your posts not yet published" className="cloud-card space-y-2 p-4">
              <p className="text-sm font-semibold text-text-hi">Your posts not yet published</p>
              {feed.data!.my_queued!.map((queued) => (
                <p key={queued.id} className="text-sm text-text-mid">
                  <span className="me-2 rounded-full bg-amber-500/15 px-2 py-0.5 text-xs font-semibold text-amber-300">
                    {queued.status === 'pending'
                      ? 'Waiting for approval'
                      : `Scheduled ${queued.publish_at ? new Date(queued.publish_at).toLocaleString() : ''}`}
                  </span>
                  <span className="line-clamp-1 inline">{queued.body.replace(/<[^>]+>/g, ' ').slice(0, 120)}</span>
                </p>
              ))}
            </section>
          )}

          {(postSpaces.length > 1 || feed.data) && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div role="tablist" aria-label="Spaces" className="flex flex-wrap gap-1.5">
                {[{ id: '', label: 'All' }, ...postSpaces].map((s) => (
                  <button
                    key={s.id || 'all'}
                    type="button"
                    role="tab"
                    aria-selected={section === 'posts' && space === s.id}
                    onClick={() => {
                      setFresh([])
                      setSection('posts')
                      setSpace(s.id as CommunityPostSpace | '')
                    }}
                    className={cn(
                      'rounded-full px-3.5 py-1.5 text-xs font-semibold',
                      section === 'posts' && space === s.id ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid hover:bg-text-hi/[0.1]',
                    )}
                  >
                    {s.label}
                  </button>
                ))}
                {(community.spaces ?? [])
                  .filter((s) => s.enabled && (s.id === 'events' || s.id === 'marketplace'))
                  .map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      role="tab"
                      aria-selected={section === s.id}
                      onClick={() => setSection(s.id as 'events' | 'marketplace')}
                      className={cn(
                        'rounded-full px-3.5 py-1.5 text-xs font-semibold',
                        section === s.id ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid hover:bg-text-hi/[0.1]',
                      )}
                    >
                      {s.label}
                    </button>
                  ))}
              </div>
              {section === 'posts' && (
              <label className="inline-flex items-center gap-1.5 text-xs text-text-low">
                Order
                <select
                  value={sort}
                  onChange={(e) => {
                    setFresh([])
                    setSort(e.target.value as 'latest' | 'top')
                  }}
                  className="rounded-full border border-[var(--cloud-border)] bg-transparent px-2.5 py-1 text-xs text-text-hi"
                >
                  <option value="latest">Latest</option>
                  <option value="top">Most engaged</option>
                </select>
              </label>
              )}
            </div>
          )}

          {/* A 404 here is the private-community answer, not a broken page: the
              posts exist and are not for this viewer yet. */}
          {section === 'events' ? (
            <CommunityEvents communityId={community.id} slug={community.slug} steward={isSteward} member={isMember} />
          ) : section === 'marketplace' ? (
            <CommunityMarket communityId={community.id} member={isMember} steward={isSteward} currentUserId={user?.id ?? null} />
          ) : feed.error ? (
            <p className="cloud-card p-4 text-sm text-text-mid">
              {isMember
                ? feed.error
                : !user
                  ? 'The posts in this community are for its members. Join it to read them.'
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
                  steward={isSteward}
                  onCommunityChanged={refresh}
                />
              ))}
            </div>
          )}
          {editing && isOwner && (
            <CommunitySettingsDialog
              community={{ ...community, public_content: publishesPublicly }}
              onClose={() => setEditing(false)}
              onChanged={refresh}
            />
          )}
          {showPaid && isOwner && (
            <PaidAccessDialog communityId={community.id} spaces={community.spaces ?? []} paidSpaces={community.paid_spaces ?? []} onClose={() => setShowPaid(false)} onChanged={refresh} />
          )}
          {showAnalytics && isSteward && <AnalyticsDialog communityId={community.id} onClose={() => setShowAnalytics(false)} />}
          {showQueue && isSteward && (
            <PendingPostsDialog communityId={community.id} onClose={() => setShowQueue(false)} onChanged={refresh} />
          )}
          {showAudit && isSteward && <AuditDialog communityId={community.id} onClose={() => setShowAudit(false)} />}
          {showMembers && canSeeMembers && (
            <CommunityMembersDialog
              communityId={community.id}
              canModerate={isSteward}
              pending={pending}
              onClose={() => setShowMembers(false)}
              onChanged={refresh}
            />
          )}
        </>
      )}
    </div>
  )
}
