import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { ArrowRight } from 'lucide-react'
import PostCard from '@/components/social/PostCard'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { kaluta, membersOnlyCommunity, type Post } from '@/lib/api'
import { pendingRef, refFromSearch } from '@/lib/share'
import PublicShell from '@/components/landing/PublicShell'
import { Eyebrow } from '@/components/landing/PageKit'
import { KL_BTN_GHOST, KL_BTN_GOLD } from '@/components/landing/kl-classes'

/**
 * One post, on its own page, for somebody who may have no account.
 *
 * This is where a link shared outside Kinjy lands. It is the whole first
 * impression: if it demands a sign-in before showing anything, the share was
 * pointless, because nobody forwards a login wall.
 *
 * What a visitor may see is decided by the server, not here. A post that is
 * followers-only, in a circle or in a community answers 404 to someone who is
 * not in that audience, and the age gate runs on this route exactly as it does
 * in a feed — a direct link is the obvious way around a rating, so it is not
 * one. An unsigned visitor counts as UNKNOWN, which the policy engine treats
 * as a minor.
 */
export default function PostPage() {
  const { postId = '' } = useParams()
  const [params] = useSearchParams()
  const { user } = useAuth()
  // A community post opened by somebody outside the group: the server names
  // the community, so the page can send them there to join instead of
  // calling the link broken.
  const [community, setCommunity] = useState<{ slug: string; name: string } | null>(null)
  const post = useApi<Post>(
    () =>
      kaluta.posts.get(postId).catch((err: unknown) => {
        setCommunity(membersOnlyCommunity(err))
        throw err
      }),
    [postId],
  )

  // Remembering the invitation is ReferralWatch's job now, for every page
  // rather than only this one - a second copy here is how the two drift
  // apart. What is still this page's job is carrying the code on the Join
  // button, so the form is right even in a browser that stores nothing.
  const ref = refFromSearch(params.toString()) || pendingRef() || null

  const joinHref = ref ? `/join?mode=signup&ref=${encodeURIComponent(ref)}` : '/join?mode=signup'

  return (
    <PublicShell>
      <section className="mx-auto w-full max-w-2xl px-5 pb-16 pt-10 sm:pt-14">
        {post.loading && <p className="text-sm text-[var(--kl-low)]" role="status">Loading…</p>}

        {post.error && community && (
          <div className="rounded-[20px] border border-[var(--kl-paper-2)] bg-[var(--kl-surface)] p-8 shadow-[0_24px_48px_-34px_var(--kl-shadow)]">
            <h1 className="text-[clamp(28px,4vw,40px)] font-bold leading-[1.05] tracking-[-0.04em]">
              This post is in {community.name}
            </h1>
            <p className="mt-4 text-[1rem] leading-relaxed text-[var(--kl-mid)]">
              Only its members can read it. Join the community to see this post and everything else shared there.
            </p>
            <div className="mt-6 flex flex-wrap gap-3">
              {user ? (
                <Link to={`/communities/${community.slug}`} className={`${KL_BTN_GOLD} !py-3 !text-[15px]`}>
                  See the community
                  <ArrowRight size={15} aria-hidden="true" />
                </Link>
              ) : (
                <>
                  <Link to={joinHref} className={`${KL_BTN_GOLD} !py-3 !text-[15px]`}>
                    Create your account
                    <ArrowRight size={15} aria-hidden="true" />
                  </Link>
                  <Link to={`/join?mode=signin&next=${encodeURIComponent(`/communities/${community.slug}`)}`} className={`${KL_BTN_GHOST} !py-3 !text-[15px]`}>
                    Sign in
                  </Link>
                </>
              )}
            </div>
          </div>
        )}

        {post.error && !community && (
          <div className="rounded-[20px] border border-[var(--kl-paper-2)] bg-[var(--kl-surface)] p-8 shadow-[0_24px_48px_-34px_var(--kl-shadow)]">
            <Eyebrow>Post</Eyebrow>
            <h1 className="mt-4 text-[clamp(28px,4vw,40px)] font-bold leading-[1.05] tracking-[-0.04em]">
              This post is not available
            </h1>
            <p className="mt-4 text-[1rem] leading-relaxed text-[var(--kl-mid)]">
              {/* The server says 404 for "does not exist", "not for you" and
                  "not for your age" alike, on purpose — telling them apart would
                  say which links are worth passing on. So the page cannot be
                  more specific than this without guessing. */}
              It may have been deleted, or it may not be public. If somebody sent you this link, ask
              them to share it again.
            </p>
            <Link to="/" className={`${KL_BTN_GHOST} mt-6 !py-3 !text-[15px]`}>
              Go to Kinjy
            </Link>
          </div>
        )}

        {post.data && (
          <>
            <PostCard
              post={post.data}
              algorithmId="chronological"
              mode="new"
              isOwn={post.data.author_id === user?.id}
              currentUserId={user?.id ?? ''}
              onHidden={() => post.reload()}
              onChangeAlgorithm={() => {}}
            />

            {!user && (
              <div className="relative mt-6 overflow-hidden rounded-[20px] p-7 sm:p-8" style={{ background: 'linear-gradient(160deg, var(--kl-stage-a), var(--kl-stage-b))' }}>
                <div aria-hidden="true" className="kl-sheen absolute -right-12 -top-16 h-[200px] w-[200px] rounded-full opacity-35 blur-[70px]" />
                <h2 className="relative text-[1.4rem] font-bold leading-tight tracking-[-0.03em]">This is one post on Kinjy</h2>
                <p className="relative mt-3 max-w-md text-[0.95rem] leading-relaxed text-[var(--kl-mid)]">
                  Thirteen modules, one account: feeds, communities, messages, a family tree and
                  memorials.
                  {ref && ' You were invited by a member.'}
                </p>
                <Link to={joinHref} className={`${KL_BTN_GOLD} relative mt-6 !py-3 !text-[15px]`}>
                  Create your account
                  <ArrowRight size={15} aria-hidden="true" />
                </Link>
                <p className="relative mt-4 text-sm text-[var(--kl-mid)]">
                  Already a member?{' '}
                  <Link to="/join?mode=signin" className="font-semibold text-[var(--kl-gold-deep)] hover:underline">
                    Sign in
                  </Link>
                </p>
              </div>
            )}
          </>
        )}
      </section>
    </PublicShell>
  )
}
