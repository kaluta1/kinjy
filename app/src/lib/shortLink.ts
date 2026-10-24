/**
 * One short link per page and member: kinjy.com/s/<token>.
 *
 * Every place that hands out a link - the invitation card, a post's share
 * window, a community's Share link, a group conversation's invitation - asks
 * here, so the address shown, copied, put behind each network's button and
 * drawn as a QR code is the same string everywhere. The server keeps who
 * shared it; the address carries nothing about the member.
 *
 * Somebody signed out has nobody to credit, so they get the page's plain
 * address. A link that could not be made is reported as such: nothing is
 * copied or shown in its place, because a wrong link is worse than none.
 */
import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta } from '@/lib/api'
import { siteOrigin } from '@/lib/share'

// Per member and page, for this tab: the server answers the same link anyway,
// and asking again on every open of a share window is a round trip for nothing.
const cache = new Map<string, Promise<string>>()

/** The member's short link to a page (a path such as "/p/pst_..."). */
export function shortLinkFor(userId: string, destination: string): Promise<string> {
  const key = `${userId} ${destination}`
  let pending = cache.get(key)
  if (!pending) {
    // These links are the "credit me" ones: they carry the member's invitation.
    pending = kaluta.auth.shareLink(destination, true).then((link) => link.url)
    cache.set(key, pending)
    // A failure is not remembered: the next ask tries again.
    pending.catch(() => cache.delete(key))
  }
  return pending
}

export interface ShortLinkState {
  /** The address to show, copy, share and encode; null until it is known. */
  url: string | null
  loading: boolean
  error: string | null
  retry: () => void
}

/** The short link for a page, or null for no page yet. */
export function useShortLink(destination: string | null): ShortLinkState {
  const { user } = useAuth()
  const [state, setState] = useState<{ url: string | null; error: string | null; for: string | null }>({
    url: null,
    error: null,
    for: null,
  })
  const [attempt, setAttempt] = useState(0)
  const retry = useCallback(() => setAttempt((n) => n + 1), [])
  const key = destination ? `${user?.id ?? ''} ${destination} ${attempt}` : null

  useEffect(() => {
    if (!destination || !key || !user) return
    let cancelled = false
    shortLinkFor(user.id, destination)
      .then((url) => {
        if (!cancelled) setState({ url, error: null, for: key })
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setState({ url: null, error: err instanceof ApiError ? err.message : 'Could not create the link', for: key })
      })
    return () => {
      cancelled = true
    }
  }, [destination, key, user])

  // Nobody signed in, nobody to credit: the plain address is the right link.
  if (destination && !user) {
    return { url: new URL(destination, siteOrigin()).toString(), loading: false, error: null, retry }
  }
  const current = state.for === key
  return {
    url: current ? state.url : null,
    loading: Boolean(destination) && !current,
    error: current ? state.error : null,
    retry,
  }
}
