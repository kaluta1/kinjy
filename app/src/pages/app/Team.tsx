import { useState } from 'react'
import { Link } from 'react-router'
import { Crown, Gavel, ShieldCheck, Trash2, UserPlus } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta, type StaffRole } from '@/lib/api'
import { cn } from '@/lib/utils'

const ROLES: Array<{ id: StaffRole; label: string; what: string; icon: typeof Crown }> = [
  {
    id: 'moderator',
    label: 'Moderator',
    what: 'Reviews posts, decides appeals, handles memorial death reports and contact-risk cases. No money, no accounts.',
    icon: Gavel,
  },
  {
    id: 'admin',
    label: 'Admin',
    what: 'The whole console: moderation, payments and payouts, KYC, disputes, ads, the ledger.',
    icon: ShieldCheck,
  },
  {
    id: 'superadmin',
    label: 'Superadmin',
    what: 'Everything an admin does, and decides who is on this team.',
    icon: Crown,
  },
]

/**
 * Who runs Kinjy, and how far each person reaches.
 *
 * Every staff member can see the list; only a superadmin changes it. A new
 * role reaches the person within a few minutes (their next token refresh)
 * without them signing out. The server refuses to leave Kinjy without a
 * superadmin, and nobody changes their own role.
 */
export default function Team() {
  const { user } = useAuth()
  const team = useApi(() => kaluta.team.list(), [])
  const [who, setWho] = useState('')
  const [role, setRole] = useState<StaffRole>('moderator')
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const canManage = Boolean(team.data?.can_manage)

  const run = async (key: string, action: () => Promise<unknown>, done: string) => {
    setBusy(key)
    setNote(null)
    try {
      await action()
      setNote({ ok: true, text: done })
      team.reload()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not work' })
    } finally {
      setBusy(null)
    }
  }

  const forbidden = team.error && /403|access required|forbidden/i.test(team.error)

  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-[clamp(32px,4.5vw,48px)] font-bold leading-[1.05] tracking-[-0.04em] text-text-hi">
          The Kinjy team
        </h1>
        <p className="mt-2 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          Who can use the console, and how far. A change reaches the person within a few minutes, without them
          signing out.{' '}
          <Link to="/trust-safety" className="font-semibold text-gold-soft hover:underline">
            Open the review console
          </Link>
        </p>
      </header>

      {forbidden ? (
        <p role="alert" className="rounded-2xl bg-warning/10 px-5 py-4 text-sm text-text-hi">
          This page is for Kinjy staff. Your account does not have access.
        </p>
      ) : (
        <div className="space-y-6">
          <section className="grid gap-3 sm:grid-cols-3">
            {ROLES.map((r) => (
              <div key={r.id} className="cloud-card p-4">
                <p className="flex items-center gap-2 text-sm font-semibold text-text-hi">
                  <r.icon size={16} className="text-gold-soft" aria-hidden="true" />
                  {r.label}
                </p>
                <p className="mt-1.5 text-xs leading-relaxed text-text-low">{r.what}</p>
              </div>
            ))}
          </section>

          {canManage && (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                if (!who.trim()) return
                void run(
                  'add',
                  async () => {
                    await kaluta.team.add(who.trim(), role)
                    setWho('')
                  },
                  `${who.trim()} is now ${ROLES.find((r) => r.id === role)?.label.toLowerCase()}.`,
                )
              }}
              className="cloud-card flex flex-wrap items-end gap-3 p-4"
            >
              <label className="min-w-[220px] flex-1">
                <span className="mb-1 block text-xs font-semibold text-text-mid">Email or username</span>
                <input
                  value={who}
                  onChange={(e) => setWho(e.target.value)}
                  placeholder="name@example.com or @username"
                  className="w-full rounded-full border border-[var(--cloud-border)] bg-transparent px-4 py-2 text-sm text-text-hi focus:border-gold/50 focus:outline-none"
                />
              </label>
              <label>
                <span className="mb-1 block text-xs font-semibold text-text-mid">Role</span>
                <select
                  value={role}
                  onChange={(e) => setRole(e.target.value as StaffRole)}
                  className="rounded-full border border-[var(--cloud-border)] bg-transparent px-4 py-2 text-sm text-text-hi"
                >
                  {ROLES.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="submit"
                disabled={busy === 'add' || !who.trim()}
                className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-40"
              >
                <UserPlus size={15} aria-hidden="true" />
                Add to the team
              </button>
            </form>
          )}

          {note && (
            <p role="status" className={cn('text-sm', note.ok ? 'text-success' : 'text-danger')}>
              {note.text}
            </p>
          )}

          <section className="cloud-card divide-y divide-[var(--cloud-border)]">
            {team.loading && <p className="p-4 text-sm text-text-low">Loading…</p>}
            {team.error && !forbidden && <p className="p-4 text-sm text-danger">{team.error}</p>}
            {(team.data?.items ?? []).map((member) => {
              const self = member.user_id === user?.id
              return (
                <div key={member.user_id} className="flex flex-wrap items-center gap-3 p-4">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold text-text-hi">
                      {member.display_name}
                      {self && <span className="ms-2 text-xs font-normal text-text-low">(you)</span>}
                    </p>
                    <p className="truncate text-xs text-text-low">
                      @{member.handle} · {member.email}
                    </p>
                  </div>
                  {canManage && !self ? (
                    <>
                      <select
                        value={member.role}
                        disabled={busy === member.user_id}
                        onChange={(e) =>
                          void run(
                            member.user_id,
                            () => kaluta.team.change(member.user_id, e.target.value as StaffRole),
                            `${member.display_name} is now ${e.target.value}.`,
                          )
                        }
                        aria-label={`Role of ${member.display_name}`}
                        className="rounded-full border border-[var(--cloud-border)] bg-transparent px-3 py-1.5 text-sm text-text-hi"
                      >
                        {ROLES.map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        disabled={busy === member.user_id}
                        onClick={() =>
                          void run(
                            member.user_id,
                            () => kaluta.team.remove(member.user_id),
                            `${member.display_name} is an ordinary member again.`,
                          )
                        }
                        aria-label={`Take ${member.display_name} off the team`}
                        className="rounded-full p-2 text-text-low hover:bg-danger/10 hover:text-danger"
                      >
                        <Trash2 size={15} aria-hidden="true" />
                      </button>
                    </>
                  ) : (
                    <span className="rounded-full bg-gold/15 px-3 py-1 text-xs font-semibold capitalize text-gold-soft">
                      {member.role}
                    </span>
                  )}
                </div>
              )
            })}
          </section>
        </div>
      )}
    </AppShell>
  )
}
