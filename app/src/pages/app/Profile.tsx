import { useCallback, useEffect, useState } from 'react'
import { Link,useNavigate, useParams } from 'react-router'
import { CalendarDays, Check, Clock, MapPin, MessageSquare, Pencil, UserPlus, Users } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { ProfileAsideSkeleton, ProfileSkeleton } from '@/components/app/ProfileSkeleton'
import MediaLightbox from '@/components/social/MediaLightbox'
import MemberAvatar from '@/components/social/MemberAvatar'
import PostCard from '@/components/social/PostCard'
import { VerifiedBadge } from '@/components/ui-kit'
import { useAppTheme } from '@/components/appdemo/theme'
import { useAuth } from '@/hooks/useAuth'
import { FEATURES } from '@/lib/features'
import { announce } from '@/lib/live'
import {
  ApiError,
  kaluta,
  type CommunityEvent,
  type Permissions,
  type Post,
  type Profile as ProfileData,
} from '@/lib/api'
import { countryName, languageName, splitLanguages } from '@/lib/profileOptions'
import { cn } from '@/lib/utils'

/** Why an action is unavailable, in the member's own terms. */
const BLOCKED: Record<string, string> = {
  blocked: 'You cannot interact with this member.',
  not_connected: 'Connect first — they only accept messages from accepted connections.',
}

