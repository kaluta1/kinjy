import { useState } from 'react'
import { Link } from 'react-router'
import { BellOff, EyeOff, LogOut } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type AudiencePrefRow } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Private audiences you receive: every choice you made about somebody's circle
 * (mute, hide, opt out), to review or undo. A circle is shown by a label that
 * only you see — never its name or who else is in it.
 */
export default function AudienceSettings() {
  const prefs = useApi(() => kaluta.posts.audiencePrefs(), [])
  // Changes made here, laid over what the server sent; null = no longer listed.
  const [changed, setChanged] = useState<Record<number, Partial<AudiencePrefRow> | null>>({})
  const items = (prefs.data?.items ?? [])
    .filter((r) => changed[r.id] !== null)
    .map((r) => ({ ...r, ...changed[r.id] }))
  const [error, setError] = useState<string | null>(null)

  const change = async (row: AudiencePrefRow, patch: { muted?: boolean; hidden?: boolean; opt_out?: boolean }) => {
    setError(null)
    try {
      const result = await kaluta.posts.changeAudiencePref(row.id, patch)
      setChanged((all) => ({ ...all, [row.id]: 'removed' in result ? null : result }))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the change')
    }
  }

  const toggle = (on: boolean) =>
    cn(
      'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold',
      on ? 'bg-gold/20 text-gold-soft' : 'text-text-mid hover:bg-text-hi/[0.07]',
    )

  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-[clamp(32px,4vw,46px)] font-bold leading-[1.05] tracking-[-0.04em] text-text-hi">Private audiences</h1>
        <p className="mt-3 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          When someone shares a post with one of their circles, you can mute it, hide it from your feeds or opt out. Each
          choice is listed here and can be undone. The author is never told; you never see the circle’s name or who else is
          in it. To make your own circles, go to{' '}
          <Link to="/circles" className="font-semibold text-gold-soft hover:underline">
            Circles
          </Link>
          .
        </p>
      </header>
      <section className="cloud-card p-5 md:p-7">
        {prefs.loading && !prefs.data && <p className="text-sm text-text-mid">Loading…</p>}
        {prefs.error && <p className="text-sm text-warning">{prefs.error}</p>}
        {prefs.data && items.length === 0 && (
          <p className="text-sm leading-relaxed text-text-mid">
            Nothing here yet. On a post shared with you privately, tap its “Private audience” label to manage it.
          </p>
        )}
        <ul className="divide-y divide-[var(--cloud-border)]">
          {items.map((row) => (
            <li key={row.id} className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center">
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <MemberAvatar
                  handle={row.author?.handle ?? null}
                  displayName={row.author?.display_name ?? null}
                  avatarUrl={row.author?.avatar_url ?? null}
                  size={40}
                />
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-text-hi">{row.label}</p>
                  {row.opted_out && (
                    <p className="caption">
                      Opted out{row.opted_out_at ? ` on ${new Date(row.opted_out_at).toLocaleDateString()}` : ''}
                      {row.opt_out_past ? ', past posts included' : ', from then on'}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <button type="button" aria-pressed={row.muted} onClick={() => void change(row, { muted: !row.muted })} className={toggle(row.muted)}>
                  <BellOff size={12} aria-hidden="true" /> {row.muted ? 'Muted' : 'Mute'}
                </button>
                <button type="button" aria-pressed={row.hidden} onClick={() => void change(row, { hidden: !row.hidden })} className={toggle(row.hidden)}>
                  <EyeOff size={12} aria-hidden="true" /> {row.hidden ? 'Hidden' : 'Hide'}
                </button>
                {row.opted_out && (
                  <button type="button" onClick={() => void change(row, { opt_out: false })} className={toggle(true)}>
                    <LogOut size={12} aria-hidden="true" /> Opt back in
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
        {error && (
          <p role="alert" className="mt-3 text-sm text-danger">
            {error}
          </p>
        )}
      </section>
    </AppShell>
  )
}
