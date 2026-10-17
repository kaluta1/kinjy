import { useState } from 'react'
import { preview } from '@/lib/mediaurl'
import type { PostMedia } from '@/lib/api'
import { cn } from '@/lib/utils'
import VideoPlayer from './VideoPlayer'

/**
 * A post's pictures, laid out the way people expect them.
 *
 * What was wrong before: every image was `object-cover`, which crops. A single
 * portrait photo — most phone photos — lost its top and bottom, so a picture of
 * somebody arrived with their head cut off, and a screenshot arrived unreadable.
 * Several images were forced into fixed-height halves, so three pictures left a
 * gap and five were simply all there, each one short and cropped.
 *
 * The rule now follows the count, because what is right depends on it:
 *
 * * **One** — never cropped. The whole picture, at its own shape, centred on a
 *   dark ground and capped in height so a very tall image does not push the
 *   next post off the screen. A single image is the post; cutting it is cutting
 *   the post.
 * * **Two** — side by side, equal, cropped to match. Two pictures of different
 *   shapes sitting at different heights reads as a mistake.
 * * **Three** — one tall on the left, two stacked on the right.
 * * **Four or more** — a two-by-two grid, with "+N" over the last tile when
 *   there are more. Cropping is right here: the grid is a contact sheet, and
 *   the viewer is one tap away.
 *
 * Cropped tiles are centred on their subject as far as CSS allows
 * (`object-cover` keeps the middle), which is why the single-image case is the
 * one that must not crop: there is no grid to keep tidy, only a photograph.
 */
/**
 * The shape a feed picture is allowed to be, as a width/height ratio.
 *
 * The same limits Facebook uses: nothing taller than 4:5 and nothing wider
 * than 1.91:1. Outside that range the picture is cropped to the limit rather
 * than shown whole, because a 9:16 phone photo shown whole is a column of
 * image two screens tall that buries the next post.
 */
const MIN_RATIO = 0.8
const MAX_RATIO = 1.91
/** And never taller than this, however wide the column gets. */
const MAX_MEDIA_HEIGHT = 560

/**
 * The shape of a multi-picture block, as width/height.
 *
 * A ratio rather than a height in pixels. The heights here used to be h-64 and
 * h-80 - 256 and 320 - chosen when the feed column was about 814px wide. The
 * column is 560 now, and a fixed height cannot follow it: the same h-80 that
 * gave reasonable tiles at 814 was producing 278x158 tiles, a ratio of 1.76,
 * which is a letterbox slot. A portrait photo in one of those is a vertical
 * strip of its own middle.
 *
 * These are chosen so the tiles come out close to square, which is what every
 * other feed does and what a cropped thumbnail wants to be:
 *
 *   two   - side by side, so the block is twice a square tile
 *   three - one tall beside two stacked; the tall one reads as a portrait
 *   four  - a two-by-two of squares, so the block is square itself
 *
 * Expressed this way they hold at any column width, including a phone's.
 */
const BLOCK_RATIO: Record<number, number> = { 2: 2, 3: 1.45, 4: 1 }

