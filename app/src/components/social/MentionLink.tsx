import { Link } from 'react-router'
import { cn } from '@/lib/utils'

/**
 * A person named in a post, a comment or a message: their name, linked to
 * their profile.
 *
 * The name only, everywhere - "Alex Tugume", not "Alex Tugume @alextugume745",
 * which read as noise in a sentence. The username is still one hover away
 * (the link's title) and is what the picker shows while typing, where telling
 * two people with the same name apart actually matters. A handle the server
 * could not resolve to a name stays exactly as typed.
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
      title={`@${handle}`}
      onClick={stopPropagation ? (event) => event.stopPropagation() : undefined}
      className={cn('font-semibold hover:underline', className)}
    >
      {name || `@${handle}`}
    </Link>
  )
}
