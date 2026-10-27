/**
 * One audible player at a time.
 *
 * Opening a post's video in the viewer starts a second, unmuted player of
 * the same file while the card's player - unmuted earlier by the member - can
 * still be running behind it: the same narration twice, a fraction of a
 * second apart, which is heard as an echo. So whenever a video or audio
 * element starts (or is unmuted) with sound, every other element playing with
 * sound is paused. Muted autoplay in the feed is left alone: it is silent.
 *
 * Installed once, on the document, so it covers every player on the site -
 * ours and plain <video controls> alike.
 */
function audible(el: HTMLMediaElement): boolean {
  return !el.paused && !el.muted && el.volume > 0
}

function silenceOthers(event: Event): void {
  const current = event.target
  if (!(current instanceof HTMLMediaElement) || !audible(current)) return
  for (const other of Array.from(document.querySelectorAll<HTMLMediaElement>('video, audio'))) {
    if (other !== current && audible(other)) other.pause()
  }
}

let installed = false

export function installSingleAudio(): void {
  if (installed || typeof document === 'undefined') return
  installed = true
  // Media events do not bubble: listened for in the capture phase.
  document.addEventListener('play', silenceOthers, true)
  document.addEventListener('volumechange', silenceOthers, true)
}
