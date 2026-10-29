import { useState } from 'react'
import { Music } from 'lucide-react'
import { preview } from '@/lib/mediaurl'
import type { PostMedia } from '@/lib/api'

/**
 * A post's audio: the browser's own player - play and pause, seeking, the
 * elapsed time and the length, volume where the device has it - which is
 * keyboard- and screen-reader-accessible as it comes. Never started on its
 * own: `preload="metadata"` reads only the length until somebody presses play.
 *
 * The address is the signed one the server sent for this viewer, as for a
 * video, so who may hear it is decided where who may see the post is.
 */
export default function AudioPost({
  item,
  onLoadDeferred,
  loadingDeferred,
}: {
  item: PostMedia
  /** Data saver: the server sent no URL until the member asks. */
  onLoadDeferred: () => void
  loadingDeferred: boolean
}) {
  const [failed, setFailed] = useState(false)

  return (
    <div className="mt-3 flex items-center gap-3 rounded-2xl border border-white/8 bg-white/[0.03] px-3 py-2.5">
      <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gold/15 text-gold-soft">
        <Music size={16} />
      </span>
      {item.url === null ? (
        <button
          type="button"
          onClick={onLoadDeferred}
          disabled={loadingDeferred}
          className="flex-1 text-start text-sm text-text-hi disabled:opacity-60"
        >
          {loadingDeferred ? 'Loading…' : 'Tap to load the audio'}
          <span className="caption block text-text-low">Data saver is on - nothing downloaded yet</span>
        </button>
      ) : failed ? (
        <p className="caption flex-1">This audio could not be played.</p>
      ) : (
        <audio
          controls
          preload="metadata"
          src={preview(item.url) ?? item.url}
          onError={() => setFailed(true)}
          aria-label={item.alt_text || 'Audio'}
          className="h-10 min-w-0 flex-1"
        >
          Your browser cannot play this audio.
        </audio>
      )}
    </div>
  )
}
