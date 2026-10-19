import { useCallback, useEffect, useState } from 'react'
import { Check, Shield, ShieldOff, UserMinus, X } from 'lucide-react'
import { ApiError, kaluta } from '@/lib/api'
import { useAppTheme } from '@/components/appdemo/theme'
import MemberAvatar from '@/components/social/MemberAvatar'
import { cn } from '@/lib/utils'

interface Member {
  user_id: string
  role: string
  status: string
  profile: { handle: string; display_name: string; avatar_url: string | null } | null
}

/**
 * Governance, on screen.
 *
 * A private community turned every request into a `pending` row and gave
 * nobody a way to answer it — so asking to join was a request into a void.
 * The endpoints exist now; this is the desk they sit on.
 *
 * Shown only to an owner or moderator: the API refuses anyone else, and a
 * panel that renders and then 403s on every action is worse than no panel.
 */
export default function MemberQueue({
  communityId,
  canModerate,
  onChanged,
  bare = false,
}: {
  communityId: string
  canModerate: boolean
  /** After an approval, a ban... - so the page's counts follow. */
  onChanged?: () => void
  /** Inside a dialog that already has the frame and the title. */
  bare?: boolean
}) {
  const { tok } = useAppTheme()
  const [members, setMembers] = useState<Member[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    if (!canModerate) return
    setLoading(true)
    kaluta.communities
      .members(communityId)
      .then((page) => setMembers(page.items))
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load members'))
      .finally(() => setLoading(false))
  }, [communityId, canModerate])

  useEffect(load, [load])

  if (!canModerate) return null

  const act = async (
    userId: string,
    action: 'approve' | 'reject' | 'ban' | 'unban' | 'promote' | 'demote',
  ) => {
    setBusy(userId)
    setError(null)
    try {
      await kaluta.communities.act(communityId, userId, action)
      load()
      onChanged?.()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not go through')
    } finally {
      setBusy(null)
    }
  }

  const pending = members.filter((m) => m.status === 'pending')
  const active = members.filter((m) => m.status === 'active')
  const banned = members.filter((m) => m.status === 'banned')

  const name = (m: Member) => m.profile?.display_name ?? `@${m.user_id.slice(0, 12)}`

  const Row = ({ member, children }: { member: Member; children: React.ReactNode }) => (
    <li className="flex items-center gap-2.5 py-1.5">
      <MemberAvatar
        handle={member.profile?.handle}
        displayName={member.profile?.display_name}
        avatarUrl={member.profile?.avatar_url ?? null}
        size={32}
      />
      <span className="min-w-0 flex-1">
        {/* Name only; the handle on hover. */}
        <span
          className={cn('block truncate text-sm', tok.text)}
          title={member.profile?.handle ? `@${member.profile.handle}` : undefined}
        >
          {name(member)}
        </span>
      </span>
      {member.role !== 'member' && (
        <span className="rounded-full bg-gold/15 px-2 py-0.5 text-[0.62rem] font-bold text-gold-soft">
          {member.role}
        </span>
      )}
      <span className="flex shrink-0 gap-1">{children}</span>
    </li>
  )

  const Action = ({
    onClick,
    label,
    icon: Icon,
    tone = 'neutral',
    disabled,
  }: {
    onClick: () => void
    label: string
    icon: typeof Check
    tone?: 'good' | 'bad' | 'neutral'
    disabled?: boolean
  }) => (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      aria-label={label}
      className={cn(
        'flex h-7 w-7 items-center justify-center rounded-full border disabled:opacity-40',
        tone === 'good' && 'border-success/40 text-success hover:bg-success/10',
        tone === 'bad' && 'border-warning/40 text-warning hover:bg-warning/10',
        tone === 'neutral' && cn('border-current/20', tok.low, tok.hoverBg),
      )}
    >
      <Icon size={13} aria-hidden="true" />
    </button>
  )

  return (
    <div className={cn(!bare && cn('rounded-card-lg p-4', tok.card))}>
      {!bare && <h3 className={cn('mb-3 text-sm font-semibold', tok.text)}>Members</h3>}

      {loading && <p className={cn('text-xs', tok.low)}>Loading…</p>}

      {!loading && pending.length > 0 && (
        <>
          <p className={cn('mb-1 text-xs font-semibold', tok.mid)}>
            Asking to join ({pending.length})
          </p>
          <ul className="mb-3 divide-y divide-current/10">
            {pending.map((m) => (
              <Row key={m.user_id} member={m}>
                <Action
                  label="Admit"
                  icon={Check}
                  tone="good"
                  disabled={busy === m.user_id}
                  onClick={() => void act(m.user_id, 'approve')}
                />
                <Action
                  label="Decline"
                  icon={X}
                  disabled={busy === m.user_id}
                  onClick={() => void act(m.user_id, 'reject')}
                />
              </Row>
            ))}
          </ul>
        </>
      )}

      {!loading && (
        <>
          <p className={cn('mb-1 text-xs font-semibold', tok.mid)}>
            Members ({active.length})
          </p>
          <ul className="divide-y divide-current/10">
            {active.map((m) => (
              <Row key={m.user_id} member={m}>
                {m.role === 'owner' ? (
                  // The owner is not actionable — not by a moderator, and not
                  // by themselves. The API refuses it too. Their badge already
                  // says who they are.
                  null
                ) : (
                  <>
                    <Action
                      label={m.role === 'moderator' ? 'Make a member' : 'Make a moderator'}
                      icon={m.role === 'moderator' ? ShieldOff : Shield}
                      disabled={busy === m.user_id}
                      onClick={() => void act(m.user_id, m.role === 'moderator' ? 'demote' : 'promote')}
                    />
                    <Action
                      label="Ban"
                      icon={UserMinus}
                      tone="bad"
                      disabled={busy === m.user_id}
                      onClick={() => void act(m.user_id, 'ban')}
                    />
                  </>
                )}
              </Row>
            ))}
          </ul>
        </>
      )}

      {banned.length > 0 && (
        <>
          <p className={cn('mb-1 mt-3 text-xs font-semibold', tok.mid)}>
            Banned ({banned.length})
          </p>
          <ul className="divide-y divide-current/10">
            {banned.map((m) => (
              <Row key={m.user_id} member={m}>
                <Action
                  label="Lift the ban"
                  icon={Check}
                  disabled={busy === m.user_id}
                  onClick={() => void act(m.user_id, 'unban')}
                />
              </Row>
            ))}
          </ul>
        </>
      )}

      {error && <p className="mt-2 text-xs text-amber-200">{error}</p>}
    </div>
  )
}
