import { useState } from 'react'
import { Clock, Eye, EyeOff, Link2, X } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type SupervisedView, type SupervisionState } from '@/lib/api'
import { FEATURES } from '@/lib/features'
import { cn } from '@/lib/utils'

/**
 * Parental supervision, for both people in it.
 *
 * The page is built around the disclosure rather than around the controls. What
 * a parent cannot see is shown at the same size as what they can, and it is
 * shown before either side agrees to anything — a limit nobody reads is a limit
 * the teenager has no reason to believe in.
 */

const SETTING_LABELS: Record<string, string> = {
  who_can_message: 'Who can message you',
  who_can_invite: 'Who can send you invitations',
  who_can_add_family: 'Who can add you to a family tree',
  who_can_add_community: 'Who can add you to a community',
  who_can_add_group: 'Who can add you to a group chat',
  who_can_invite_group: 'Who can invite you to a group chat',
  who_can_see_family: 'Who can see your family tree',
  discoverable: 'Appear in search and suggestions',
}

function label(setting: string) {
  return SETTING_LABELS[setting] ?? setting.replace(/_/g, ' ')
}

/** Settings about the family tree, listed only while that feature is open. */
const FAMILY_SETTINGS = new Set(['who_can_add_family', 'who_can_see_family', 'family_tree_shared'])

function shown(setting: string) {
  return FEATURES.familyTreeApp || !FAMILY_SETTINGS.has(setting)
}

