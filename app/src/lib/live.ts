/**
 * A one-line pub/sub for "something changed, anyone showing it should refresh".
 *
 * Following someone updates a count in the profile card, empties a row from the
 * suggestions list and changes what the connections tab shows — three
 * components with no parent in common. Threading callbacks through the tree for
 * that is more plumbing than the problem deserves, and a data-fetching library
 * is a dependency this app does not otherwise need.
 */
export type LiveTopic = 'profile' | 'connections' | 'feed' | 'circles' | 'pins'

export function announce(topic: LiveTopic): void {
  window.dispatchEvent(new CustomEvent('kaluta:changed', { detail: topic }))
}

export function onChange(topic: LiveTopic, handler: () => void): () => void {
  const listener = (event: Event) => {
    if ((event as CustomEvent<LiveTopic>).detail === topic) handler()
  }
  window.addEventListener('kaluta:changed', listener)
  return () => window.removeEventListener('kaluta:changed', listener)
}
