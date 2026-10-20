import { Component, type ReactNode } from 'react'

/** A page's code that could not be loaded - the site was updated since this tab opened. */
export function isStaleChunkError(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error)
  return /dynamically imported module|Importing a module script failed|Failed to fetch|ChunkLoadError|error loading dynamically/i.test(
    text,
  )
}

const RELOADED_KEY = 'kinjy.reloaded-for-update'

/**
 * Reload once to pick up the new version. Guarded per address, so a page that
 * really is broken shows its error instead of reloading forever.
 */
export function reloadForUpdate(): boolean {
  try {
    if (sessionStorage.getItem(RELOADED_KEY) === window.location.href) return false
    sessionStorage.setItem(RELOADED_KEY, window.location.href)
  } catch {
    /* no storage: reload anyway, once is all this call does */
  }
  window.location.reload()
  return true
}

/**
 * What a page shows when it fails, instead of a blank screen.
 *
 * A tab opened before a deployment asks for files that are no longer there
 * when it opens a page it had not opened yet; React then unmounts everything
 * and the member sees an empty, dark window. That case reloads by itself;
 * any other failure says so and offers a reload.
 */
export default class RouteErrorBoundary extends Component<{ children: ReactNode }, { error: unknown }> {
  state: { error: unknown } = { error: null }

  static getDerivedStateFromError(error: unknown) {
    return { error }
  }

  componentDidCatch(error: unknown) {
    if (isStaleChunkError(error)) reloadForUpdate()
  }

  render() {
    if (!this.state.error) return this.props.children
    const stale = isStaleChunkError(this.state.error)
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center px-6 text-center">
        <p className="text-lg font-bold text-text-hi">
          {stale ? 'Kinjy was just updated' : 'This page could not be shown'}
        </p>
        <p className="mt-2 max-w-md text-sm leading-relaxed text-text-low">
          {stale
            ? 'Reload to get the new version.'
            : 'Something went wrong while opening it. Reloading usually fixes it.'}
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink"
        >
          Reload
        </button>
      </div>
    )
  }
}
