import { useEffect, useRef, useState } from 'react'
import { Heart, Lightbulb, PartyPopper, ThumbsUp, HandHeart } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { ApiError, kaluta, type ReactionSummary } from '@/lib/api'
import ReactorList from './ReactorList'
import { cn } from '@/lib/utils'

/** Must match REACTION_KINDS in social-service. */
export const REACTIONS: Array<{ id: string; label: string; icon: LucideIcon; tint: string }> = [
  { id: 'like', label: 'Like', icon: ThumbsUp, tint: 'text-sky' },
  { id: 'celebrate', label: 'Celebrate', icon: PartyPopper, tint: 'text-gold-soft' },
  { id: 'support', label: 'Support', icon: HandHeart, tint: 'text-emerald-400' },
  { id: 'insightful', label: 'Insightful', icon: Lightbulb, tint: 'text-amber-300' },
  { id: 'love', label: 'Love', icon: Heart, tint: 'text-rose-400' },
]

const BY_ID = new Map(REACTIONS.map((r) => [r.id, r]))

/**
 * A reaction control: one button that shows your current choice, and a picker
 * on hover or focus.
 *
 * One reaction per member per post, so choosing a second replaces the first and
 * tapping the current one clears it. Counts come with the post, so the row
 * renders without a request per card.
 */
export default function Reactions({
  postId,
  summary,
  onChange,
  isAuthor = false,
  canEngage = true,
  onRefused,
}: {
  postId: string
  summary: ReactionSummary
  onChange: (next: ReactionSummary) => void
  /** Whether the viewer wrote this post: only they may see who reacted. */
  isAuthor?: boolean
  /**
   * Whether this viewer may react at all. False for somebody reading an open
   * community they have not joined: every word is theirs to read and none of
   * it is theirs to answer.
   */
  canEngage?: boolean
  /** Why a reaction did not happen, for the caller to show where it makes sense. */
  onRefused?: (reason: string) => void
}) {
  const { tok } = useAppTheme()
  const [open, setOpen] = useState(false)
  const [showWho, setShowWho] = useState(false)
  // Leaving the trigger should not snatch the picker away mid-reach: the pointer
  // has to travel, and a stray pixel between the two used to cancel the whole
  // interaction. A short grace period makes the gesture forgiving.
  const closeTimer = useRef<number | undefined>(undefined)
  const show = () => {
    window.clearTimeout(closeTimer.current)
    setOpen(true)
  }
  const hide = () => {
    window.clearTimeout(closeTimer.current)
    closeTimer.current = window.setTimeout(() => setOpen(false), 260)
  }
  useEffect(() => () => window.clearTimeout(closeTimer.current), [])
  const [busy, setBusy] = useState(false)

  const mine = summary.mine ? BY_ID.get(summary.mine) : null
  const Icon = mine?.icon ?? ThumbsUp

  const react = async (kind: string) => {
    if (busy) return
    // Said before the tap rather than undone after it: an optimistic count
    // that appears and vanishes reads as the page breaking.
    if (!canEngage) {
      setOpen(false)
      onRefused?.('Join this community to take part in it.')
      return
    }
    setBusy(true)
    setOpen(false)
    // Optimistic: reflect the tap now, reconcile with the server's counts after.
    const optimistic: ReactionSummary = {
      counts: { ...summary.counts },
      total: summary.total,
      mine: summary.mine === kind ? null : kind,
    }
    if (summary.mine) optimistic.counts[summary.mine] = Math.max(0, (optimistic.counts[summary.mine] ?? 1) - 1)
    if (optimistic.mine) optimistic.counts[kind] = (optimistic.counts[kind] ?? 0) + 1
    optimistic.total = Object.values(optimistic.counts).reduce((a, b) => a + b, 0)
    onChange(optimistic)

    try {
      const result = await kaluta.posts.react(postId, kind)
      onChange(result.reactions)
    } catch (err) {
      onChange(summary) // roll back
      // And say why. A reaction that silently undoes itself looks like the
      // page is broken, and the server's refusal is usually something the
      // reader can act on - "join this community" most of all.
      onRefused?.(err instanceof ApiError ? err.message : 'That reaction did not go through.')
    } finally {
      setBusy(false)
    }
  }

  // The kinds actually used on this post, biggest first — a summary, not a tally.
  const present = Object.entries(summary.counts)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)

  return (
    <div className="flex items-center gap-1.5">
      <div
        className="relative"
        onMouseEnter={show}
        onMouseLeave={hide}
      >
        <button
          type="button"
          onClick={() => react(summary.mine ?? 'like')}
          onFocus={show}
          aria-pressed={Boolean(summary.mine)}
          aria-label={mine ? `Your reaction: ${mine.label}. Change or remove it.` : 'React'}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold',
            mine ? mine.tint : cn(tok.mid, tok.hoverBg),
          )}
        >
          <Icon size={14} fill={summary.mine ? 'currentColor' : 'none'} aria-hidden="true" />
          {mine?.label ?? 'React'}
        </button>

        {open && (
          /* pb-2 on the positioner, not mb-1 on the panel: the padding is part
             of the hover target, so the pointer never crosses dead space. */
          <div className="absolute bottom-full start-0 z-20 pb-2">
            <div className={cn('flex gap-0.5 rounded-full p-1 shadow-cloud', tok.cardSolid)}>
            {REACTIONS.map((reaction) => (
              <button
                key={reaction.id}
                type="button"
                onClick={() => react(reaction.id)}
                title={reaction.label}
                aria-label={reaction.label}
                className={cn(
                  'flex h-8 w-8 items-center justify-center rounded-full',
                  reaction.tint,
                  summary.mine === reaction.id && 'bg-white/10',
                )}
              >
                <reaction.icon size={16} fill={summary.mine === reaction.id ? 'currentColor' : 'none'} />
              </button>
            ))}
            </div>
          </div>
        )}
      </div>

      {summary.total > 0 &&
        // For the author the count is a question with an answer, so it is a
        // button. For everyone else it is a number, so it is not: the server
        // refuses them the list, and a control that always fails is worse
        // than no control.
        (isAuthor ? (
          <button
            type="button"
            onClick={() => setShowWho(true)}
            aria-label={`See who reacted (${summary.total})`}
            className={cn(
              'inline-flex items-center gap-0.5 rounded-full px-1 text-xs hover:underline',
              tok.low,
            )}
          >
            {present.map(([kind]) => {
              const meta = BY_ID.get(kind)
              if (!meta) return null
              return <meta.icon key={kind} size={11} className={meta.tint} aria-hidden="true" />
            })}
            <span className="ms-0.5">{summary.total}</span>
          </button>
        ) : (
          <span className={cn('inline-flex items-center gap-0.5 text-xs', tok.low)}>
            {present.map(([kind]) => {
              const meta = BY_ID.get(kind)
              if (!meta) return null
              return <meta.icon key={kind} size={11} className={meta.tint} aria-hidden="true" />
            })}
            <span className="ms-0.5">{summary.total}</span>
          </span>
        ))}

      {showWho && <ReactorList postId={postId} onClose={() => setShowWho(false)} />}
    </div>
  )
}
