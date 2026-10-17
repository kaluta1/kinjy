import { Link } from 'react-router'
import { cn } from '@/lib/utils'

/**
 * A person named in a post, a comment or a message: their name and their
 * username, linked to their profile.
 *
 * The name alone hid who it was - two members can share a name, and the
 * username is what tells them apart and what the author typed. The username
 * alone named nobody a reader recognises. So both: the name first, the
 * @username beside it, quieter. A handle the server could not resolve stays
 * exactly as typed.
 */
export default function MentionLink({
  handle,
  name,
  className,
  stopPropagation = false,
}: {
  /** Without the @, lower case. */
  handle: string
  name?: string | null
  className?: string
  /** Inside a clickable card, so the card does not open as well. */
  stopPropagation?: boolean
}) {
  return (
    <Link
      to={`/u/${handle}`}
      onClick={stopPropagation ? (event) => event.stopPropagation() : undefined}
      className={cn('font-semibold hover:underline', className)}
    >
      {name ? (
        <>
          {name} <span className="font-normal opacity-70">@{handle}</span>
        </>
      ) : (
        `@${handle}`
      )}
    </Link>
  )
}
