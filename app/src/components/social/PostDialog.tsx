import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import PostCard from './PostCard'
import type { Post } from '@/lib/api'

/**
 * One post, opened: the post itself and its comments in a panel over the feed.
 *
 * Opening a post used to mean leaving the feed, which costs the member their
 * scroll position and everything they had not read yet. A dialog keeps the
 * feed where it was.
 *
 * The card inside is the same PostCard as in the feed rather than a second
 * rendering of a post. Two of those would drift: media handling, the age
 * notice, reporting, reactions and the share panel all live in the card, and a
 * "detail view" that reimplements them is a second place for each to be wrong.
 */
export default function PostDialog({
  post,
  currentUserId,
  onClose,
  onHidden,
}: {
  post: Post
  currentUserId: string
  onClose: () => void
  onHidden: (postId: string) => void
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    // The feed behind must not scroll while this is open, or closing the
    // dialog returns the member to a different place than they left.
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Post and comments"
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4 backdrop-blur-sm sm:p-8"
      onClick={onClose}
    >
      <div
        // Stops a click inside the panel closing it. The backdrop closes;
        // the content does not.
        onClick={(event) => event.stopPropagation()}
        className="relative w-full max-w-2xl rounded-card-lg bg-ink-2 shadow-cloud"
      >
        {/* Sticky: a long post scrolls, and the way out must not scroll with
            it to the top of a page the reader has left behind. */}
        <div className="sticky top-0 z-10 flex justify-end rounded-t-card-lg bg-ink-2/85 px-3 pt-3 backdrop-blur">
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-full bg-ink/70 p-1.5 text-text-mid hover:text-text-hi"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="px-4 pb-4 sm:px-6 sm:pb-6">
          <PostCard
            post={post}
            algorithmId="chronological"
            mode="new"
            isOwn={post.author_id === currentUserId}
            currentUserId={currentUserId}
            onHidden={(id) => {
              onHidden(id)
              onClose()
            }}
            onChangeAlgorithm={() => {}}
            // Inside the dialog the comments are the point, so they are already
            // open and the card does not offer a second way to toggle them.
            commentsAlwaysOpen
          />
        </div>
      </div>
    </div>,
    document.body,
  )
}