export default function Profile() {
  const { handle = '' } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { tok, lang } = useAppTheme()

  // The picture being looked at full size, if any. A profile picture is
  // usually the only photograph of somebody on their page, and an 80px circle
  // is a thumbnail of it rather than the thing itself.
  const [zoom, setZoom] = useState<{ url: string; alt: string } | null>(null)
  const [profile, setProfile] = useState<ProfileData | null>(null)
  const [permissions, setPermissions] = useState<Permissions | null>(null)
  const [posts, setPosts] = useState<Post[]>([])
  // Upcoming events they host, in communities this reader may see.
  const [events, setEvents] = useState<CommunityEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [following, setFollowing] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const found = await kaluta.account.profileByHandle(handle)
      setProfile(found)

      // Permissions and posts are independent of each other: a failure in one
      // must not blank the page, so they settle separately.
      const [perm, page, hosted] = await Promise.allSettled([
        kaluta.connections.with(found.user_id),
        kaluta.posts.byAuthor(found.user_id),
        kaluta.communityPaid.hostedEvents(found.user_id),
      ])
      setEvents(hosted.status === 'fulfilled' ? hosted.value.items : [])
      if (perm.status === 'fulfilled') {
        setPermissions(perm.value)
        // From the server, not assumed: a profile reached from a notification
        // or from Connections used to start at "Follow" for somebody already
        // followed, and asked for the same click again and again.
        setFollowing(Boolean(perm.value.following))
      } else {
        setFollowing(false)
      }
      if (page.status === 'fulfilled') setPosts(page.value.items)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load this profile')
    } finally {
      setLoading(false)
    }
  }, [handle])

  useEffect(() => {
    void load()
  }, [load])

  const isSelf = profile?.user_id === user?.id

  /** Follow, or stop following: the same button, as everywhere else. */
  const toggleFollow = async () => {
    if (!profile) return
    setBusy(true)
    setNote(null)
    try {
      if (following) {
        await kaluta.social.unfollow(profile.user_id)
        setFollowing(false)
        setProfile((p) => (p ? { ...p, followers_count: Math.max(0, p.followers_count - 1) } : p))
      } else {
        const result = await kaluta.social.follow(profile.user_id)
        setFollowing(true)
        // Already followed means the count already included us.
        if (!result.already) {
          setProfile((p) => (p ? { ...p, followers_count: p.followers_count + 1 } : p))
        }
      }
      announce('profile')
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : following ? 'Could not unfollow' : 'Could not follow')
    } finally {
      setBusy(false)
    }
  }

  const connect = async () => {
    if (!profile) return
    setBusy(true)
    setNote(null)
    try {
      const result = await kaluta.connections.invite(profile.user_id)
      setNote(
        result.status === 'accepted'
          ? 'You are now connected — they had already invited you.'
          : 'Invitation sent.',
      )
      announce('connections')
      setPermissions((p) => (p ? { ...p, pending: true, invited_by_me: true, can_invite: false } : p))
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not send the invitation')
    } finally {
      setBusy(false)
    }
  }

  const message = async () => {
    if (!profile) return
    setBusy(true)
    setNote(null)
    try {
      // Straight to this thread, by id, rather than leaving the list to choose.
      const conversation = await kaluta.messages.start([profile.user_id])
      navigate(`/messages?c=${conversation.id}`)
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not open a conversation')
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <AppShell aside={<ProfileAsideSkeleton />}>
        <ProfileSkeleton />
      </AppShell>
    )
  }

  if (error || !profile) {
    return (
      <AppShell title="Profile">
        <p role="alert" className="rounded-xl bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          {error ?? 'This member does not exist.'}
        </p>
      </AppShell>
    )
  }

  const place = [profile.city, profile.state, profile.country && countryName(profile.country, lang)]
    .filter(Boolean)
    .join(', ')
  const spoken = splitLanguages(profile.languages)
    .map((code) => languageName(code, lang))
    .join(', ')
  const action = 'inline-flex items-center gap-2 rounded-full px-5 py-2.5 text-sm font-semibold transition-colors'
  const label = 'mono-data mb-3 text-[0.7rem] font-bold uppercase tracking-[0.15em] text-gold-soft'

  return (
    <AppShell
      aside={
        <>
          <section className={cn('rounded-[20px] p-5', tok.card)}>
            <p className={label}>About</p>
            {profile.bio ? (
              <p className={cn('whitespace-pre-wrap text-[0.9rem] leading-relaxed', tok.mid)}>{profile.bio}</p>
            ) : (
              <p className={cn('text-sm', tok.low)}>No bio yet.</p>
            )}
            <dl className="mt-4 space-y-3 border-t border-[var(--cloud-border)] pt-4 text-sm">
              {place && (
                <div className="flex justify-between gap-3">
                  <dt className={tok.low}>Location</dt>
                  <dd className={cn('text-end font-medium', tok.text)}>{place}</dd>
                </div>
              )}
              <div className="flex justify-between gap-3">
                <dt className={tok.low}>Languages</dt>
                <dd className={cn('text-end font-medium', tok.text)}>{spoken}</dd>
              </div>
            </dl>
          </section>

          {permissions && !isSelf && (
            <section className={cn('rounded-[20px] p-5', tok.card)}>
              <p className={label}>Your relationship</p>
              <ul className="space-y-3 text-sm">
                {[
                  {
                    on: permissions.connected,
                    text: permissions.connected ? 'Connected' : permissions.pending ? 'Invitation pending' : 'Not connected',
                  },
                  { on: permissions.can_message, text: permissions.can_message ? 'Can message' : 'Cannot message yet' },
                  ...(FEATURES.familyTreeApp
                    ? [{ on: permissions.can_add_family, text: permissions.can_add_family ? 'Can add to family tree' : 'Cannot add to family tree' }]
                    : []),
                ].map((row) => (
                  <li key={row.text} className={cn('flex items-center gap-2.5', row.on ? tok.text : tok.low)}>
                    <span
                      aria-hidden="true"
                      className={cn(
                        'grid h-5 w-5 shrink-0 place-items-center rounded-full',
                        row.on ? 'bg-emerald-500/15 text-emerald-300' : 'bg-text-hi/[0.07] text-text-low',
                      )}
                    >
                      {row.on ? <Check size={12} /> : <span className="h-1 w-1 rounded-full bg-current" />}
                    </span>
                    {row.text}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      }
    >
      {/* Header card */}
      <div className={cn('overflow-hidden rounded-[20px]', tok.card)}>
        {profile.cover_url ? (
          <button
            type="button"
            onClick={() => setZoom({ url: profile.cover_url as string, alt: `${profile.display_name}'s cover photo` })}
            className="block w-full cursor-zoom-in"
            aria-label="View cover photo"
          >
            <img src={profile.cover_url} alt="" className="h-40 w-full object-cover sm:h-52" />
          </button>
        ) : (
          <div className="h-32 bg-gradient-to-r from-[#F0C878] via-[#F2B8A2] to-[#C9CDF5] sm:h-44" aria-hidden="true" />
        )}
        <div className="px-5 pb-6 sm:px-7">
          <div className="-mt-14 mb-4 flex items-end justify-between gap-3">
            {/* Only a real photograph is worth opening. Without one the
                avatar is generated initials, and zooming those is a joke at
                the member's expense. */}
            {profile.avatar_url ? (
              <button
                type="button"
                onClick={() => setZoom({ url: profile.avatar_url as string, alt: `${profile.display_name}'s profile picture` })}
                className="cursor-zoom-in rounded-full"
                aria-label="View profile picture"
              >
                <MemberAvatar
                  displayName={profile.display_name}
                  avatarUrl={profile.avatar_url}
                  size={112}
                  ring
                />
              </button>
            ) : (
              <MemberAvatar
                displayName={profile.display_name}
                avatarUrl={profile.avatar_url}
                size={112}
                ring
              />
            )}
            {isSelf && (
              <Link
                to="/dashboard?tab=profile"
                className={cn(action, 'border border-[var(--cloud-border)]', tok.mid, 'hover:border-gold/50 hover:text-gold-soft')}
              >
                <Pencil size={14} aria-hidden="true" />
                Edit profile
              </Link>
            )}
          </div>

          <h1 className={cn('flex items-center gap-2 text-[clamp(28px,3.4vw,38px)] font-bold leading-[1.05] tracking-[-0.035em]', tok.text)}>
            {profile.display_name}
            {profile.verified && <VerifiedBadge size={22} />}
          </h1>
          {/* The handle used to sit here and again in the About card. A
              person is their name; the handle is an address, and the address
              is already in the URL of the page you are on. */}
          <p className={cn('mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.95rem]', tok.low)}>
            {place && (
              <span className="inline-flex items-center gap-1">
                <MapPin size={13} aria-hidden="true" />
                {place}
              </span>
            )}
          </p>

          <dl className="mt-5 flex items-center gap-8">
            <div>
              <dd className={cn('text-2xl font-bold leading-none tabular-nums tracking-[-0.02em]', tok.text)}>{profile.followers_count}</dd>
              <dt className={cn('mt-1.5 text-sm', tok.low)}>followers</dt>
            </div>
            <div>
              <dd className={cn('text-2xl font-bold leading-none tabular-nums tracking-[-0.02em]', tok.text)}>{profile.following_count}</dd>
              <dt className={cn('mt-1.5 text-sm', tok.low)}>following</dt>
            </div>
          </dl>

          {!isSelf && (
            <div className="mt-6 flex flex-wrap gap-2.5">
              <button
                type="button"
                onClick={toggleFollow}
                disabled={busy}
                aria-pressed={following}
                className={cn(
                  action,
                  following
                    ? cn('border border-[var(--cloud-border)]', tok.low)
                    : 'bg-gradient-to-br from-gold-soft to-gold text-ink shadow-[0_8px_20px_-10px_rgba(166,120,57,0.6)]',
                )}
              >
                <UserPlus size={15} aria-hidden="true" />
                {following ? 'Following' : permissions?.follows_you ? 'Follow back' : 'Follow'}
              </button>

              {permissions?.connected ? (
                <span className={cn(action, 'bg-emerald-500/10 text-emerald-300')}>
                  <Users size={15} aria-hidden="true" />
                  Connected
                </span>
              ) : permissions?.pending ? (
                <span className={cn(action, 'border border-[var(--cloud-border)]', tok.low)}>
                  <Clock size={15} aria-hidden="true" />
                  {permissions.invited_by_me ? 'Invitation sent' : 'They invited you'}
                </span>
              ) : (
                <button
                  type="button"
                  onClick={connect}
                  disabled={busy || !permissions?.can_invite}
                  title={
                    permissions?.can_invite
                      ? 'They decide whether to accept'
                      : 'This member is not accepting invitations'
                  }
                  className={cn(action, 'border border-sky/50 text-sky hover:bg-sky/10 disabled:opacity-40')}
                >
                  <Check size={15} aria-hidden="true" />
                  Connect
                </button>
              )}

              <button
                type="button"
                onClick={message}
                disabled={busy || !permissions?.can_message}
                title={permissions?.can_message ? 'Open a conversation' : BLOCKED[permissions?.reason ?? ''] ?? ''}
                className={cn(action, 'border border-[var(--cloud-border)]', tok.mid, 'hover:border-gold/50 hover:text-text-hi disabled:opacity-40')}
              >
                <MessageSquare size={15} aria-hidden="true" />
                Message
              </button>
            </div>
          )}

          {note && <p className="mt-4 text-sm font-medium text-gold-soft">{note}</p>}
          {!isSelf && permissions && !permissions.can_message && (
            <p className={cn('mt-3 text-sm', tok.low)}>
              {BLOCKED[permissions.reason] ?? 'Messaging is closed for this member.'}
            </p>
          )}
        </div>
      </div>

      {/* Events they host - only when there are some, and only ones this reader may open. */}
      {events.length > 0 && (
        <>
          <h2 className={cn('mb-4 mt-8 flex items-center gap-2.5 text-xl font-bold tracking-[-0.03em]', tok.text)}>
            Events
            <span className="mono-data rounded-full bg-text-hi/[0.07] px-2.5 py-0.5 text-xs font-semibold text-text-mid">
              {events.length}
            </span>
          </h2>
          <ul className="space-y-2">
            {events.map((e) => (
              <li key={e.id}>
                <Link to={`/events/${e.id}`} className={cn('block rounded-[20px] p-4 hover:ring-1 hover:ring-gold/40', tok.card)}>
                  <span className={cn('block font-semibold', tok.text)}>{e.title}</span>
                  <span className={cn('mt-1 flex items-center gap-1.5 text-sm', tok.low)}>
                    <CalendarDays size={13} aria-hidden="true" />
                    {new Date(e.starts_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                    {e.community && ` · ${e.community.name}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}

      {/* Their posts */}
      <h2 className={cn('mb-4 mt-8 flex items-center gap-2.5 text-xl font-bold tracking-[-0.03em]', tok.text)}>
        Posts
        {posts.length > 0 && (
          <span className="mono-data rounded-full bg-text-hi/[0.07] px-2.5 py-0.5 text-xs font-semibold text-text-mid">
            {posts.length}
          </span>
        )}
      </h2>
      {posts.length === 0 ? (
        <div className={cn('rounded-[20px] px-6 py-12 text-center', tok.card)}>
          <p className={cn('text-base font-semibold', tok.text)}>Nothing public here yet</p>
          <p className={cn('mt-1 text-sm', tok.low)}>Posts shared with everyone will appear here.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {posts.map((post) => (
            <PostCard
              key={post.id}
              post={post}
              algorithmId="chronological"
              isOwn={post.author_id === user?.id}
              currentUserId={user?.id ?? ''}
              onHidden={(id) => setPosts((current) => current.filter((p) => p.id !== id))}
              onChangeAlgorithm={() => undefined}
            />
          ))}
        </div>
      )}

      {/* The same viewer the feed uses: full size, arrow keys, escape to
          close. A second one would be a second set of those behaviours to
          keep in step. */}
      {zoom && (
        <MediaLightbox
          media={[{ url: zoom.url, kind: 'image', alt_text: zoom.alt }]}
          index={0}
          onClose={() => setZoom(null)}
        />
      )}
    </AppShell>
  )
}
