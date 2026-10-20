import { useCallback, useEffect, useRef, useState } from 'react'
import { Navigate, Link, useNavigate, useSearchParams, useParams } from 'react-router'
import { PenLine, RefreshCw, Sparkles, TrendingUp } from 'lucide-react'
import { useAuth } from '@/hooks/useAuth'
import AppShell, { RailCard } from '@/components/app/AppShell'
import { useViewTracking } from '@/hooks/useViewTracking'
import Composer from '@/components/social/Composer'
import FeedModeMenu from '@/components/social/FeedModeMenu'
import Suggestions from '@/components/social/Suggestions'
import PostCard from '@/components/social/PostCard'
import PostDialog from '@/components/social/PostDialog'
import PostSkeleton, { PostSkeletonList } from '@/components/social/PostSkeleton'
import { ApiError, kaluta, membersOnlyCommunity, type Algorithm, type FeedMode, type FeedPage, type Post } from '@/lib/api'
import { FEATURES } from '@/lib/features'
import { slotAboveOrb } from '@/lib/floating'
import { useTopic } from '@/hooks/useRealtime'
import { cn } from '@/lib/utils'

const EMPTY_REASON: Record<string, string> = {
  not_following_anyone: 'You are not following anyone yet. Switch to New or For You to find people.',
}

/**
 * The member's Social Hub — the real feed, not the marketing demo at /feeds.
 *
 * Blueprint §1: the feed modes are explicit and separate. "Following" is
 * strictly reverse-chronological and the UI says so, because the promise only
 * means something if it is visible: a ranked feed labelled chronological would
 * be the exact conflict the blueprint set out to resolve.
 */