export default function MediaGrid({
  media,
  onOpen,
  onLoadDeferred,
  loadingDeferred,
  autoplay = true,
}: {
  media: PostMedia[]
  onOpen: (index: number) => void
  /** Data saver: the server sent no URL until the member asks. */
  onLoadDeferred: () => void
  loadingDeferred: boolean
  autoplay?: boolean
}) {
  // Measured from the file when the server did not record it, which is almost
  // always: 10 of the 11 images in production carry no width or height. Without
  // this the single-image box has no shape to hold and falls back to a minimum.
  //
  // Declared before any early return - a hook after a conditional return runs
  // on some renders and not others, which is React error #310.
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null)

  if (media.length === 0) return null

  const shown = media.slice(0, 4)
  const hidden = media.length - shown.length

  const tile = (item: PostMedia, index: number, className: string) => (
    <Tile
      key={item.url ?? `deferred-${index}`}
      item={item}
      index={index}
      className={className}
      onOpen={onOpen}
      onLoadDeferred={onLoadDeferred}
      loadingDeferred={loadingDeferred}
      autoplay={autoplay}
      more={index === shown.length - 1 && hidden > 0 ? hidden : 0}
    />
  )

  // One: the picture, filling the card, at a shape the feed can live with.
  if (media.length === 1) {
    const only = media[0]
    // The recorded shape if there is one, otherwise the shape measured off the
    // file once it lands.
    const source =
      only.width && only.height ? { width: only.width, height: only.height } : natural
    const trueRatio = source ? source.width / source.height : null
    const ratio = trueRatio ? Math.min(Math.max(trueRatio, MIN_RATIO), MAX_RATIO) : null
    // Clamped, so part of the picture is not on screen. Worth saying so: the
    // card otherwise looks like the whole picture, just badly framed.
    const cropped = trueRatio !== null && ratio !== null && Math.abs(trueRatio - ratio) > 0.01
    return (
      <div
        className="relative -mx-5 mt-3 overflow-hidden border-y border-white/8 bg-black/40"
        style={
          ratio
            ? {
                // Width is set explicitly, not left to auto: a block with
                // aspect-ratio *and* max-height shrinks itself sideways to keep
                // the ratio once the cap bites, and a square picture came out
                // 560 wide in an 814 column with dead card either side of it.
                //
                // The +2.5rem is the -mx-5 above. A plain 100% resolves against
                // the card's *content* box, so it cancelled the right half of
                // the bleed: the picture sat flush to the left edge and stopped
                // 40px short of the right one.
                width: 'calc(100% + 2.5rem)',
                aspectRatio: String(ratio),
                maxHeight: MAX_MEDIA_HEIGHT,
              }
            : // Nothing measured yet. A minimum keeps the jump small rather
              // than total when the picture lands and the real shape arrives.
              { minHeight: 220 }
        }
      >
        <Tile
          item={only}
          index={0}
          single
          boxed={Boolean(ratio)}
          className=""
          onOpen={onOpen}
          onLoadDeferred={onLoadDeferred}
          loadingDeferred={loadingDeferred}
          autoplay={autoplay}
          more={0}
          onNatural={setNatural}
        />
        {cropped && only.kind !== 'video' && (
          /* The cut edge, faded, with the way to the whole picture.

             The fade is pointer-events-none so the picture underneath stays
             tappable along its bottom edge; the button is the only thing here
             that takes a click, and it stops the event so it opens the image
             rather than the post. */
          <>
            {/* The cut edge, blurred rather than merely darkened. A dark
                gradient dims the picture; a blur says the picture carries on
                and you are not being shown all of it, which is the actual
                state of affairs.

                Masked so the blur fades in going up instead of starting at a
                hard line - an unmasked backdrop-filter draws a band across the
                photo, which looks like a defect. */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-x-0 bottom-0 h-24 backdrop-blur-md"
              style={{
                maskImage: 'linear-gradient(to top, #000 45%, transparent)',
                WebkitMaskImage: 'linear-gradient(to top, #000 45%, transparent)',
              }}
            />
            {/* A little dark under it, so white text on the button holds up
                over a pale photo. Much lighter than before, because the blur
                is now doing the work the darkness used to do. */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/45 to-transparent"
            />
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                onOpen(0)
              }}
              className="absolute bottom-3 right-3 rounded-full bg-black/55 px-3 py-1.5 text-[0.78rem] font-semibold text-white backdrop-blur-sm hover:bg-black/75"
            >
              See full image
            </button>
          </>
        )}
      </div>
    )
  }

  // Three: one tall beside two stacked.
  if (media.length === 3) {
    return (
      <div
        className="-mx-5 mt-3 grid grid-cols-2 gap-0.5 overflow-hidden border-y border-white/8 bg-ink"
        style={{ aspectRatio: String(BLOCK_RATIO[3]), maxHeight: MAX_MEDIA_HEIGHT }}
      >
        {tile(media[0], 0, 'h-full min-h-0')}
        <div className="grid min-h-0 grid-rows-2 gap-0.5">
          {tile(media[1], 1, 'h-full min-h-0')}
          {tile(media[2], 2, 'h-full min-h-0')}
        </div>
      </div>
    )
  }

  // Two, or four and more.
  return (
    <div
      className="-mx-5 mt-3 grid grid-cols-2 gap-0.5 overflow-hidden border-y border-white/8 bg-ink"
      style={{
        aspectRatio: String(BLOCK_RATIO[media.length === 2 ? 2 : 4]),
        maxHeight: MAX_MEDIA_HEIGHT,
      }}
    >
      {shown.map((item, index) => tile(item, index, 'h-full min-h-0'))}
    </div>
  )
}

