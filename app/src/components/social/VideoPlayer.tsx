import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Maximize, Maximize2, Minimize, Pause, Play, RotateCcw, Volume2, VolumeX } from 'lucide-react'
import { cn } from '@/lib/utils'

const pad = (n: number) => String(Math.floor(n)).padStart(2, '0')
const clock = (seconds: number) => {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return h ? `${h}:${pad(m)}:${pad(seconds % 60)}` : `${m}:${pad(seconds % 60)}`
}
const SPEEDS = [1, 1.25, 1.5, 2, 0.75]

/**
 * Kinjy's video player. The browser's own controls are never shown.
 *
 * They are a different shape, colour and size in every browser, sit on top of
 * the card's rounding, and on mobile Safari hijack the tap that should play.
 *
 * What a member sees, in order:
 * - **the cover** - the still media-service took from the video, or the
 *   brand gradient when there is none - with a play button, before anything
 *   has downloaded;
 * - **a spinner** whenever the video is fetching and cannot play yet: from
 *   the press of play to the first frame, and again whenever playback stalls.
 *   A press that seems to do nothing is the complaint this answers;
 * - **our bar**: play, time, a seek track that shows how much has already
 *   loaded and can be dragged, speed, sound, and real fullscreen.
 *
 * Playback follows the viewport when the member allows autoplay (muted, and
 * never after they paused it by hand). Space/K play and pause, arrows seek
 * five seconds, M mutes, F goes fullscreen.
 */
