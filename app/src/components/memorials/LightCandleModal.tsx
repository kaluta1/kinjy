import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import { Flame, X } from 'lucide-react'
import { CandleFlowerWidget } from '@/components/ui-kit'
import { lifeSpan } from '@/components/graveyard/format'
import { ApiError, kaluta, type Memorial } from '@/lib/api'

const cloudEase = [0.22, 1, 0.36, 1] as [number, number, number, number]

interface LitCandle {
  id: number
  name: string
}

/**
 * “Light a candle for someone” (memorials.md §7).
 *
 * The candle is real: the visitor finds a memorial that exists, and lighting it
 * adds a candle to that memorial for its family and every visitor to see — the
 * same one the QR page lights. No account is needed, and it is free.
 */
export default function LightCandleModal({
  open,
  onClose,
  onLight,
}: {
  open: boolean
  onClose: () => void
  lit: LitCandle[]
  /** Called with the name on the memorial once its candle is lit. */
  onLight: (name: string) => void
}) {
  const [query, setQuery] = useState('')
  // Results carry the query they answer, so an older answer is never shown for a newer one.
  const [found, setFound] = useState<{ q: string; items: Memorial[] } | null>(null)
  const [lighting, setLighting] = useState<string | null>(null)
  const [done, setDone] = useState<Memorial | null>(null)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef(0)
  const reduced = useReducedMotion()
  const q = query.trim()
  const results = found && found.q === q ? found.items : null

  const handleClose = () => {
    setQuery('')
    setFound(null)
    setDone(null)
    setError(null)
    onClose()
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    if (open) window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => {
    if (!open || q.length < 2) return
    const ticket = ++latest.current
    const timer = window.setTimeout(() => {
      kaluta.memorials
        .list({ q, limit: 6 })
        .then((page) => ticket === latest.current && setFound({ q, items: page.items }))
        .catch(() => ticket === latest.current && setFound({ q, items: [] }))
    }, 300)
    return () => window.clearTimeout(timer)
  }, [open, q])

  const light = async (memorial: Memorial) => {
    setLighting(memorial.id)
    setError(null)
    try {
      await kaluta.memorials.tribute(memorial.id, { kind: 'candle' })
      setDone(memorial)
      onLight(memorial.full_name)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not light the candle. Try again.')
    } finally {
      setLighting(null)
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.35 }}
          className="fixed inset-0 z-[80] flex items-center justify-center bg-[#060814]/70 p-6 backdrop-blur-sm"
          onClick={handleClose}
          role="dialog"
          aria-modal="true"
          aria-label="Light a candle for someone"
        >
          <motion.div
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: 24 }}
            transition={{ duration: 0.5, ease: cloudEase }}
            className="w-full max-w-md rounded-[20px] border border-[var(--kl-paper-2)] bg-[var(--kl-surface)] p-8 shadow-[0_40px_90px_-30px_rgba(0,0,0,0.6)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between">
              <h3 className="kl-serif text-[28px] font-semibold">Light a candle</h3>
              <button
                type="button"
                onClick={handleClose}
                aria-label="Close"
                className="rounded-full p-1.5 text-text-mid transition-colors hover:text-text-hi"
              >
                <X size={18} />
              </button>
            </div>

            {done ? (
              <div className="mt-6 text-center">
                <motion.div
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ duration: 0.6, ease: cloudEase }}
                  className="inline-block"
                >
                  <CandleFlowerWidget kind="candle" tier="free" name={done.full_name} />
                </motion.div>
                <p role="status" className="mt-2 text-sm text-text-hi">
                  Your candle is lit for {done.full_name}.
                </p>
                <p className="caption mt-1 !text-text-mid">Their family and every visitor can see it on the memorial.</p>
                <div className="mt-5 flex flex-wrap items-center justify-center gap-3">
                  <Link
                    to={`/memorial/${done.qr_code}`}
                    className="rounded-full border border-gold/40 px-5 py-2 text-sm font-semibold text-text-hi hover:border-gold/70"
                  >
                    Open the memorial
                  </Link>
                  <button
                    type="button"
                    onClick={() => {
                      setDone(null)
                      setQuery('')
                      setFound(null)
                    }}
                    className="rounded-full px-5 py-2 text-sm font-semibold text-text-mid hover:text-text-hi"
                  >
                    Light another
                  </button>
                </div>
              </div>
            ) : (
              <>
                <p className="caption mt-2 !text-text-mid">
                  Free, always. It appears on their memorial for the family and every visitor to see.
                </p>
                <label className="mt-6 block">
                  <span className="mono-data text-[0.68rem] tracking-[0.18em] text-gold-soft">IN MEMORY OF</span>
                  <input
                    autoFocus
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Type their name…"
                    maxLength={60}
                    aria-label="Find the memorial by name"
                    className="mt-2 w-full rounded-[10px] border border-[var(--kl-paper-2)] bg-[var(--kl-paper)] px-4 py-3 text-text-hi placeholder:text-text-low focus:border-[var(--kl-gold)] focus:outline-none"
                  />
                </label>

                <ul className="mt-3 max-h-56 space-y-1 overflow-y-auto pr-1 [scrollbar-width:thin]">
                  {q.length >= 2 && results === null && <li className="px-1 text-xs text-text-mid">Looking…</li>}
                  {results?.length === 0 && (
                    <li className="px-1 text-xs text-text-mid">
                      No public memorial by that name yet.{' '}
                      <Link to="/graveyard" className="text-text-hi underline underline-offset-2">
                        Create one
                      </Link>
                      .
                    </li>
                  )}
                  {(results ?? []).map((m) => (
                    <li key={m.id} className="flex items-center gap-3 rounded-[10px] px-2 py-2 hover:bg-[var(--kl-paper)]">
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm text-text-hi">{m.full_name}</span>
                        <span className="block text-xs text-text-mid">{lifeSpan(m.birth_date, m.death_date)}</span>
                      </span>
                      <button
                        type="button"
                        onClick={() => void light(m)}
                        disabled={lighting !== null}
                        aria-label={`Light a candle for ${m.full_name}`}
                        className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-3.5 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
                      >
                        <Flame size={12} aria-hidden="true" />
                        {lighting === m.id ? 'Lighting…' : 'Light'}
                      </button>
                    </li>
                  ))}
                </ul>
                {error && (
                  <p role="alert" className="mt-3 text-sm text-danger">
                    {error}
                  </p>
                )}
              </>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