function Tile({
  item,
  index,
  className,
  onOpen,
  onLoadDeferred,
  loadingDeferred,
  autoplay,
  more,
  single = false,
  boxed = false,
  onNatural,
}: {
  item: PostMedia
  index: number
  className: string
  onOpen: (index: number) => void
  onLoadDeferred: () => void
  loadingDeferred: boolean
  autoplay: boolean
  /** How many further pictures this tile stands for. */
  more: number
  single?: boolean
  /** The container already has the right shape, so the image fills it. */
  boxed?: boolean
  /** Reports the file's real shape when the server never recorded one. */
  onNatural?: (size: { width: number; height: number }) => void
}) {
  const [broken, setBroken] = useState(false)

  // Data saver: nothing has downloaded, and the tap is what asks for it.
  if (item.url === null) {
    return (
      <button
        type="button"
        onClick={onLoadDeferred}
        disabled={loadingDeferred}
        className={cn(
          'flex w-full flex-col items-center justify-center gap-1 bg-white/4 hover:bg-white/8 disabled:opacity-60',
          single ? 'py-10' : className,
        )}
      >
        <span className="text-sm text-text-hi">
          {loadingDeferred ? 'Loading…' : `Tap to load ${item.kind}`}
        </span>
        <span className="caption text-text-low">Data saver is on — nothing downloaded yet</span>
      </button>
    )
  }

  if (item.kind === 'video') {
    return (
      <div className={cn('relative w-full overflow-hidden bg-black', className)}>
        <VideoPlayer
          // The local copy, like the pictures. A video is read in pieces as
          // it plays and again on every seek, and each of those pieces was a
          // round trip to object storage.
          src={preview(item.url) ?? item.url}
          poster={item.poster_url}
          autoplay={autoplay}
          onExpand={() => onOpen(index)}
          className="h-full w-full"
        />
      </div>
    )
  }

  if (broken) {
    // A dead URL should leave a tidy tile, not a torn-page icon and the alt
    // text spilling across the card.
    return (
      <div
        className={cn(
          'flex w-full items-center justify-center bg-white/[0.04]',
          single ? 'py-12' : className,
        )}
      >
        <span className="caption">This image could not be loaded</span>
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(index)}
      aria-label={item.alt_text || 'Open image'}
      className={cn('relative block w-full overflow-hidden', single && boxed && 'h-full', className)}
    >
      <img
        // The feed-sized copy, not the camera file. A tap opens the original.
        src={preview(item.url)}
        alt={item.alt_text ?? ''}
        loading="lazy"
        onError={() => setBroken(true)}
        onLoad={(event) => {
          if (!onNatural) return
          const el = event.currentTarget
          if (el.naturalWidth && el.naturalHeight) {
            onNatural({ width: el.naturalWidth, height: el.naturalHeight })
          }
        }}
        className={cn(
          'cursor-zoom-in hover:opacity-95',
          single
            // Fills the card and crops to the box, the way a feed picture does
            // everywhere else. object-contain was letterboxing instead: once
            // the height cap bit, a tall picture sat in a band of black with
            // the card's own width unused either side of it.
            //
            // What is cropped is only what falls outside 4:5 or 1.91:1, and a
            // tap still opens the whole picture.
            //
            // `h-full` only when the container already has the shape: inside a
            // box defined by min-height alone it resolves to zero, and the
            // image renders as a line - so until the file is measured it is
            // laid out by its own width instead.
            // object-top, not the default centre. What gets cut off a tall
            // picture is then its foot rather than its head, and the head is
            // where a poster puts its title and a screenshot its first line -
            // a banner arrived with its own headline sliced off.
            ? cn('w-full object-cover object-top', boxed ? 'h-full' : 'max-h-[560px] object-contain')
            : 'h-full w-full object-cover',
        )}
      />
      {more > 0 && (
        <span className="absolute inset-0 flex items-center justify-center bg-black/55 text-2xl font-semibold text-white">
          +{more}
        </span>
      )}
    </button>
  )
}