export default function SocialHub() {
  const { user, loading: authLoading } = useAuth()
  // The Universal Navigation links to /hub?mode=… , so the query string is the
  // source of truth for which feed mode is showing.
  const [params, setParams] = useSearchParams()
  const mode = params.get('mode') ?? 'new'

  // The place and topic filters live in the URL alongside the mode. They used to
  // be local state, so a hashtag link like ?mode=topics&topic=tag switched the
  // mode and then silently ignored the topic — the feed came back unfiltered and
  // clicking a tag looked like it did nothing.
  const topic = params.get('topic') ?? ''
  const city = params.get('city') ?? ''
  const country = params.get('country') ?? ''

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }
  const setMode = (next: string) => {
    // Switching mode drops a filter that belonged to the previous one, and
    // retires the "showing New instead" notice: the member has chosen.
    setFellBackFrom(null)
    const params = next === 'new' ? new URLSearchParams() : new URLSearchParams({ mode: next })
    setParams(params, { replace: true })
  }
  const [algorithmId, setAlgorithmId] = useState<string>('friends_first')
  // The member's saved defaults, applied once. Without this the two settings
  // in Feed & experience were stored, returned by the API, and then overruled
  // by a hardcoded 'new' / 'friends_first' every time the page opened.
  //
  // The feed waits for this to resolve. Applying the defaults *after* a first
  // load meant three feeds were built per page open — the hardcoded pair, then
  // the saved algorithm, then the saved mode — three rankings of 500 candidates
  // for one screen.
  const appliedDefaults = useRef(false)
  const [defaultsReady, setDefaultsReady] = useState(false)
  const [orbVisible, setOrbVisible] = useState<boolean>(FEATURES.assistant)
  // The modes and algorithms on offer, fetched with the preferences so a saved
  // choice can be checked against them before it is applied.
  const [catalog, setCatalog] = useState<{ modes: FeedMode[]; algorithms: Algorithm[] }>({
    modes: [],
    algorithms: [],
  })
  // Whether the page is still here when the defaults arrive: a late setParams
  // would otherwise rewrite the URL of whatever page the member moved on to.
  // A ref set by its own effect, not a cleanup flag in the loader: StrictMode
  // runs effects twice, and the loader below only ever starts once.
  const mounted = useRef(false)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  useEffect(() => {
    if (appliedDefaults.current) return
    appliedDefaults.current = true
    void Promise.allSettled([kaluta.account.preferences(), kaluta.feeds.modes(), kaluta.feeds.algorithms()])
      .then(([prefsResult, modesResult, algorithmsResult]) => {
        if (!mounted.current) return
        const modes = modesResult.status === 'fulfilled' ? modesResult.value.modes : []
        const algorithms = algorithmsResult.status === 'fulfilled' ? algorithmsResult.value.items : []
        setCatalog({ modes, algorithms })
        // A preferences outage must not leave the feed empty — it falls back
        // to the built-in defaults and loads anyway.
        if (prefsResult.status !== 'fulfilled') return
        const prefs = prefsResult.value

        // A saved choice is applied only if it still exists. A removed
        // algorithm or mode used to fail every page open with an error until
        // the member found the setting and changed it.
        const algorithm = prefs.algorithm_id
        if (typeof algorithm === 'string' && algorithms.some((a) => a.id === algorithm)) {
          setAlgorithmId(algorithm)
        }
        // The floating composer button stacks above the orb, so it needs to
        // know whether the orb is there at all.
        setOrbVisible(FEATURES.assistant && prefs.assistant_visible !== false)
        // Only when the URL says nothing: a shared link or a hashtag click is
        // an explicit request and must win over the default.
        const savedMode = prefs.default_feed_mode
        if (
          !params.get('mode') &&
          typeof savedMode === 'string' &&
          savedMode !== 'new' &&
          modes.some((m) => m.id === savedMode)
        ) {
          setParams(new URLSearchParams({ mode: savedMode }), { replace: true })
        }
      })
      .finally(() => {
        if (mounted.current) setDefaultsReady(true)
      })
    // Runs once; params/setParams are read at call time, not tracked.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const [feed, setFeed] = useState<FeedPage | null>(null)
  // The post opened over the feed, and the paging state for scroll-to-load.
  const [openPost, setOpenPost] = useState<Post | null>(null)

  // A shared link lands here, not on a page of its own.
  //
  // /p/{id} used to render the post alone, which is where a shared link
  // normally dead-ends: you read the one post and there is nothing around it,
  // so the thing a share is for - arriving somewhere alive - does not happen.
  // The route now mounts the feed and opens that post over it, which is what
  // every other network does with a shared link.
  //
  // The URL is unchanged, because the unfurlers key on it and the Caddy
  // matcher rewrites /p/* for them.
  const { postId: routePostId } = useParams()
  // ?post= is what the notifications have always linked to, and nothing read
  // it - so every "someone replied to you" landed on the feed with the post it
  // was about nowhere in sight. Both spellings open the same dialog, because
  // notifications already sent carry the query form and will for as long as
  // anybody's bell has unread items in it.
  const sharedPostId = routePostId ?? params.get('post') ?? undefined
  const navigate = useNavigate()
  const [loadingMore, setLoadingMore] = useState(false)
  const observer = useRef<IntersectionObserver | null>(null)
  // Cards report themselves as seen from here rather than each card firing its
  // own request — one observer, one batched call.
  const feedRef = useRef<HTMLDivElement>(null)
  useViewTracking(feedRef, Boolean(feed))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Set when Following turned out to be empty and New was shown instead, so
  // the switch is explained to the member rather than silent.
  const [fellBackFrom, setFellBackFrom] = useState<string | null>(null)
  // A mode the member asked for after being shown the fallback. Without this,
  // "Show Following anyway" is undone by the very fallback that offered it: the
  // feed comes back empty, falls back again, and the button does nothing.
  const insistedOn = useRef<string | null>(null)
  // New posts are announced, not injected. Splicing a stranger's post into the
  // list while somebody is reading moves the text under their eyes; a banner
  // lets them choose the moment.
  const [pending, setPending] = useState(0)
  useTopic('feed', (event) => {
    if (event.type !== 'post') return
    if (event.author_id === user?.id) return
    setPending((n) => n + 1)
  })

  // Past the first screenful the composer card has served its purpose, so it
  // gives the width back to the posts and becomes a floating button instead.
  const [scrolled, setScrolled] = useState(false)
  const [openComposer, setOpenComposer] = useState(0)
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 220)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // Arriving from "Change my algorithm": bring the picker into view and focus
  // it, so the link lands on the control rather than merely on the page.
  useEffect(() => {
    if (params.get('focus') !== 'algorithm') return
    const picker = document.getElementById('algorithm-picker') as HTMLSelectElement | null
    if (!picker) return
    picker.scrollIntoView({ block: 'center', behavior: 'auto' })
    picker.focus()
    const next = new URLSearchParams(params)
    next.delete('focus')
    setParams(next, { replace: true })
  }, [params, setParams])

  // Only the most recent request may fill the feed. Switching mode or
  // algorithm while a feed is loading used to let the older answer arrive last
  // and overwrite the screen with the wrong feed, often an empty one.
  const inFlight = useRef<AbortController | null>(null)
  const load = useCallback(async () => {
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    setLoading(true)
    setError(null)
    try {
      const page = await kaluta.feeds.page(
        {
          mode,
          algorithm_id: algorithmId,
          city: mode === 'local' ? city || undefined : undefined,
          country: mode === 'country' ? country || undefined : undefined,
          topic: mode === 'topics' ? topic || undefined : undefined,
        },
        { signal: controller.signal },
      )
      if (controller.signal.aborted) return
      // A Following feed with nobody followed is empty by construction, and a
      // member who opens the app to a blank screen reads it as their posts
      // having gone, not as a mode behaving exactly as specified. Changing the
      // stored default fixes new accounts; this fixes the ones already carrying
      // "following", whose stored preference cannot be told apart from a
      // deliberate choice - so rather than overwrite it, the feed falls back
      // for this visit and says why.
      if (
        page.items.length === 0 &&
        page.empty_reason === 'not_following_anyone' &&
        mode !== 'new' &&
        insistedOn.current !== mode
      ) {
        setFellBackFrom(mode)
        setParams(new URLSearchParams(), { replace: true })
        return
      }
      // Deliberately not cleared here: the fallback's own successful load is
      // what arrives next, and clearing on success wiped the explanation before
      // it could be read. It is cleared when the member picks a mode instead.
      setFeed(page)
    } catch (err) {
      if (controller.signal.aborted) return
      setError(err instanceof ApiError ? err.message : 'Could not load the feed')
    } finally {
      if (inFlight.current === controller) setLoading(false)
    }
  }, [mode, algorithmId, city, country, topic])

  useEffect(() => {
    if (user && defaultsReady) void load()
  }, [user, defaultsReady, load])
  useEffect(() => () => inFlight.current?.abort(), [])

  // Fetch the shared post and open it. Only when arriving on /p/{id}: the
  // feed itself sets openPost directly from the card that was clicked, and
  // already has the post in hand.
  useEffect(() => {
    if (!sharedPostId) return
    let cancelled = false
    kaluta.posts
      .get(sharedPostId)
      .then((post) => {
        if (!cancelled) setOpenPost(post)
      })
      // A post that is gone, or not for this viewer, simply leaves the feed
      // showing - which is a better landing than an error page. A post in a
      // community they have not joined takes them to that community, where
      // joining (or asking to) is one button.
      .catch((err: unknown) => {
        const community = membersOnlyCommunity(err)
        if (!cancelled && community) navigate(`/communities/${community.slug}`, { replace: true })
      })
    return () => {
      cancelled = true
    }
  }, [sharedPostId, navigate])

  // These three sit above the early returns below, and must stay there.
  // A hook after a conditional return runs on some renders and not others,
  // which is React error #310 and an unmounted tree - /hub went black on
  // refresh for exactly this reason.
  /**
   * The next page, appended.
   *
   * Guarded on `loadingMore` as well as `has_more` because the sentinel can
   * cross the viewport several times in one flick, and without the guard a
   * fast scroll fires four identical requests and shows each page twice.
   *
   * Posts are deduplicated on the way in: a ranked feed re-scores a moving
   * candidate pool, so the same post can legitimately appear in two pages, and
   * React would then warn about duplicate keys and render it twice.
   */
  const loadMore = useCallback(async () => {
    if (!feed || loadingMore || !feed.has_more) return
    setLoadingMore(true)
    try {
      const page = await kaluta.feeds.page({
        mode,
        algorithm_id: algorithmId,
        city: mode === 'local' ? city || undefined : undefined,
        country: mode === 'country' ? country || undefined : undefined,
        topic: mode === 'topics' ? topic || undefined : undefined,
        offset: feed.items.length,
      })
      setFeed((current) => {
        if (!current) return current
        const seen = new Set(current.items.map((item) => item.id))
        const fresh = page.items.filter((item) => !seen.has(item.id))
        return { ...current, items: [...current.items, ...fresh], has_more: page.has_more }
      })
    } catch (err) {
      // A failed page is not a failed feed: what is already on screen stays,
      // and the next scroll tries again. Logged rather than swallowed - a
      // silent catch here is how a broken "load more" looks exactly like a
      // feed that has ended.
      console.warn('could not load the next page of the feed', err)
    } finally {
      setLoadingMore(false)
    }
  }, [feed, loadingMore, mode, algorithmId, city, country, topic])

  /**
   * Watch the end of the feed.
   *
   * A callback ref rather than an effect over a ref: the sentinel is rendered
   * conditionally, so the moment it exists is not the moment any dependency
   * list changes. Keyed on `loadMore` so the observer always closes over the
   * current page offset.
   *
   * rootMargin starts the fetch before the member reaches the end, so the next
   * page is usually already there when they get to it.
   */
  const watchEnd = useCallback(
    (node: HTMLDivElement | null) => {
      observer.current?.disconnect()
      if (!node) return
      observer.current = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) void loadMore()
        },
        { rootMargin: '600px' },
      )
      observer.current.observe(node)
    },
    [loadMore],
  )

  useEffect(() => () => observer.current?.disconnect(), [])

  if (authLoading) {
    return (
      <div className="flex min-h-[60svh] items-center justify-center" role="status" aria-label="Loading">
        <div className="h-12 w-12 rounded-full animate-orb-breathe" style={{ background: 'var(--grad-orb)' }} />
      </div>
    )
  }
  if (!user) return <Navigate to="/join?mode=signin" replace />

  const prepend = (post: Post) => setFeed((f) => (f ? { ...f, items: [post, ...f.items] } : f))
  const drop = (postId: string) =>
    setFeed((f) => (f ? { ...f, items: f.items.filter((p) => p.id !== postId) } : f))

  // The feed's context, in the shell's own rail rather than a second grid the
  // page invents for itself.
  // Trending, derived from the posts actually on screen rather than invented.
  // The demo shows three fixed hashtags; a real one has to count something, and
  // the honest thing to count here is what this feed is carrying.
  const trending = Object.entries(
    (feed?.items ?? []).flatMap((post) => post.topics).reduce<Record<string, number>>((acc, topic) => {
      acc[topic] = (acc[topic] ?? 0) + 1
      return acc
    }, {}),
  )
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)

  const rail = (
    <>
      {/* Docked "why am I seeing this", as /app presents it: the explanation of
          the *feed* lives here, and each card keeps its own Why? button for the
          explanation of that post. */}
      <div className="rounded-[20px] cloud-glass p-5">
        <p className="mono-data text-[0.72rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Why am I seeing this?</p>
        <p className="mt-3 text-[0.85rem] leading-relaxed text-text-mid">
          Your feed is currently ranked by{' '}
          <span className="font-bold text-text-hi">
            {feed?.algorithm_name ?? feed?.algorithm ?? 'chronological order'}
          </span>
          .
        </p>
        <ul className="mt-2.5 flex flex-wrap gap-1.5">
          {(feed?.ranked
            ? [`Ranked · ${feed?.total_candidates ?? 0} posts scored`, 'Your algorithm applies']
            : ['Pure chronological', 'No ranking applied']
          ).map((reason) => (
            <li
              key={reason}
              className="rounded-md bg-sky/10 px-2.5 py-1.5 text-xs font-semibold text-sky"
            >
              {reason}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs leading-relaxed text-text-low">
          Every post carries its own Why? button.
        </p>
      </div>

      {trending.length > 0 && (
        <div className="rounded-[20px] cloud-glass p-5">
          <p className="mb-3 flex items-center gap-1.5 text-[0.95rem] font-bold tracking-[-0.01em] text-text-hi">
            <TrendingUp size={14} className="text-coral" aria-hidden="true" />
            Trending in this feed
          </p>
          <ul className="space-y-2">
            {trending.map(([topic, count], index) => (
              <li key={topic} className="flex items-baseline gap-2">
                <span className="mono-data text-xs text-gold">{index + 1}</span>
                <Link
                  to={`/hub?mode=topics&topic=${encodeURIComponent(topic)}`}
                  className="min-w-0 flex-1 truncate text-xs font-semibold text-text-mid hover:text-gold-soft"
                >
                  #{topic}
                </Link>
                <span className="mono-data text-[0.65rem] text-text-low">{count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <Suggestions />
      <RailCard title="Your algorithm">
        <p className="caption mb-3">Its filter applies in every mode; its ranking, in the ranked ones.</p>
        <select
          id="algorithm-picker"
          value={algorithmId}
          onChange={(e) => setAlgorithmId(e.target.value)}
          className="w-full rounded-card-sm border border-white/10 bg-ink-2/70 px-3 py-2 text-sm text-text-hi focus:border-gold/40 focus:outline-none"
          aria-label="Ranking algorithm"
        >
          {catalog.algorithms.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
              {a.builtin ? '' : ' · community'}
            </option>
          ))}
        </select>
        {feed?.ranked === false && (
          <p className="caption mt-2 text-text-low">
            {feed.filtered
              ? `This mode keeps date order, and still shows only what ${feed.filtered.name} allows.`
              : 'This mode keeps date order, so it does not rank.'}
          </p>
        )}
        <Link to="/feeds" className="caption mt-3 block text-gold-soft hover:underline">
          Browse the Algorithm Marketplace →
        </Link>
      </RailCard>

      <RailCard title="This feed">
        <dl className="space-y-2 text-xs">
          {[
            ['Mode', feed?.mode ?? '—'],
            ['Algorithm', feed?.algorithm_name ?? feed?.algorithm ?? '—'],
            ['Ranked', feed?.ranked ? 'yes' : 'no'],
            ['Candidates scored', feed?.total_candidates ?? '—'],
            ['Shown', feed?.items.length ?? 0],
          ].map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-text-low">{k}</dt>
              <dd className="mono-data text-text-mid">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </RailCard>
    </>
  )

  return (
    <AppShell aside={rail}>
      <div className="min-w-0">
        <h1 className="sr-only">
          {catalog.modes.find((m) => m.id === mode)?.label ?? 'Feed'}
        </h1>

        <div className="mb-4 flex items-center justify-between gap-4">
          <FeedModeMenu modes={catalog.modes} active={mode} onSelect={setMode} />
          <button
            type="button"
            onClick={() => void load()}
            aria-label="Refresh the feed"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-text-mid transition-colors hover:bg-text-hi/5"
          >
            <RefreshCw size={17} className={cn(loading && 'animate-spin')} aria-hidden="true" />
          </button>
        </div>

          {/* The place or topic the geographic/topic modes filter on */}
          {(mode === 'local' || mode === 'country' || mode === 'topics') && (
            <div className="mt-3">
              <input
                value={mode === 'local' ? city : mode === 'country' ? country : topic}
                onChange={(e) => {
                  const value = e.target.value
                  if (mode === 'local') setParam('city', value)
                  else if (mode === 'country') setParam('country', value.toUpperCase().slice(0, 2))
                  else setParam('topic', value.replace(/^#/, '').toLowerCase())
                }}
                placeholder={
                  mode === 'local'
                    ? 'Which city? — Kigoma'
                    : mode === 'country'
                      ? 'Country code — TZ'
                      : 'Which topic? — agriculture'
                }
                /* Distinct from the composer's own city field: two controls
                   sharing one accessible name is ambiguous for screen readers
                   and made an automated check fill the wrong box. */
                aria-label={
                  mode === 'local'
                    ? 'Filter the feed by city'
                    : mode === 'country'
                      ? 'Filter the feed by country code'
                      : 'Filter the feed by topic'
                }
                className="w-full max-w-xs rounded-full border border-white/10 bg-ink-2/60 px-4 py-2 text-xs text-text-hi placeholder:text-text-low focus:border-gold/40 focus:outline-none"
              />
            </div>
          )}

          <Composer onPosted={prepend} collapsed={scrolled} openSignal={openComposer} />

          {pending > 0 && (
            <button
              type="button"
              onClick={() => {
                setPending(0)
                void load()
                window.scrollTo({ top: 0, behavior: 'auto' })
              }}
              className="mx-auto mt-4 flex items-center gap-2 rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink shadow-cloud hover:brightness-110"
            >
              <Sparkles size={14} aria-hidden="true" />
              {pending === 1 ? '1 new post' : `${pending} new posts`}
            </button>
          )}

          {/* Feed */}
          <div className="mt-4 space-y-4" ref={feedRef}>
            {/* The shape of the feed rather than the word "loading": the
                cards are already the right size, so nothing shifts under a
                thumb when the posts land. */}
            {loading && <PostSkeletonList count={3} />}

            {!loading && error && (
              <div
                role="alert"
                className="rounded-card-sm border border-amber-400/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-200"
              >
                {error}
              </div>
            )}

            {/* The age lookup failed, so the feed was filtered as for a minor.
                Said out loud: a thinner feed with no reason given looks like
                the app losing posts. */}
            {!loading && !error && feed?.degraded && (
              <div className="flex items-center justify-between gap-3 rounded-card-sm border border-sky/25 bg-sky/10 px-4 py-3 text-sm text-sky">
                <span role="status">
                  Some posts may be missing for a moment. Refresh in a little while to see everything.
                </span>
                <button
                  type="button"
                  onClick={() => void load()}
                  className="shrink-0 rounded-full border border-sky/40 px-3 py-1 text-xs font-semibold hover:bg-sky/10"
                >
                  Refresh
                </button>
              </div>
            )}

            {fellBackFrom && !loading && (
              <p className="cloud-card mb-3 p-3 text-sm text-text-mid">
                You are not following anyone yet, so this is the New feed.{' '}
                <button
                  type="button"
                  onClick={() => {
                    // Clearing the marker first, so asking for Following again
                    // is not immediately undone by the fallback that brought
                    // this notice up.
                    insistedOn.current = fellBackFrom
                    setFellBackFrom(null)
                    setMode(fellBackFrom)
                  }}
                  className="underline hover:text-text-hi"
                >
                  Show Following anyway
                </button>
              </p>
            )}

            {!loading && !error && feed?.items.length === 0 && (
              <div className="cloud-card p-10 text-center">
                <span className="mx-auto grid h-12 w-12 place-items-center rounded-[14px] bg-gold/15 text-gold-soft">
                  <Sparkles size={22} aria-hidden="true" />
                </span>
                <p className="mx-auto mt-4 max-w-sm text-[15px] leading-relaxed text-text-mid">
                  {EMPTY_REASON[feed.empty_reason ?? ''] ?? 'Nothing here yet — publish the first post.'}
                </p>
              </div>
            )}

            {!loading &&
              feed?.items.map((post) => (
                <PostCard
                  key={post.id}
                  post={post}
                  algorithmId={feed.algorithm}
                  mode={feed.mode}
                  isOwn={post.author_id === user.id}
                  currentUserId={user.id}
                  onHidden={drop}
                  onOpen={() => setOpenPost(post)}
                  onChangeAlgorithm={() =>
                    document.getElementById('algorithm-picker')?.focus({ preventScroll: false })
                  }
                />
              ))}

            {/* What the observer watches. Rendered only while there is more,
                so reaching the real end stops the requests rather than leaving
                a sentinel sitting at the bottom firing forever. */}
            {!loading && feed?.has_more && (
              <div ref={watchEnd} className="py-6 text-center" aria-hidden="true">
                {loadingMore && (
                  /* One card's worth, for the same reason: the next post
                     arrives where the skeleton already is. */
                  <span className="block w-full" role="status" aria-label="Loading more posts">
                    <PostSkeleton index={1} />
                  </span>
                )}
              </div>
            )}

            {!loading && feed && !feed.has_more && feed.items.length > 0 && (
              <p className="py-6 text-center text-xs text-text-low">You are all caught up.</p>
            )}
          </div>
      </div>

      {openPost && (
        <PostDialog
          post={openPost}
          currentUserId={user.id}
          onClose={() => setOpenPost(null)}
          onHidden={drop}
        />
      )}

      {/* Compose stays one tap away once the card has scrolled off. */}
      {scrolled && (
        <button
          type="button"
          onClick={() => setOpenComposer((n) => n + 1)}
          aria-label="Create a post"
          className={cn(
            'fixed z-40 inline-flex items-center gap-2 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-3 text-sm font-bold text-ink shadow-cloud-hover',
            // Stacked above the assistant orb rather than beside it: both used
            // to anchor to the same corner with slightly different offsets,
            // which read as deliberate and overlapped by 48×44 pixels.
            slotAboveOrb(orbVisible),
          )}
        >
          <PenLine size={16} aria-hidden="true" />
          <span className="hidden sm:inline">Post</span>
        </button>
      )}
    </AppShell>
  )
}
