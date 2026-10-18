import { useEffect, useState } from 'react'
import { preview } from '@/lib/mediaurl'
import { cn } from '@/lib/utils'
import { createPortal } from 'react-dom'
import { ChevronLeft, ChevronRight, Download, MessageCircle, X } from 'lucide-react'
import type { PostMedia } from '@/lib/api'
import VideoPlayer from './VideoPlayer'

/**
 * Full-size preview for a post's media.
 *
 * Deliberately not the shadcn Dialog: this needs the whole viewport, a black
 * ground and keyboard paging, and fighting a centred, padded, max-width panel
 * into that shape costs more than the ~60 lines here.
 */
/** lg and up, watched rather than read once: a tablet turns. */
function useWide(): boolean {
  const [wide, setWide] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(min-width: 1024px)').matches,
  )
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 1024px)')
    const update = () => setWide(mq.matches)
    update()
    mq.addEventListener('change', update)
    return () => mq.removeEventListener('change', update)
  }, [])
  return wide
}

export default function MediaLightbox({
  media,
  index,
  onClose,
  aside,
  asideOpen,
}: {
  media: PostMedia[]
  index: number
  onClose: () => void
  /**
   * What sits beside the picture on a wide screen - the post and its
   * comments, for a feed photo.
   *
   * Optional, because not every picture has a conversation around it: the
   * product gallery and a chat attachment are pictures and nothing else, and
   * giving them an empty panel would be worse than giving them none.
   *
   * On a phone it is a sheet along the bottom rather than a column at the
   * side. It used to be `lg:block` and nothing else, on the reasoning that a
   * photo wants the whole screen and the comments are a tap away on the post
   * - but the comment button on a post *with* a picture opens this viewer, so
   * on a phone tapping "comments" gave you a black screen, a photo, and no
   * comments anywhere.
   */
  aside?: React.ReactNode
  /**
   * Whether the sheet starts open on a phone. True when the member asked for
   * the conversation (the comment button), false when they asked for the
   * picture (tapping it) - which is the distinction Facebook makes too.
   */
  asideOpen?: boolean
}) {
  const [current, setCurrent] = useState(index)
  const [sheet, setSheet] = useState(Boolean(asideOpen))
  // Which shape the panel takes, decided in JavaScript rather than by two
  // elements with `hidden lg:block` between them. Rendering both would mount
  // the conversation twice - two fetches, two composers, two counts - and the
  // hidden one would answer every change the visible one made.
  const wide = useWide()

  useEffect(() => setCurrent(index), [index])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key === 'ArrowRight') setCurrent((i) => Math.min(media.length - 1, i + 1))
      if (event.key === 'ArrowLeft') setCurrent((i) => Math.max(0, i - 1))
    }
    document.addEventListener('keydown', onKey)
    // The page behind must not scroll while a full-screen viewer is open.
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [media.length, onClose])

  const item = media[current]
  if (!item) return null

  // Portalled to <body>: an ancestor with backdrop-filter (every .cloud-card
  // has one) establishes a containing block, and `position: fixed` would then
  // resolve against the post card instead of the viewport — the overlay opened
  // at 664×453 inside the card rather than full screen.
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Media preview"
      className={cn(
        'fixed inset-0 z-[90] flex bg-black',
        // Pure black, not black/92: a photo viewer that lets the page glow
        // through reads as an overlay rather than as somewhere you have gone.
        //
        // With a conversation attached the two share the screen: stacked on a
        // phone, side by side from lg. Stacking in the flow rather than
        // floating the sheet over the photo is what lets the picture give up
        // the height it needs to - a sheet drawn on top would simply cover it.
        aside ? 'flex-col items-stretch lg:flex-row' : 'items-center justify-center',
      )}
      onClick={onClose}
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="Close preview"
        // Top-left, where every photo viewer puts it - and away from the
        // comments panel's own controls on the right.
        className="absolute start-4 top-4 z-20 rounded-full bg-white/10 p-2.5 text-white hover:bg-white/20"
      >
        <X size={18} />
      </button>
      {/* The original file, in its own format. Right-click "Save image as"
          saves the display copy, which is WEBP - not what anybody expects a
          saved photo to be. */}
      {item && item.kind === 'image' && item.url && (
        <a
          href={item.url}
          download
          onClick={(event) => event.stopPropagation()}
          aria-label="Download the original picture"
          title="Download"
          className="absolute start-16 top-4 z-20 rounded-full bg-white/10 p-2.5 text-white hover:bg-white/20"
        >
          <Download size={18} />
        </a>
      )}


      {/* The picture's half. flex-1 so it takes whatever the panel leaves,
          and centred inside that rather than inside the window - otherwise the
          photo sits behind the comments on a narrow desktop. */}
      <div className="relative flex min-h-0 min-w-0 flex-1 items-center justify-center overflow-hidden p-4">
      {/* The arrows belong to the picture, so they live in its column.
          On the dialog itself, `end-3` is the far right of the whole window -
          which, with the panel open, is the far side of the comments, not the
          far side of the photo. They also lost their centring there: the
          dialog switches to items-stretch to let the panel fill the height, so
          the arrows stretched with it instead of sitting at the middle. They
          now centre themselves and are measured against the picture's own
          column. */}
      {media.length > 1 && (
        <>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setCurrent((i) => Math.max(0, i - 1))
            }}
            disabled={current === 0}
            aria-label="Previous"
            className="absolute start-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-white/10 p-3 text-white hover:bg-white/20 disabled:opacity-25"
          >
            <ChevronLeft size={20} />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setCurrent((i) => Math.min(media.length - 1, i + 1))
            }}
            disabled={current === media.length - 1}
            aria-label="Next"
            className="absolute end-3 top-1/2 z-10 -translate-y-1/2 rounded-full bg-white/10 p-3 text-white hover:bg-white/20 disabled:opacity-25"
          >
            <ChevronRight size={20} />
          </button>
        </>
      )}
      {/* Stop the backdrop's close handler from firing on the media itself. */}
      {/* Bounded by this column, not by the window.
          max-w-[92vw] is 92% of the *viewport*, which knows nothing about the
          comments panel - so with the panel open the picture was drawn across
          it and sliced the first words off every line of the post. Sizing
          against the container is what makes the two halves share the screen
          instead of overlapping.

          Height is still capped against the window: the panel is beside the
          picture, not above it, so it never contests the height - and
          max-h-full here is circular, because this wrapper's height is set by
          the very image it would be constraining. A 1000x1600 poster rendered
          at full size and ran off the bottom. */}
      <div className="flex h-full max-w-full items-center justify-center" onClick={(e) => e.stopPropagation()}>
        {item.kind === 'video' ? (
          // The same player as in the feed, so the controls do not change shape
          // between the card and the full-size view. Unmuted here: opening a
          // video full-size is asking to watch it.
          <VideoPlayer
            src={preview(item.url) ?? item.url ?? ''}
            poster={item.poster_url}
            autoplay
            startMuted={false}
            fill
            className="max-h-full max-w-full rounded-lg"
          />
        ) : (
          <img
            // The display copy here too. Its long edge is 1440 px, which fills
            // a full-screen view on anything short of a retina desktop, and it
            // is the picture the feed has just finished downloading - so
            // opening one is instant instead of fetching the camera file
            // again. The original is still what a download gives.
            src={preview(item.url)}
            alt={item.alt_text ?? ''}
            className="max-h-full max-w-full object-contain"
          />
        )}
      </div>

      {media.length > 1 && (
        <p className="absolute bottom-5 text-xs text-white/70">
          {current + 1} / {media.length}
        </p>
      )}
      {item.alt_text && (
        <p className="absolute bottom-12 max-w-lg px-6 text-center text-sm text-white/80">
          {item.alt_text}
        </p>
      )}
      </div>

      {/* The conversation, beside the picture. Its own scroll, so a long
          thread never pushes the photo out of the window. */}
      {aside && wide && (
        <aside
          onClick={(e) => e.stopPropagation()}
          className="w-[400px] shrink-0 overflow-y-auto overscroll-contain bg-ink-2 p-5 xl:w-[460px]"
        >
          {aside}
        </aside>
      )}

      {/* The same conversation on a phone, as a sheet under the picture.
          Closed, it is a bar you tap; open, it takes the lower half and the
          photo keeps the rest. */}
      {aside && !wide && (
        <div className="shrink-0" onClick={(e) => e.stopPropagation()}>
          {sheet ? (
            <section className="flex max-h-[62svh] flex-col rounded-t-2xl bg-ink-2">
              <button
                type="button"
                onClick={() => setSheet(false)}
                aria-label="Hide comments"
                aria-expanded="true"
                className="flex shrink-0 justify-center py-2.5"
              >
                {/* The handle every sheet on a phone has. It is the control,
                    not decoration: tapping anywhere on this row closes it.
                    Drawn in the panel's own text colour - the first version
                    was white/25, which is invisible on the light theme the
                    panel actually uses. */}
                <span className="h-1 w-9 rounded-full bg-text-hi/25" />
              </button>
              <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
                {aside}
              </div>
            </section>
          ) : (
            <button
              type="button"
              onClick={() => setSheet(true)}
              aria-expanded="false"
              className="flex w-full items-center justify-center gap-2 rounded-t-2xl bg-ink-2 py-3.5 pb-[max(0.875rem,env(safe-area-inset-bottom))] text-sm font-semibold text-text-hi"
            >
              <MessageCircle size={16} aria-hidden="true" />
              Comments
            </button>
          )}
        </div>
      )}
    </div>,
    document.body,
  )
}
