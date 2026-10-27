import type { ReactNode } from 'react'
import { Copy, MoreHorizontal, Pencil, Reply, Trash2 } from 'lucide-react'
import FloatingPanel from '@/components/social/FloatingPanel'
import { MESSAGE_REACTIONS } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Emoji reactions under a message: a small pill per emoji with how many people
 * chose it, and the viewer's own highlighted. Counts, not names - naming
 * everyone who reacted turns a quiet acknowledgement into a scoreboard - and a
 * tap on a pill is the reaction itself: it takes yours back, or adds it.
 *
 * Only the emoji the server allows (MESSAGE_REACTIONS) are ever sent.
 */

/** The pills under a bubble. */
export function ReactionChips({
  counts,
  mine,
  onToggle,
  ownMessage = false,
}: {
  counts: Record<string, number>
  mine: string | null | undefined
  onToggle: (emoji: string) => void
  /** Your own message, which has no avatar gutter beside it. */
  ownMessage?: boolean
}) {
  const entries = Object.entries(counts)
  if (!entries.length) return null
  return (
    // Lined up with the edge of the bubble they belong to. Somebody else's
    // message is indented past their avatar, so pills with a plain margin sat
    // under the avatar instead of under the message - the same 36px gutter the
    // sender's name above the bubble already allows for.
    <div className={cn('-mt-1.5 flex flex-wrap gap-1', ownMessage ? 'me-2' : 'ms-9')}>
      {entries.map(([emoji, count]) => {
        const chosen = mine === emoji
        return (
          <button
            key={emoji}
            type="button"
            onClick={() => onToggle(emoji)}
            aria-pressed={chosen}
            aria-label={`${emoji} ${count}${chosen ? ', your reaction' : ''}`}
            className={cn(
              'inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-xs text-text-hi',
              chosen ? 'border-gold/50 bg-gold/20' : 'border-white/12 bg-ink-2 hover:border-white/25',
            )}
          >
            <span aria-hidden="true">{emoji}</span>
            <span className="tabular-nums">{count}</span>
          </button>
        )
      })}
    </div>
  )
}

/**
 * The button beside a bubble that opens its menu. Shown on hover or focus.
 *
 * One button, not two. There used to be a smiley for reactions and a second
 * one for edit and delete, side by side on your own messages - and a smiley
 * that also offers Delete is a smiley that lies, so now it is the neutral
 * one that every messenger uses for "there is more here".
 *
 * Pointer devices only: on a phone the gesture is a long press, and a
 * permanently visible button beside every bubble is clutter in the one place
 * where the text matters most.
 */
export function MessageMenuButton({ onOpen, label }: { onOpen: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={() => onOpen()}
      aria-label={label}
      aria-haspopup="dialog"
      title="React, reply, copy…"
      className="hidden shrink-0 self-center rounded-full p-1.5 text-text-low opacity-0 hover:text-text-hi focus-visible:opacity-100 group-hover/bubble:opacity-100 [@media(pointer:fine)]:block"
    >
      <MoreHorizontal size={14} aria-hidden="true" />
    </button>
  )
}

/**
 * Everything you can do to a message, in one menu.
 *
 * Raised by a right-click on a desktop, a long press on a phone, or the small
 * button beside the bubble - three gestures, one menu, because there used to be
 * two: a row of emoji from a long press, and an edit-and-delete menu from a
 * button that only appeared on your own messages while the pointer was over
 * them. A member who right-clicked got the browser's own menu, which knows
 * nothing about messages.
 *
 * The emoji sit along the top, the way every messenger puts them, so the most
 * common thing to do is one tap rather than a tap and a read. Choosing the
 * emoji you already have takes it back.
 */
export function MessageMenu({
  anchor,
  current,
  canEdit,
  canDelete,
  onPick,
  onReply,
  onCopy,
  onEdit,
  onDelete,
  onClose,
  extra = [],
  deleteLabel = 'Delete',
}: {
  /** The id of the bubble this is about, so the menu sits over it. */
  anchor: string
  current: string | null | undefined
  /** Text the server can read: not a sticker, not an end-to-end encrypted message. */
  canEdit: boolean
  canDelete: boolean
  onPick: (emoji: string) => void
  onReply: () => void
  /** Absent when there is no text to copy. */
  onCopy?: () => void
  onEdit: () => void
  onDelete: () => void
  onClose: () => void
  /** More actions for this message (pin, save, report...), between Edit and Delete. */
  extra?: Array<{ label: string; icon: ReactNode; onClick: () => void; danger?: boolean }>
  /** "Remove" when a moderator takes down somebody else's message. */
  deleteLabel?: string
}) {
  const item = 'flex w-full items-center gap-2.5 rounded-card-sm px-2.5 py-2 text-sm hover:bg-white/10'
  const run = (action: () => void) => () => {
    action()
    onClose()
  }

  return (
    <FloatingPanel anchor={anchor} label="Message options" onClose={onClose}>
      <div role="group" aria-label="Reactions" className="flex gap-0.5">
        {MESSAGE_REACTIONS.map((emoji) => (
          <button
            key={emoji}
            type="button"
            onClick={run(() => onPick(emoji))}
            aria-label={`React ${emoji}`}
            aria-pressed={current === emoji}
            className={cn(
              'flex h-9 w-9 items-center justify-center rounded-full text-xl hover:scale-110 hover:bg-white/10 focus-visible:bg-white/10',
              current === emoji && 'bg-gold/20',
            )}
          >
            <span aria-hidden="true">{emoji}</span>
          </button>
        ))}
      </div>

      <div className="mt-1 border-t border-white/10 pt-1">
        <button type="button" onClick={run(onReply)} className={cn(item, 'text-text-hi')}>
          <Reply size={14} aria-hidden="true" />
          Reply
        </button>
        {onCopy && (
          <button type="button" onClick={run(onCopy)} className={cn(item, 'text-text-hi')}>
            <Copy size={14} aria-hidden="true" />
            Copy text
          </button>
        )}
        {canEdit && (
          <button type="button" onClick={run(onEdit)} className={cn(item, 'text-text-hi')}>
            <Pencil size={14} aria-hidden="true" />
            Edit
          </button>
        )}
        {extra.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={run(action.onClick)}
            className={cn(item, action.danger ? 'text-red-300' : 'text-text-hi')}
          >
            {action.icon}
            {action.label}
          </button>
        ))}
        {canDelete && (
          <button type="button" onClick={run(onDelete)} className={cn(item, 'text-red-300')}>
            <Trash2 size={14} aria-hidden="true" />
            {deleteLabel}
          </button>
        )}
      </div>
    </FloatingPanel>
  )
}
