import { useState } from 'react'
import { AlertTriangle, Clock, Eye, Gavel, ShieldAlert } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import {
  ApiError,
  kaluta,
  type AppealItem,
  type ModerationOverview,
  type ReviewItem,
} from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * The Trust & Safety console.
 *
 * Two queues and the numbers that describe them. The numbers are the reason
 * this page is arranged the way it is: the overturn rate sits at the top, next
 * to the queue depth, because a reviewer who can see that a third of these
 * decisions are being overturned is being told something about the classifier,
 * not about the people appealing.
 *
 * The admin surface this replaces carried hard-coded figures — "4.2M items
 * screened / day", "96 open to humans" — which is worse than showing nothing,
 * because a fabricated queue depth looks exactly like a healthy one.
 *
 * Content is shown in the queue rather than linked from it. A reviewer who has
 * to open each item somewhere else works the queue slowly, and a queue nobody
 * works shows up as teenagers seeing an empty feed rather than as a backlog
 * anyone notices.
 */

const RATINGS = [
  'GENERAL',
  'TEEN_13_PLUS',
  'TEEN_16_PLUS',
  'ADULT_18_PLUS',
  'PROHIBITED',
] as const

function Stat({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: string | number
  tone?: 'warn' | 'bad'
  hint?: string
}) {
  return (
    <div className="cloud-card p-5">
      <p className="mono-data text-[0.68rem] font-bold uppercase tracking-[0.14em] text-text-low">{label}</p>
      <p
        className={cn(
          'mt-2 text-[2rem] font-bold leading-none tracking-[-0.03em] tabular-nums text-text-hi',
          tone === 'warn' && 'text-warning',
          tone === 'bad' && 'text-danger',
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-2 text-sm text-text-low">{hint}</p> : null}
    </div>
  )
}

export default function TrustSafety() {
  const overview = useApi<ModerationOverview>(() => kaluta.trustSafety.overview(), [])
  const appeals = useApi(() => kaluta.trustSafety.appeals(), [])
  const review = useApi(() => kaluta.trustSafety.reviewQueue(), [])

  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})

  const reloadAll = () => {
    overview.reload()
    appeals.reload()
    review.reload()
  }

  const run = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    try {
      await action()
      reloadAll()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not work.')
    } finally {
      setBusy(null)
    }
  }

  const stats = overview.data
  const appealItems: AppealItem[] = appeals.data?.items ?? []
  const reviewItems: ReviewItem[] = review.data?.items ?? []

  // An admin who is not staff gets a 403 from every call above. Say so plainly
  // rather than rendering three empty queues that look like a quiet day.
  const forbidden =
    overview.error?.includes('403') ||
    overview.error?.toLowerCase().includes('forbidden') ||
    overview.error?.toLowerCase().includes('admin') ||
    overview.error?.toLowerCase().includes('access required')

  return (
    <AppShell>
      <header className="mb-6">
        <p className="mono-data text-[0.72rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Trust &amp; Safety</p>
        <h1 className="mt-2 text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
          Review, appeal, correct
        </h1>
        <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          The review queue, the appeals queue, and how often we turn out to be wrong.{' '}
          <a href="/team" className="font-semibold text-gold-soft hover:underline">
            The team
          </a>
        </p>
      </header>
      <div className="space-y-10">
        {forbidden ? (
          <p role="alert" className="rounded-2xl bg-warning/10 px-5 py-4 text-sm text-text-hi">
            This console is for Trust &amp; Safety staff. Your account does not have access.
          </p>
        ) : null}

        {error ? (
          <p role="alert" className="rounded-2xl bg-danger/10 px-4 py-3 text-sm text-danger">
            {error}
          </p>
        ) : null}

        {stats ? (
          <section aria-labelledby="ts-numbers">
            <h2 id="ts-numbers" className="sr-only">
              Current numbers
            </h2>
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Stat
                label="Awaiting review"
                value={stats.pending_review}
                tone={stats.pending_review > 50 ? 'warn' : undefined}
                hint="Restricted until somebody looks"
              />
              <Stat
                label="Appeals open"
                value={stats.appeals.open}
                tone={stats.appeals.overdue > 0 ? 'warn' : undefined}
                hint={
                  stats.appeals.overdue > 0
                    ? `${stats.appeals.overdue} past their deadline`
                    : 'None overdue'
                }
              />
              <Stat
                label="Overturn rate"
                value={
                  stats.appeals.overturn_rate === null
                    ? '—'
                    : `${Math.round(stats.appeals.overturn_rate * 100)}%`
                }
                tone={
                  stats.appeals.overturn_rate !== null && stats.appeals.overturn_rate > 0.25
                    ? 'warn'
                    : undefined
                }
                hint={`of ${stats.appeals.answered} answered`}
              />
              <Stat
                label="Child-safety escalations"
                value={stats.child_safety_escalations}
                tone={stats.child_safety_escalations > 0 ? 'bad' : undefined}
                hint="Not handled on this page"
              />
            </div>
            <p className="mt-3 text-sm text-text-low">
              {stats.reports_24h} reports in the last 24 hours · {stats.reports_total} in
              total · {stats.classified_total} items classified
            </p>
          </section>
        ) : null}

        {stats && stats.child_safety_escalations > 0 ? (
          <p className="flex items-start gap-3 rounded-2xl bg-danger/10 px-5 py-4 text-sm leading-relaxed text-text-hi">
            <ShieldAlert className="mt-0.5 size-5 shrink-0 text-danger" aria-hidden />
            <span>
              {stats.child_safety_escalations} item
              {stats.child_safety_escalations === 1 ? '' : 's'} carry an exploitation signal.
              These are not appealable here and are not shown in either queue below — they
              go to the child-safety process, not to general review.
            </span>
          </p>
        ) : null}

        {/* --- Appeals ------------------------------------------------- */}
        <section aria-labelledby="ts-appeals" className="space-y-4">
          <h2 id="ts-appeals" className="flex items-center gap-2.5 text-[1.4rem] font-bold tracking-[-0.03em] text-text-hi">
            <Gavel className="size-5 shrink-0" aria-hidden />
            Appeals
            <span className="mono-data rounded-full bg-text-hi/[0.07] px-2.5 py-0.5 text-sm font-semibold text-text-mid">({appealItems.length})</span>
          </h2>

          {appeals.loading ? <p className="text-sm text-text-low" role="status">Loading…</p> : null}
          {!appeals.loading && appealItems.length === 0 ? (
            <p className="cloud-card px-6 py-8 text-center text-sm text-text-low">
              No open appeals.
            </p>
          ) : null}

          <ul className="space-y-3">
            {appealItems.map((a) => (
              <li
                key={a.id}
                className={cn('cloud-card space-y-4 p-5 md:p-6', a.overdue && '!border-warning/60')}
              >
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-full bg-text-hi/[0.07] px-3 py-1 font-semibold text-text-mid">
                    {a.content_kind ?? 'content'}
                  </span>
                  <span className="rounded-full bg-text-hi/[0.07] px-3 py-1 font-semibold text-text-mid">
                    {a.age_rating}
                  </span>
                  <span className="rounded-full bg-text-hi/[0.07] px-3 py-1 font-semibold text-text-mid">
                    {a.decided_by === 'automatic' ? 'decided automatically' : 'decided by a person'}
                  </span>
                  {a.overdue ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-3 py-1 font-semibold text-warning">
                      <Clock className="size-3 shrink-0" aria-hidden />
                      overdue
                    </span>
                  ) : (
                    <span className="text-text-low">
                      due {new Date(a.due_at).toLocaleDateString()}
                    </span>
                  )}
                </div>

                {a.body_snapshot ? (
                  <div>
                    <p className="mono-data text-[0.7rem] font-bold uppercase tracking-[0.15em] text-gold-soft">
                      {a.action === 'refused_publication' ? 'What was refused' : 'The content'}
                    </p>
                    <p className="mt-2 whitespace-pre-wrap rounded-2xl bg-text-hi/[0.05] p-4 text-[0.95rem] leading-relaxed text-text-hi">
                      {a.body_snapshot}
                    </p>
                  </div>
                ) : (
                  <p className="text-sm text-text-low">
                    The content is no longer available to read.
                  </p>
                )}

                <div>
                  <p className="mono-data text-[0.7rem] font-bold uppercase tracking-[0.15em] text-gold-soft">Their grounds</p>
                  <p className="mt-2 text-[0.95rem] text-text-mid">{a.grounds || <span className="text-text-low">None given.</span>}</p>
                </div>

                <input
                  value={notes[a.id] ?? ''}
                  onChange={(e) => setNotes((n) => ({ ...n, [a.id]: e.target.value }))}
                  placeholder="Why — the appellant is shown this"
                  className="w-full rounded-full border border-transparent bg-text-hi/[0.07] px-5 py-3 text-[0.95rem] text-text-hi placeholder:text-text-low focus:border-gold/50 focus:bg-transparent focus:outline-none"
                />

                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy === a.id}
                    onClick={() =>
                      run(a.id, () =>
                        kaluta.trustSafety.decideAppeal(a.id, true, notes[a.id] || undefined),
                      )
                    }
                    className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-50"
                  >
                    Overturn — remove the restriction
                  </button>
                  <button
                    type="button"
                    disabled={busy === a.id}
                    onClick={() =>
                      run(a.id, () =>
                        kaluta.trustSafety.decideAppeal(a.id, false, notes[a.id] || undefined),
                      )
                    }
                    className="rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-50"
                  >
                    Uphold
                  </button>
                </div>
                <p className="text-sm text-text-low">
                  You cannot answer an appeal against a decision you made yourself.
                </p>
              </li>
            ))}
          </ul>
        </section>

        {/* --- Review queue -------------------------------------------- */}
        <section aria-labelledby="ts-review" className="space-y-4">
          <h2 id="ts-review" className="flex items-center gap-2.5 text-[1.4rem] font-bold tracking-[-0.03em] text-text-hi">
            <Eye className="size-5 shrink-0" aria-hidden />
            Awaiting review
            <span className="mono-data rounded-full bg-text-hi/[0.07] px-2.5 py-0.5 text-sm font-semibold text-text-mid">
              ({review.data?.shown ?? 0} of {review.data?.pending ?? 0})
            </span>
          </h2>
          <p className="text-[0.95rem] leading-relaxed text-text-low">
            Everything here is restricted until it is rated, so a long queue costs reach
            rather than safety. Items carrying media cannot be rated by the classifier at
            all — there is no vision model — so they wait for a person.
          </p>

          {review.loading ? <p className="text-sm text-text-low" role="status">Loading…</p> : null}
          {!review.loading && reviewItems.length === 0 ? (
            <p className="cloud-card px-6 py-8 text-center text-sm text-text-low">
              Nothing waiting.
            </p>
          ) : null}

          <ul className="space-y-3">
            {reviewItems.map((item) => (
              <li key={item.content_id} className="cloud-card space-y-4 p-5 md:p-6">
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-full bg-text-hi/[0.07] px-3 py-1 font-semibold text-text-mid">
                    {item.content_kind}
                  </span>
                  <span className="rounded-full bg-text-hi/[0.07] px-3 py-1 font-semibold text-text-mid">
                    now {item.age_rating}
                  </span>
                  {item.media_count > 0 ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-3 py-1 font-semibold text-text-hi">
                      <AlertTriangle className="size-3 shrink-0" aria-hidden />
                      {item.media_count} media — unreadable by the classifier
                    </span>
                  ) : null}
                  {item.reports > 0 ? (
                    <span className="rounded-full bg-text-hi/[0.07] px-3 py-1 font-semibold text-text-mid">
                      {item.reports} report{item.reports === 1 ? '' : 's'}
                    </span>
                  ) : null}
                  <span className="text-text-low">
                    {item.classifier_source || 'unrated'} · confidence{' '}
                    {Math.round((item.confidence ?? 0) * 100)}%
                  </span>
                </div>

                <p className="whitespace-pre-wrap rounded-2xl bg-text-hi/[0.05] p-4 text-[0.95rem] leading-relaxed text-text-hi">
                  {item.body || <span className="text-text-low">No text — media only.</span>}
                </p>

                <div className="flex flex-wrap gap-2">
                  {RATINGS.map((rating) => (
                    <button
                      key={rating}
                      type="button"
                      disabled={busy === item.content_id}
                      onClick={() =>
                        run(item.content_id, () =>
                          kaluta.trustSafety.rate(item.content_id, { age_rating: rating }),
                        )
                      }
                      className="rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold capitalize text-text-mid transition-colors hover:border-gold/50 hover:bg-gold/10 hover:text-text-hi disabled:opacity-50"
                    >
                      {rating.replace(/_/g, ' ').toLowerCase()}
                    </button>
                  ))}
                </div>
                <p className="text-sm text-text-low">
                  Your rating outranks the classifier and will not be re-rated automatically.
                </p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </AppShell>
  )
}
