import { useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { Camera, File as FileIcon, Mic, Reply, Video, X } from 'lucide-react'
import type { Message } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Replying to a message, the way WhatsApp does it: a quote block with a coloured
 * edge above the text of the bubble, the same block above the composer while
 * the reply is being written, a reply arrow beside the bubble on desktop and a
 * swipe on a phone.
 *
 * Nothing here asks the server for the quoted text. The quote is built from the
 * message the thread already holds (decrypted, if it ever is), so an
 * end-to-end conversation never has a plaintext copy made for it.
 */

const MEDIA_LABELS: Record<string, string> = {
  image: 'Photo',
  video: 'Video',
  audio: 'Audio',
  document: 'Document',
  file: 'File',
}

/** A short, plain line for the quoted message. Never anything the member cannot already see. */
function quoteText(message: Message): string {
  if (message.kind === 'sticker') return 'Sticker'
  if (message.encrypted) return 'Encrypted message'
  const text = (message.body ?? '').replace(/\s+/g, ' ').trim()
  if (text) return text.length > 140 ? `${text.slice(0, 140)}…` : text
  if (message.media_kind) return MEDIA_LABELS[message.media_kind] ?? 'File'
  return 'Message'
}

function MediaGlyph({ kind }: { kind?: string | null }) {
  const props = { size: 12, 'aria-hidden': true, className: 'shrink-0' } as const
  if (kind === 'image') return <Camera {...props} />
  if (kind === 'video') return <Video {...props} />
  if (kind === 'audio') return <Mic {...props} />
  return kind ? <FileIcon {...props} /> : null
}

/** Whoever a quote can point at: a loaded message, one not on this page, or one that is gone. */
export type QuoteTarget =
  | { state: 'found'; name: string; message: Message }
  | { state: 'older' }
  | { state: 'deleted' }

const EDGE = 'border-s-[3px] border-gold'

/** The quote inside a bubble. Clicking it jumps to the original when it is on screen. */
export function ReplyQuote({
  target,
  mine,
  onJump,
}: {
  target: QuoteTarget
  mine: boolean
  onJump?: () => void
}) {
  // On my own bubble everything is drawn on Messenger blue, so the quote is
  // white at reduced opacity: gold on blue is both poor contrast and the wrong
  // palette, and the grey used on their bubble disappears entirely. A white
  // wash also reads as "inside this bubble", where a black one read as a hole
  // punched through it.
  const surface = mine ? 'bg-white/20' : 'bg-black/20'
  const nameTone = mine ? 'text-white' : 'text-gold-soft'
  const bodyTone = mine ? 'text-white/80' : 'text-text-mid'
  const hover = mine ? 'hover:bg-white/30' : 'hover:bg-black/25'
  if (target.state !== 'found') {
    return (
      <div className={cn('mb-1 rounded-card-sm px-2.5 py-1.5 text-xs italic', bodyTone, EDGE, surface)}>
        {target.state === 'deleted' ? 'Message deleted' : 'Older message'}
      </div>
    )
  }
  const { message, name } = target
  return (
    <button
      type="button"
      onClick={onJump}
      aria-label={`Go to the message from ${name}: ${quoteText(message)}`}
      className={cn(
        'mb-1 block w-full rounded-card-sm px-2.5 py-1.5 text-start focus-visible:outline focus-visible:outline-2 focus-visible:outline-gold/60',
        hover,
        EDGE,
        surface,
      )}
    >
      <span className={cn('block truncate text-xs font-semibold', nameTone)}>{name}</span>
      <span className={cn('flex items-center gap-1 text-xs', bodyTone)}>
        <MediaGlyph kind={message.media_kind} />
        <span className="line-clamp-2 break-words">{quoteText(message)}</span>
      </span>
    </button>
  )
}

/** The strip above the composer while a reply is being written. */
export function ReplyBanner({
  name,
  message,
  onCancel,
}: {
  name: string
  message: Message
  onCancel: () => void
}) {
  return (
    <div
      className="mt-3 flex items-stretch gap-2 rounded-card-sm border border-white/10 bg-ink-2/60 pe-1.5"
      role="group"
      aria-label={`Replying to ${name}`}
    >
      <div className={cn('min-w-0 flex-1 rounded-s-card-sm px-2.5 py-1.5', EDGE)}>
        <span className="block truncate text-xs font-semibold text-gold-soft">Replying to {name}</span>
        <span className="flex items-center gap-1 text-xs text-text-mid">
          <MediaGlyph kind={message.media_kind} />
          <span className="truncate">{quoteText(message)}</span>
        </span>
      </div>
      <button
        type="button"
        onClick={onCancel}
        aria-label="Cancel reply"
        className="my-auto rounded-full p-1.5 text-text-mid hover:text-text-hi"
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  )
}

/** The reply arrow beside a bubble. Shown on hover or focus; always reachable by keyboard. */
export function ReplyButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title="Reply"
      className="hidden shrink-0 self-center rounded-full p-1.5 text-text-low opacity-0 hover:text-text-hi focus-visible:opacity-100 group-hover/bubble:opacity-100 [@media(pointer:fine)]:block"
    >
      <Reply size={14} aria-hidden="true" />
    </button>
  )
}