export default function Supervision() {
  const state = useApi<SupervisionState>(() => kaluta.supervision.mine(), [])
  const [handle, setHandle] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [detail, setDetail] = useState<SupervisedView | null>(null)

  // `keepDetail` exists because the parent's detail panel is itself opened
  // through `run`: clearing it unconditionally closed the panel in the same tick
  // it was opened, and the button looked like it did nothing at all.
  const run = async (action: () => Promise<unknown>, keepDetail = false) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      state.reload()
      if (!keepDetail) setDetail(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not work. Try again.')
    } finally {
      setBusy(false)
    }
  }

  const disclosure = state.data?.disclosure
  const links = state.data?.items ?? []
  const requests = state.data?.requests ?? []
  const active = links.find((l) => l.status === 'active')
  const invites = links.filter((l) => l.status === 'invited')

  const eyebrow = 'mono-data text-[0.7rem] font-bold uppercase tracking-[0.15em] text-gold-soft'
  const card = 'cloud-card p-5 md:p-6'
  const primary =
    'rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink shadow-[0_8px_20px_-10px_rgba(166,120,57,0.6)] disabled:opacity-50'
  const quiet =
    'rounded-full border border-[var(--cloud-border)] px-5 py-2.5 text-sm font-semibold text-text-mid transition-colors hover:border-gold/50 hover:text-text-hi disabled:opacity-50'
  const section = 'text-[1.4rem] font-bold leading-tight tracking-[-0.03em] text-text-hi'

  return (
    <AppShell>
      <div className="mx-auto max-w-3xl space-y-10 py-2">
        <header>
          <p className={eyebrow}>Supervision</p>
          <h1 className="mt-2 flex items-center gap-3 text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
            Safer, together
          </h1>
          <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
            An adult can help keep an account under 18 safer. Both people have to agree,
            either one can end it, and the other is told when they do.
          </p>
        </header>

        {error ? (
          <p role="alert" className="rounded-2xl bg-red-500/10 px-4 py-3 text-sm text-red-200">
            {error}
          </p>
        ) : null}

        {/* The agreement, before the controls. */}
        {disclosure ? (
          <section className="grid grid-cols-[minmax(0,1fr)] gap-4 sm:grid-cols-2">
            <div className={card}>
              <h2 className="mb-4 flex items-center gap-2.5 text-[1.05rem] font-bold tracking-[-0.02em] text-text-hi">
                <span className="grid h-9 w-9 place-items-center rounded-[10px] bg-emerald-500/15 text-emerald-300">
                  <Eye className="size-[18px] shrink-0" aria-hidden />
                </span>
                A supervising adult sees
              </h2>
              <ul className="space-y-2.5 text-[0.92rem] leading-relaxed text-text-mid">
                {disclosure.can_see.map((line) => (
                  <li key={line} className="flex gap-2.5">
                    <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
                    {line}
                  </li>
                ))}
              </ul>
            </div>
            <div className={card}>
              <h2 className="mb-4 flex items-center gap-2.5 text-[1.05rem] font-bold tracking-[-0.02em] text-text-hi">
                <span className="grid h-9 w-9 place-items-center rounded-[10px] bg-gold/20 text-gold-soft">
                  <EyeOff className="size-[18px] shrink-0" aria-hidden />
                </span>
                They never see
              </h2>
              <ul className="space-y-2.5 text-[0.92rem] leading-relaxed text-text-mid">
                {disclosure.cannot_see.map((line) => (
                  <li key={line} className="flex gap-2.5">
                    <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-gold" />
                    {line}
                  </li>
                ))}
              </ul>
            </div>
            <p className="rounded-2xl bg-text-hi/[0.05] px-5 py-4 text-sm leading-relaxed text-text-mid sm:col-span-2">
              {disclosure.note}
            </p>
          </section>
        ) : null}

        {/* Invitations waiting on this member. */}
        {invites.length ? (
          <section className="space-y-4">
            <h2 className={section}>Waiting for an answer</h2>
            {invites.map((link) => (
              <div key={link.id} className={cn(card, 'flex flex-wrap items-center justify-between gap-4')}>
                <p className="text-[0.95rem] text-text-hi">
                  {link.role === 'teen'
                    ? 'An adult asked to supervise this account.'
                    : 'You asked to supervise an account. Waiting for them to accept.'}
                </p>
                {link.role === 'teen' ? (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => run(() => kaluta.supervision.answer(link.id, true))}
                      className={primary}
                    >
                      Accept
                    </button>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => run(() => kaluta.supervision.answer(link.id, false))}
                      className={quiet}
                    >
                      Decline
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => run(() => kaluta.supervision.end(link.id))}
                    className={quiet}
                  >
                    Withdraw
                  </button>
                )}
              </div>
            ))}
          </section>
        ) : null}

        {/* The active link. */}
        {active ? (
          <section className="space-y-4">
            <h2 className={section}>
              {active.role === 'parent' ? 'An account you supervise' : 'Supervision is on'}
            </h2>
            <div className={cn(card, 'space-y-4')}>
              <p className="flex items-center gap-2 text-sm text-text-low">
                <span aria-hidden="true" className="h-2 w-2 rounded-full bg-emerald-400" />
                Started {new Date(active.accepted_at ?? active.created_at).toLocaleDateString()}.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                {active.role === 'parent' ? (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      run(async () => setDetail(await kaluta.supervision.view(active.id)), true)
                    }
                    className={primary}
                  >
                    Open what I can see
                  </button>
                ) : null}
                <button type="button" disabled={busy} onClick={() => run(() => kaluta.supervision.end(active.id))} className={cn(quiet, 'inline-flex items-center gap-1.5')}>
                  <X className="size-4 shrink-0" aria-hidden />
                  End supervision
                </button>
              </div>
              {active.role === 'teen' ? (
                <p className="text-sm leading-relaxed text-text-low">
                  Ending it does not change what your age already restricts, and the adult is
                  told that it ended.
                </p>
              ) : null}
            </div>
          </section>
        ) : null}

        {/* The parent's view, once opened. */}
        {detail ? (
          <section className="space-y-4">
            <h2 className={section}>Everything you can see</h2>
            <div className={cn(card, 'space-y-6')}>
              <div>
                <h3 className="mb-2 flex items-center gap-2 text-[1.05rem] font-bold tracking-[-0.02em] text-text-hi">
                  <Clock className="size-[18px] shrink-0 text-gold-soft" aria-hidden />
                  Time today
                </h3>
                <p className="text-[0.95rem] text-text-mid">
                  {Math.round(detail.time.minutes_today)} minutes used
                  {detail.time.daily_limit_minutes
                    ? ` of a ${detail.time.daily_limit_minutes}-minute limit.`
                    : ', with no limit set.'}
                </p>
                <label className="mt-3 flex items-center gap-2.5 text-sm text-text-mid">
                  Daily limit
                  <input
                    type="number"
                    min={0}
                    max={1440}
                    defaultValue={detail.time.daily_limit_minutes ?? ''}
                    onBlur={(e) =>
                      run(
                        async () => {
                          await kaluta.supervision.setTimeLimit(
                            detail.supervision.id,
                            e.target.value === '' ? null : Number(e.target.value),
                          )
                          setDetail(await kaluta.supervision.view(detail.supervision.id))
                        },
                        true,
                      )
                    }
                    className="w-24 rounded-xl border border-transparent bg-text-hi/[0.07] px-3 py-2 text-text-hi focus:border-gold/50 focus:bg-transparent focus:outline-none"
                  />
                  minutes
                </label>
              </div>

              <div className="border-t border-[var(--cloud-border)] pt-5">
                <h3 className="mb-3 text-[1.05rem] font-bold tracking-[-0.02em] text-text-hi">Safety settings</h3>
                <ul className="divide-y divide-[var(--cloud-border)] text-[0.92rem]">
                  {Object.entries(detail.settings).filter(([key]) => shown(key)).map(([key, value]) => (
                    <li key={key} className="flex items-center justify-between gap-4 py-2.5">
                      <span className="text-text-mid">{label(key)}</span>
                      <strong className="shrink-0 rounded-full bg-text-hi/[0.07] px-3 py-1 text-xs font-semibold text-text-hi">
                        {String(value)}
                      </strong>
                    </li>
                  ))}
                </ul>
              </div>

              <div className="border-t border-[var(--cloud-border)] pt-5">
                <h3 className="mb-3 text-[1.05rem] font-bold tracking-[-0.02em] text-text-hi">Not included, by design</h3>
                <ul className="space-y-2 text-[0.92rem] leading-relaxed text-text-low">
                  {detail.not_included.map((line) => (
                    <li key={line} className="flex gap-2.5">
                      <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-text-low" />
                      {line}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </section>
        ) : null}

        {/* Requests, for whichever side is looking. */}
        {requests.length ? (
          <section className="space-y-4">
            <h2 className={section}>Requests</h2>
            <ul className="space-y-3">
              {requests.map((r) => (
                <li key={r.id} className={cn(card, 'flex flex-wrap items-center justify-between gap-4')}>
                  <div>
                    <p className="text-[0.95rem] text-text-hi">
                      {label(r.setting)} → <strong>{r.requested_value}</strong>
                    </p>
                    <p
                      className={cn(
                        'mt-1 text-sm',
                        r.status === 'approved' ? 'text-emerald-300' : r.status === 'declined' ? 'text-red-200' : 'text-text-low',
                      )}
                    >
                      {r.status === 'pending'
                        ? r.role === 'parent'
                          ? 'Waiting for you'
                          : 'Waiting for the adult supervising this account'
                        : r.status === 'approved'
                          ? 'Approved'
                          : 'Declined'}
                    </p>
                  </div>
                  {r.status === 'pending' && r.role === 'parent' ? (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => kaluta.supervision.answerRequest(r.id, true))}
                        className={primary}
                      >
                        Allow
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => run(() => kaluta.supervision.answerRequest(r.id, false))}
                        className={quiet}
                      >
                        Decline
                      </button>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {/* Starting one. Either side may, and the ages decide the roles. */}
        {active ? null : (
          <section className="space-y-4">
            <h2 className={section}>Start supervision</h2>
            <form
              onSubmit={(e) => {
                e.preventDefault()
                if (handle.trim()) run(() => kaluta.supervision.invite(handle.trim()))
              }}
              className={cn(card, 'space-y-4')}
            >
              <label className="block">
                <span className="mb-2 flex items-center gap-2 text-sm font-semibold text-text-mid">
                  <Link2 className="size-4 shrink-0" aria-hidden />
                  Their username
                </span>
                <div className="flex flex-wrap gap-2">
                  <input
                    value={handle}
                    onChange={(e) => setHandle(e.target.value)}
                    placeholder="username"
                    className="min-w-40 flex-1 rounded-full border border-transparent bg-text-hi/[0.07] px-5 py-3 text-[0.95rem] text-text-hi placeholder:text-text-low focus:border-gold/50 focus:bg-transparent focus:outline-none"
                  />
                  <button type="submit" disabled={busy || !handle.trim()} className={primary}>
                    Send invitation
                  </button>
                </div>
              </label>
              <p className="text-sm leading-relaxed text-text-low">
                Whoever is under 18 becomes the supervised account, whichever of you sends the
                invitation.
              </p>
            </form>
          </section>
        )}

        {state.loading ? <p className="text-sm text-text-low" role="status">Loading…</p> : null}
      </div>
    </AppShell>
  )
}
