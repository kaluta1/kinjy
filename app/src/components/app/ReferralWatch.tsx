import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta } from '@/lib/api'
import { pendingLink, pendingRef, refFromSearch, rememberInviter, rememberLink, rememberRef } from '@/lib/share'

/**
 * Keeps hold of the invitation, wherever it arrives and however far the
 * visitor wanders before signing up.
 *
 * An invite link credits the member who sent it through `?ref=`. Until now
 * that code was only ever read on one page - the shared-post page - so a link
 * to anywhere else lost it on arrival, and even a shared post lost it for
 * anyone already signed in, because a member lands on the feed rather than on
 * that page. An invitation to the home page, the pricing page, a profile, a
 * community: all of them credited nobody.
 *
 * This watches every navigation instead, so the code is caught once on
 * whichever page the link happened to point at, and then followed through the
 * rest of the visit:
 *
 *   kinjy.com/?ref=K7QMX2P9  ->  pricing  ->  a profile  ->  join
 *                                                            ^ still credited
 *
 * Three rules, which together are what "it must not be lost" means:
 *
 * - A page with no code **does not clear** one. Browsing is not declining an
 *   invitation, and every page after the first has no `?ref=` on it.
 * - Something that is not a code does not clear one either.
 * - A different, real code **replaces** it. Coming back on somebody else's
 *   link means somebody else invited you, and that is the link that brought
 *   you here.
 *
 * "Real" is settled by asking the server rather than by looking at the shape,
 * because the shape cannot settle it: a code is six to eight characters of
 * A-Z0-9, and so is the word "whatsapp". A tracking parameter picked up from
 * a mangled share would otherwise walk over the code of the member who
 * actually did the inviting. The lookup also brings back their name, which is
 * what lets the sign-up page say who invited you.
 *
 * It survives a reload and a closed tab because it is in localStorage, and it
 * is spent and cleared at sign-up, so it never follows one browser into a
 * second account.
 *
 * Renders nothing. Mounted once, inside the router.
 */
export default function ReferralWatch() {
  const { search, pathname, hash } = useLocation()
  const navigate = useNavigate()
  const { user, loading } = useAuth()

  // A short link (kinjy.com/s/<token>) lands here with ?via=<token>: the
  // invitation of whoever shared it, held as the token - the server turns it
  // back into that member at sign-up, so no code ever sits in the address.
  // Then the token is taken off the address bar: it has done its job, and a
  // link copied from there should not pass somebody else's invitation on.
  useEffect(() => {
    const params = new URLSearchParams(search)
    const token = params.get('via')
    if (!token) return
    params.delete('via')
    const rest = params.toString()
    const clean = `${pathname}${rest ? `?${rest}` : ''}${hash}`
    // Wait for the session: whether to hold the invitation depends on it.
    if (loading) return
    if (token === pendingLink() || user) {
      // A member's sponsor was settled when they joined: a link they follow
      // (their own included) holds nothing for this browser, which would
      // otherwise carry it into the next account created on it.
      navigate(clean, { replace: true })
      return
    }
    // Only the last step - tidying the address bar - is dropped when the
    // visitor has moved on. Holding the invitation is not: a deal or
    // community page sends a signed-out visitor straight to sign-in, and that
    // redirect used to land before the answer, which then credited nobody.
    let cancelled = false
    kaluta.auth
      .resolveShareLink(token)
      .then((link) => {
        // Only an invitation link is an invitation; a neutral one leaves
        // whatever is already held exactly as it was.
        if (!link.invitation) return
        rememberLink(link.token, link.destination, link.inviter?.display_name ?? null)
        // And held by the server for the attribution window, so the sign-up
        // form cannot drop it. Best-effort: the browser copy still works.
        void kaluta.auth.captureInvitation({ link: link.token }).catch(() => undefined)
      })
      .catch((err: unknown) => {
        // 404 is an answer: not one of ours. Anything else (offline, a 5xx)
        // is not, and the token is kept unverified, as a code would be - the
        // server checks it again at sign-up and drops it if it is not real.
        if (err instanceof ApiError && err.status === 404) return
        if (!pendingRef() && !pendingLink()) rememberLink(token, pathname, null)
      })
      .finally(() => {
        if (!cancelled) navigate(clean, { replace: true })
      })
    return () => {
      cancelled = true
    }
  }, [search, pathname, hash, navigate, user, loading])

  useEffect(() => {
    const candidate = refFromSearch(search)
    if (!candidate) return
    // Already the one we are holding: nothing to check and nothing to write.
    if (candidate === pendingRef()) return

    let cancelled = false
    void (async () => {
      try {
        // Checked and held by the server (an HttpOnly cookie for the
        // attribution window), which is what applies it at sign-up whatever
        // the form says. A signed-in member is not captured: their sponsor
        // was settled when they joined.
        const held = await kaluta.auth.captureInvitation({ code: candidate })
        if (cancelled || held.reason === 'signed_in') return
        // Under the "first link wins" policy an earlier invitation is kept and
        // this one is not; the browser copy follows what the server held.
        if (held.captured) rememberRef(candidate)
        if (held.inviter) rememberInviter(held.inviter.display_name)
      } catch (err) {
        if (cancelled) return
        // Two failures that look alike and are not, which is the distinction
        // the first version of this missed. On production it stored
        // "WHATSAPP" off a mangled share, because an explicit refusal and an
        // unanswered request went down the same branch.
        //
        // 404 is an answer: the server looked and this is not one of our
        // codes. Never keep it, whether or not we are holding something
        // else.
        if (err instanceof ApiError && err.status === 404) return
        // Anything else - offline, a timeout, a 5xx, the rate limit - is not
        // an answer. Keep what we already have if we have something, and
        // otherwise keep the candidate unverified rather than dropping it: a
        // visitor who followed a real link while the network was flapping
        // should still credit whoever sent it, and a code the server turns
        // out not to know costs nothing, because the sign-up drops it rather
        // than refusing the account.
        if (pendingRef()) return
        rememberRef(candidate)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [search])

  return null
}