const SWIPE_COMMIT_PX = 56
const SWIPE_MAX_PX = 72
const LONG_PRESS_MS = 500

/**
 * Swipe a bubble towards the middle of the screen to reply, or hold it for its actions. The
 * bubble follows the finger directly (no easing: it is feedback, not
 * decoration), a reply arrow appears behind it, and a short buzz marks the
 * moment it will commit. Vertical movement is left to the list so scrolling
 * is never captured.
 */
export function SwipeToReply({
  onReply,
  onLongPress,
  disabled,
  children,
}: {
  onReply: () => void
  /** What a held press does. Defaults to replying. */
  onLongPress?: () => void
  disabled?: boolean
  children: ReactNode
}) {
  const [dx, setDx] = useState(0)
  const start = useRef<{ x: number; y: number; dir: 1 | -1; locked: boolean | null } | null>(null)
  const armed = useRef(false)
  const hold = useRef<number | undefined>(undefined)
  // What the last press was made with. The context-menu event that follows a
  // long press on a phone and the one a mouse's right button raises are the
  // same event; only the press before it says which one it was.
  const lastPointer = useRef<string | null>(null)

  const stopHold = () => {
    window.clearTimeout(hold.current)
    hold.current = undefined
  }
  const buzz = () => navigator.vibrate?.(12)

  if (disabled) return <>{children}</>

  return (
    <div
      className="relative min-w-0 touch-pan-y"
      onPointerDown={(e) => {
        lastPointer.current = e.pointerType
        // Touch only: a mouse has the reply arrow, and must keep text selection.
        if (e.pointerType !== 'touch') return
        const direction = getComputedStyle(e.currentTarget).direction === 'rtl' ? -1 : 1
        start.current = { x: e.clientX, y: e.clientY, dir: direction, locked: null }
        armed.current = false
        stopHold()
        hold.current = window.setTimeout(() => {
          hold.current = undefined
          start.current = null
          buzz()
          ;(onLongPress ?? onReply)()
        }, LONG_PRESS_MS)
      }}
      onPointerMove={(e) => {
        const s = start.current
        if (!s) return
        const moveX = (e.clientX - s.x) * s.dir
        const moveY = e.clientY - s.y
        if (Math.abs(moveX) > 8 || Math.abs(moveY) > 8) stopHold()
        if (s.locked === null && (Math.abs(moveX) > 10 || Math.abs(moveY) > 10)) {
          s.locked = Math.abs(moveX) > Math.abs(moveY) && moveX > 0
        }
        if (!s.locked) return
        const next = Math.min(Math.max(moveX, 0), SWIPE_MAX_PX)
        if (next >= SWIPE_COMMIT_PX && !armed.current) {
          armed.current = true
          buzz()
        } else if (next < SWIPE_COMMIT_PX) {
          armed.current = false
        }
        setDx(next * s.dir)
      }}
      onPointerUp={() => {
        stopHold()
        if (armed.current) onReply()
        armed.current = false
        start.current = null
        setDx(0)
      }}
      onPointerCancel={() => {
        stopHold()
        armed.current = false
        start.current = null
        setDx(0)
      }}
      onContextMenu={(e) => {
        // A long press raises this on Android, and the hold above has already
        // opened the message's own menu, so the browser's would be a second
        // menu on top of the first. Suppressed for a finger and only for a
        // finger.
        //
        // It used to test `start.current === null`, which is also true for
        // every mouse - a mouse never sets `start` - so a right-click on any
        // message was swallowed and nothing at all appeared. A right-click is
        // the browser's, and it gets the browser's menu.
        if (lastPointer.current === 'touch') e.preventDefault()
      }}
    >
      {dx !== 0 && (
        <span
          aria-hidden="true"
          className={cn(
            'pointer-events-none absolute inset-y-0 start-1 flex items-center text-text-mid',
            Math.abs(dx) >= SWIPE_COMMIT_PX && 'text-gold-soft',
          )}
        >
          <Reply size={16} />
        </span>
      )}
      <div style={dx ? { transform: `translateX(${dx}px)` } : undefined}>{children}</div>
    </div>
  )
}