export default function VideoPlayer({
  src,
  poster,
  autoplay = true,
  className,
  onExpand,
  startMuted = true,
  fill = false,
}: {
  src: string
  /** The cover. A missing or broken one falls back to the gradient. */
  poster?: string | null
  /** The member's data/autoplay preference. False means it waits to be asked. */
  autoplay?: boolean
  className?: string
  /** Opens the video in the post's viewer, when there is one. */
  onExpand?: () => void
  startMuted?: boolean
  /** In the viewer the video fills the space instead of being capped. */
  fill?: boolean
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [started, setStarted] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [ended, setEnded] = useState(false)
  const [muted, setMuted] = useState(startMuted)
  const [volume, setVolume] = useState(1)
  const [progress, setProgress] = useState(0)
  const [buffered, setBuffered] = useState(0)
  const [duration, setDuration] = useState(0)
  const [speed, setSpeed] = useState(1)
  const [full, setFull] = useState(false)
  const [coverOk, setCoverOk] = useState(Boolean(poster))
  const [failed, setFailed] = useState(false)
  const [dragging, setDragging] = useState(false)
  // The bar hides itself during playback when nobody is touching the player.
  const [idle, setIdle] = useState(false)
  const idleTimer = useRef<number | null>(null)
  // Set the moment somebody presses pause themselves: from then on the
  // viewport may stop this video but never start it again.
  const pausedByHand = useRef(false)

  useEffect(() => setCoverOk(Boolean(poster)), [poster])

  const play = useCallback(() => {
    const video = ref.current
    if (!video) return
    setFailed(false)
    // Shown at once, cleared by `playing`: the gap between the press and the
    // first frame is exactly when a member wonders whether anything happened.
    if (video.readyState < 3) setWaiting(true)
    void video.play().catch(() => setWaiting(false))
  }, [])

  const wake = useCallback(() => {
    setIdle(false)
    if (idleTimer.current) window.clearTimeout(idleTimer.current)
    idleTimer.current = window.setTimeout(() => setIdle(true), 2500)
  }, [])
  useEffect(
    () => () => {
      if (idleTimer.current) window.clearTimeout(idleTimer.current)
    },
    [],
  )

  useEffect(() => {
    const video = ref.current
    const wrap = wrapRef.current
    if (!video || !wrap) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && entry.intersectionRatio >= 0.5) {
          if (autoplay && !pausedByHand.current && !ended) play()
        } else {
          video.pause()
        }
      },
      { threshold: [0, 0.5, 1] },
    )
    observer.observe(wrap)
    return () => observer.disconnect()
  }, [autoplay, play, ended])

  useEffect(() => {
    const video = ref.current
    if (!video) return
    const readBuffer = () => {
      const ranges = video.buffered
      let end = 0
      for (let i = 0; i < ranges.length; i += 1) {
        if (ranges.start(i) <= video.currentTime + 0.5) end = Math.max(end, ranges.end(i))
      }
      setBuffered(end)
    }
    const handlers: Array<[string, () => void]> = [
      ['play', () => { setPlaying(true); setStarted(true); setEnded(false) }],
      ['pause', () => setPlaying(false)],
      ['playing', () => setWaiting(false)],
      ['waiting', () => setWaiting(true)],
      ['stalled', () => { if (!video.paused) setWaiting(true) }],
      ['canplay', () => setWaiting(false)],
      ['ended', () => { setEnded(true); setPlaying(false) }],
      ['error', () => { setFailed(true); setWaiting(false) }],
      ['timeupdate', () => { setProgress(video.currentTime); readBuffer() }],
      ['progress', readBuffer],
      ['loadedmetadata', () => setDuration(video.duration)],
      ['durationchange', () => setDuration(video.duration)],
      ['volumechange', () => { setMuted(video.muted); setVolume(video.volume) }],
    ]
    for (const [name, fn] of handlers) video.addEventListener(name, fn)
    return () => {
      for (const [name, fn] of handlers) video.removeEventListener(name, fn)
    }
  }, [])

  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement === wrapRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const toggle = () => {
    const video = ref.current
    if (!video) return
    if (video.paused) {
      pausedByHand.current = false
      if (ended) video.currentTime = 0
      play()
    } else {
      pausedByHand.current = true
      video.pause()
    }
    wake()
  }

  const seekTo = (seconds: number) => {
    const video = ref.current
    if (!video || !duration) return
    video.currentTime = Math.min(Math.max(0, seconds), duration)
    setProgress(video.currentTime)
    setEnded(false)
  }

  const seekFromPointer = (clientX: number) => {
    const track = trackRef.current
    if (!track || !duration) return
    const box = track.getBoundingClientRect()
    const ratio = Math.min(Math.max((clientX - box.left) / box.width, 0), 1)
    seekTo(ratio * duration)
  }

  /** Hand over to the viewer's own player, stopping this one first. */
  const openViewer = () => {
    if (!onExpand) return
    pausedByHand.current = true
    ref.current?.pause()
    onExpand()
  }

  const toggleFull = () => {
    const wrap = wrapRef.current
    if (!wrap) return
    if (document.fullscreenElement) void document.exitFullscreen()
    else if (wrap.requestFullscreen) void wrap.requestFullscreen().catch(openViewer)
    else openViewer()
  }

  const toggleMute = () => {
    const video = ref.current
    if (!video) return
    video.muted = !video.muted
    setMuted(video.muted)
  }

  const cycleSpeed = () => {
    const video = ref.current
    if (!video) return
    const next = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length]
    video.playbackRate = next
    setSpeed(next)
  }

  const onKey = (event: React.KeyboardEvent) => {
    const key = event.key.toLowerCase()
    if (key === ' ' || key === 'k') toggle()
    else if (key === 'arrowright') seekTo(progress + 5)
    else if (key === 'arrowleft') seekTo(progress - 5)
    else if (key === 'm') toggleMute()
    else if (key === 'f') toggleFull()
    else return
    event.preventDefault()
    wake()
  }

  const percent = duration ? (progress / duration) * 100 : 0
  const loaded = duration ? Math.min(100, (buffered / duration) * 100) : 0
  const showBar = !playing || !idle || dragging || waiting

  return (
    <div
      ref={wrapRef}
      tabIndex={0}
      role="group"
      aria-label="Video player"
      onKeyDown={onKey}
      onPointerMove={wake}
      className={cn(
        'group relative overflow-hidden bg-black outline-none focus-visible:ring-2 focus-visible:ring-gold/60',
        full && 'flex items-center justify-center',
        className,
      )}
    >
      <video
        ref={ref}
        src={src}
        muted={muted}
        playsInline
        // Metadata only until somebody plays: a few kilobytes for the length,
        // not the film. The cover is what is shown meanwhile.
        preload="metadata"
        onClick={toggle}
        className={cn(
          'w-full cursor-pointer bg-black',
          full
            ? 'h-full max-h-none object-contain'
            : fill
              ? 'max-h-[88svh] object-contain'
              : 'aspect-video max-h-[520px] object-contain',
        )}
      />

      {/* The cover, until the first frame plays. An <img> rather than the
          video's poster attribute, so a missing cover can fall back. */}
      {!started && (
        <div className="pointer-events-none absolute inset-0">
          {poster && coverOk ? (
            <img src={poster} alt="" onError={() => setCoverOk(false)} className="h-full w-full object-cover" />
          ) : (
            <div className="h-full w-full bg-gradient-to-br from-[#1b2340] via-[#2b2a3f] to-[#4a3b22]" />
          )}
        </div>
      )}

      {/* Fetching: from the press of play to the first frame, and on stalls. */}
      {waiting && !failed && (
        <div
          className="pointer-events-none absolute inset-0 grid place-items-center bg-black/25"
          role="status"
          aria-label="Loading video"
        >
          <Loader2 size={44} className="animate-spin text-white drop-shadow" aria-hidden="true" />
        </div>
      )}

      {failed && (
        <div className="absolute inset-0 grid place-items-center bg-black/60 p-4 text-center" role="alert">
          <div>
            <p className="text-sm text-white/90">This video could not be loaded.</p>
            <button
              type="button"
              onClick={() => {
                const video = ref.current
                if (!video) return
                setFailed(false)
                video.load()
                play()
              }}
              className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-white/15 px-4 py-1.5 text-sm font-semibold text-white hover:bg-white/25"
            >
              <RotateCcw size={14} aria-hidden="true" />
              Try again
            </button>
          </div>
        </div>
      )}

      {/* The big button: before the first play, while paused, and at the end. */}
      {!playing && !waiting && !failed && (
        <button
          type="button"
          onClick={toggle}
          aria-label={ended ? 'Play again' : 'Play video'}
          className="absolute inset-0 grid place-items-center"
        >
          <span className="grid h-16 w-16 place-items-center rounded-full bg-black/55 shadow-xl ring-1 ring-white/25 backdrop-blur-sm transition-transform hover:scale-105">
            {ended ? (
              <RotateCcw size={26} className="text-white" aria-hidden="true" />
            ) : (
              <Play size={28} className="ms-1 text-white" fill="currentColor" aria-hidden="true" />
            )}
          </span>
        </button>
      )}

      {/* The bar. */}
      <div
        className={cn(
          'absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 via-black/40 to-transparent px-3 pb-2.5 pt-10 transition-opacity duration-300',
          showBar ? 'opacity-100' : 'pointer-events-none opacity-0',
        )}
      >
        <div
          ref={trackRef}
          role="slider"
          tabIndex={-1}
          aria-label="Seek"
          aria-valuemin={0}
          aria-valuemax={Math.round(duration) || 0}
          aria-valuenow={Math.round(progress)}
          aria-valuetext={`${clock(progress)} of ${clock(duration)}`}
          onPointerDown={(event) => {
            event.currentTarget.setPointerCapture?.(event.pointerId)
            setDragging(true)
            seekFromPointer(event.clientX)
          }}
          onPointerMove={(event) => {
            if (dragging) seekFromPointer(event.clientX)
          }}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
          className="group/track relative mb-2 h-4 cursor-pointer touch-none"
        >
          <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 overflow-hidden rounded-full bg-white/20 transition-[height] group-hover/track:h-1.5">
            <div className="absolute inset-y-0 start-0 bg-white/35" style={{ width: `${loaded}%` }} />
            <div
              className="absolute inset-y-0 start-0 bg-gradient-to-r from-gold-soft to-gold"
              style={{ width: `${percent}%` }}
            />
          </div>
          <div
            className={cn(
              'absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow transition-opacity',
              dragging ? 'opacity-100' : 'opacity-0 group-hover/track:opacity-100',
            )}
            style={{ left: `${percent}%` }}
          />
        </div>

        <div className="flex items-center gap-3 text-white">
          <button
            type="button"
            onClick={toggle}
            aria-label={playing ? 'Pause' : 'Play'}
            className="opacity-90 hover:opacity-100"
          >
            {playing ? (
              <Pause size={18} fill="currentColor" aria-hidden="true" />
            ) : (
              <Play size={18} fill="currentColor" aria-hidden="true" />
            )}
          </button>
          <span className="text-xs tabular-nums text-white/85">
            {clock(progress)} <span className="text-white/50">/ {clock(duration)}</span>
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={cycleSpeed}
            aria-label={`Playback speed ${speed}x`}
            className="rounded-md px-1.5 py-0.5 text-xs font-semibold tabular-nums opacity-90 hover:bg-white/15 hover:opacity-100"
          >
            {speed}x
          </button>
          <button
            type="button"
            onClick={toggleMute}
            aria-label={muted ? 'Unmute' : 'Mute'}
            className="opacity-90 hover:opacity-100"
          >
            {muted ? <VolumeX size={18} aria-hidden="true" /> : <Volume2 size={18} aria-hidden="true" />}
          </button>
          {/* Volume, not just on/off. From a tablet up: on a phone the
              buttons on its side already do this, and the bar has no room. */}
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            onChange={(e) => {
              const video = ref.current
              if (!video) return
              const next = Number(e.target.value)
              video.volume = next
              video.muted = next === 0
              wake()
            }}
            aria-label="Volume"
            className="hidden h-1 w-20 cursor-pointer accent-[#E5B556] sm:block"
          />
          {onExpand && !full && (
            <button
              type="button"
              // The viewer opens its own player: this one stops first, or both
              // play and the sound comes twice.
              onClick={openViewer}
              aria-label="Open in the viewer"
              className="opacity-90 hover:opacity-100"
            >
              <Maximize2 size={16} aria-hidden="true" />
            </button>
          )}
          <button
            type="button"
            onClick={toggleFull}
            aria-label={full ? 'Exit full screen' : 'Full screen'}
            className="opacity-90 hover:opacity-100"
          >
            {full ? <Minimize size={18} aria-hidden="true" /> : <Maximize size={18} aria-hidden="true" />}
          </button>
        </div>
      </div>
    </div>
  )
}
