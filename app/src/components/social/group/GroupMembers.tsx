import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router'
import { Ban, Crown, Flag, MoreHorizontal, Search, ShieldCheck, TreeDeciduous, UserMinus, UserPlus, X } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import MemberAvatar from '@/components/social/MemberAvatar'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { ApiError, kaluta, type Conversation, type GroupMember, type PersonBrief } from '@/lib/api'
import { FEATURES } from '@/lib/features'
import { cn } from '@/lib/utils'
import { ReportDialog } from './GroupBits'
import { ROLE_LABEL } from './groupText'

type Pending = GroupMember & { invited_by: string; created_at: string }

/**
 * Who is in the group, what each of them may do, and the ways in.
 *
 * Every button here is also refused by the server when it is not yours to
 * press; the list only leaves out what would fail.
 */
export default function GroupMembers({
  conversation,
  meId,
  onChanged,
}: {
  conversation: Conversation
  meId: string
  onChanged: () => void
}) {
  const { tok } = useAppTheme()
  const myRole = conversation.my_role ?? 'member'
  const runs = myRole === 'owner' || myRole === 'admin'
  const [members, setMembers] = useState<GroupMember[] | null>(null)
  const [total, setTotal] = useState(0)
  const [limited, setLimited] = useState(false)
  const [more, setMore] = useState(false)
  const [pending, setPending] = useState<Pending[]>([])
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<Array<PersonBrief & { user_id: string }>>([])
  const [relatives, setRelatives] = useState<Array<{ user_id: string; name: string }> | null>(null)
  const [menuFor, setMenuFor] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<{ kind: 'remove' | 'transfer' | 'block'; member: GroupMember } | null>(null)
  const [reporting, setReporting] = useState<GroupMember | null>(null)

  const load = useCallback(() => {
    kaluta.messages
      .participants(conversation.id)
      .then((page) => {
        setMembers(page.items)
        setTotal(page.members_count ?? page.total)
        setLimited(Boolean(page.limited))
        setMore(page.has_more)
      })
      .catch(() => setMembers([]))
    if (runs && !conversation.is_welcome) {
      kaluta.messages
        .pendingInvites(conversation.id)
        .then((page) => setPending(page.items))
        .catch(() => setPending([]))
    }
  }, [conversation.id, conversation.is_welcome, runs])
  useEffect(load, [load])

  useEffect(() => {
    const text = query.trim()
    if (text.length < 2) {
      setFound([])
      return
    }
    const timer = window.setTimeout(() => {
      void kaluta.people
        .search(text, 8)
        .then((page) => {
          const present = new Set((members ?? []).map((m) => m.user_id))
          setFound(page.items.filter((p) => !present.has(p.user_id)))
        })
        .catch(() => setFound([]))
    }, 200)
    return () => window.clearTimeout(timer)
  }, [query, members])

  const run = async (key: string, action: () => Promise<string | void>, failure: string) => {
    setBusy(key)
    setNote(null)
    try {
      const said = await action()
      if (said) setNote(said)
      load()
      onChanged()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : failure)
    } finally {
      setBusy(null)
      setMenuFor(null)
    }
  }

  const bringIn = (userId: string, name: string) =>
    run(
      userId,
      async () => {
        const result = await kaluta.messages.addMembers(conversation.id, [userId])
        setQuery('')
        if (result.invited?.includes(userId)) return `${name} was invited. They join when they accept.`
        if (result.added.includes(userId)) return `${name} was added.`
        return `${name} is already in the group.`
      },
      'Could not add them',
    )

  const loadRelatives = () => {
    setRelatives([])
    kaluta.family
      .persons(100)
      .then((page) => {
        const present = new Set((members ?? []).map((m) => m.user_id))
        setRelatives(
          page.items
            .filter((p) => p.user_id && p.user_id !== meId && !present.has(p.user_id) && !p.death_date && !p.deceased)
            .map((p) => ({ user_id: p.user_id as string, name: [p.given_name, p.family_name].filter(Boolean).join(' ') })),
        )
      })
      .catch(() => setRelatives([]))
  }

  // Admins remove moderators and members; only the owner removes an admin.
  const canRemove = (m: GroupMember) =>
    runs &&
    m.user_id !== meId &&
    !conversation.is_welcome &&
    m.role !== 'owner' &&
    (m.role !== 'admin' || myRole === 'owner')

  return (
    <div>
      {limited && (
        <p className={cn('mb-2 text-xs', tok.low)}>
          This group shows its member list to admins only. You see who runs it; it has {total} members.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <h4 className={cn('flex-1 text-xs font-semibold', tok.text)}>
          {total} {total === 1 ? 'member' : 'members'}
        </h4>
        {runs && !conversation.is_welcome && (
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            aria-expanded={adding}
            className="inline-flex items-center gap-1.5 rounded-full border border-sky/50 px-3 py-1 text-xs font-semibold text-sky hover:bg-sky/10"
          >
            <UserPlus size={12} aria-hidden="true" />
            Add or invite
          </button>
        )}
      </div>

      {adding && (
        <div className="mt-2 space-y-2">
          <p className={cn('text-xs', tok.low)}>
            Each person’s own settings decide: somebody who lets you add them is added; somebody who only accepts
            invitations gets one to accept or decline.
          </p>
          <label className="relative flex items-center">
            <Search size={14} className="pointer-events-none absolute start-3 text-text-low" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name…"
              aria-label="Search for someone to add or invite"
              className={cn('w-full rounded-full border border-[var(--cloud-border)] bg-transparent py-2 pe-3 ps-9 text-sm', tok.text)}
            />
          </label>
          <ul className="space-y-1">
            {found.map((person) => (
              <li key={person.user_id}>
                <button
                  type="button"
                  disabled={busy === person.user_id}
                  onClick={() => void bringIn(person.user_id, person.display_name)}
                  className="flex w-full items-center gap-2.5 rounded-xl p-2 text-start hover:bg-text-hi/[0.05] disabled:opacity-50"
                >
                  <MemberAvatar handle={person.handle} displayName={person.display_name} avatarUrl={person.avatar_url} size={32} />
                  <span className={cn('min-w-0 flex-1 truncate text-sm', tok.text)}>{person.display_name}</span>
                  <UserPlus size={14} className="shrink-0 text-sky" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
          {FEATURES.familyTreeApp && (
            <div>
              {relatives === null ? (
                <button
                  type="button"
                  onClick={loadRelatives}
                  className={cn('inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-3 py-1 text-xs font-semibold hover:border-gold/40', tok.mid)}
                >
                  <TreeDeciduous size={12} aria-hidden="true" />
                  Invite relatives from my family tree
                </button>
              ) : relatives.length === 0 ? (
                <p className={cn('text-xs', tok.low)}>No relative with a Kinjy account to invite.</p>
              ) : (
                <ul className="space-y-1">
                  {relatives.map((r) => (
                    <li key={r.user_id}>
                      <button
                        type="button"
                        disabled={busy === r.user_id}
                        onClick={() => void bringIn(r.user_id, r.name)}
                        className="flex w-full items-center gap-2.5 rounded-xl p-2 text-start hover:bg-text-hi/[0.05] disabled:opacity-50"
                      >
                        <TreeDeciduous size={14} className="shrink-0 text-gold-soft" aria-hidden="true" />
                        <span className={cn('min-w-0 flex-1 truncate text-sm', tok.text)}>{r.name}</span>
                        <UserPlus size={14} className="shrink-0 text-sky" aria-hidden="true" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <p className={cn('mt-1 text-[0.7rem]', tok.low)}>
                Only people in your own tree with a Kinjy account. Their settings still decide, and nothing about the
                tree is shown to the group.
              </p>
            </div>
          )}
        </div>
      )}

      {runs && pending.length > 0 && (
        <div className="mt-3">
          <h4 className={cn('text-xs font-semibold', tok.text)}>{pending.length} invited, not answered yet</h4>
          <ul className="mt-1 space-y-1">
            {pending.map((p) => (
              <li key={p.user_id} className="flex items-center gap-2">
                <MemberAvatar handle={p.handle ?? undefined} displayName={p.display_name} avatarUrl={p.avatar_url} size={28} />
                <span className={cn('min-w-0 flex-1 truncate text-xs', tok.text)}>{p.display_name}</span>
                <button
                  type="button"
                  disabled={busy === `cancel-${p.user_id}`}
                  onClick={() =>
                    void run(`cancel-${p.user_id}`, async () => {
                      await kaluta.messages.cancelInvite(conversation.id, p.user_id)
                      return 'Invitation withdrawn.'
                    }, 'Could not withdraw it')
                  }
                  className={cn('rounded-full px-2 py-1 text-xs hover:bg-text-hi/[0.07]', tok.low)}
                >
                  Withdraw
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ul className="mt-2 space-y-0.5">
        {(members ?? []).map((member) => {
          const self = member.user_id === meId
          const roleActions: Array<{ role: 'admin' | 'moderator' | 'member'; label: string }> = []
          if (!self && member.role !== 'owner') {
            if (myRole === 'owner') {
              if (member.role !== 'admin') roleActions.push({ role: 'admin', label: 'Make admin' })
              if (member.role === 'admin') roleActions.push({ role: 'member', label: 'Remove as admin' })
            }
            if (runs && member.role !== 'admin') {
              if (member.role !== 'moderator') roleActions.push({ role: 'moderator', label: 'Make moderator' })
              if (member.role === 'moderator') roleActions.push({ role: 'member', label: 'Remove as moderator' })
            }
          }
          const hasMenu = !self
          return (
            <li key={member.user_id} className="relative flex items-center gap-2.5 rounded-xl p-2">
              <MemberAvatar handle={member.handle ?? undefined} displayName={member.display_name} avatarUrl={member.avatar_url} size={38} />
              <span className="min-w-0 flex-1">
                <Link
                  to={member.handle ? `/u/${member.handle}` : '#'}
                  className={cn('block truncate text-sm font-semibold hover:underline', tok.text)}
                >
                  {member.display_name}
                  {self && <span className={cn('ms-1 font-normal', tok.low)}>(you)</span>}
                </Link>
                <span className="flex flex-wrap items-center gap-2">
                  {member.role !== 'member' && (
                    <span className="caption inline-flex items-center gap-1 text-gold-soft">
                      {member.role === 'owner' ? <Crown size={11} aria-hidden="true" /> : <ShieldCheck size={11} aria-hidden="true" />}
                      {ROLE_LABEL[member.role]}
                    </span>
                  )}
                  {member.blocked && (
                    <span className="caption inline-flex items-center gap-1 text-red-300">
                      <Ban size={11} aria-hidden="true" />
                      Blocked
                    </span>
                  )}
                </span>
              </span>
              {hasMenu && (
                <button
                  type="button"
                  aria-label={`Options for ${member.display_name}`}
                  aria-expanded={menuFor === member.user_id}
                  onClick={() => setMenuFor((v) => (v === member.user_id ? null : member.user_id))}
                  className={cn('rounded-full p-1.5 hover:bg-text-hi/[0.07]', tok.low)}
                >
                  <MoreHorizontal size={16} aria-hidden="true" />
                </button>
              )}
              {menuFor === member.user_id && (
                <div
                  role="menu"
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      e.stopPropagation()
                      setMenuFor(null)
                    }
                  }}
                  className={cn('absolute end-2 top-11 z-10 w-56 overflow-hidden rounded-xl border border-[var(--cloud-border)] shadow-xl', tok.card)}
                >
                  {roleActions.map((a) => (
                    <button
                      key={a.label}
                      type="button"
                      role="menuitem"
                      disabled={busy === member.user_id}
                      onClick={() =>
                        void run(member.user_id, async () => {
                          await kaluta.messages.setRole(conversation.id, member.user_id, a.role)
                        }, 'Could not change their role')
                      }
                      className={cn('flex w-full items-center gap-2 px-3 py-2 text-start text-sm hover:bg-text-hi/[0.05]', tok.text)}
                    >
                      <ShieldCheck size={14} aria-hidden="true" />
                      {a.label}
                    </button>
                  ))}
                  {myRole === 'owner' && !conversation.is_welcome && (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => setConfirm({ kind: 'transfer', member })}
                      className={cn('flex w-full items-center gap-2 px-3 py-2 text-start text-sm hover:bg-text-hi/[0.05]', tok.text)}
                    >
                      <Crown size={14} aria-hidden="true" />
                      Make them the owner
                    </button>
                  )}
                  {canRemove(member) && (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => setConfirm({ kind: 'remove', member })}
                      className="flex w-full items-center gap-2 px-3 py-2 text-start text-sm text-red-300 hover:bg-red-500/10"
                    >
                      <UserMinus size={14} aria-hidden="true" />
                      Remove from the group
                    </button>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => setConfirm({ kind: 'block', member })}
                    className={cn('flex w-full items-center gap-2 px-3 py-2 text-start text-sm hover:bg-text-hi/[0.05]', tok.text)}
                  >
                    <Ban size={14} aria-hidden="true" />
                    {member.blocked ? 'Unblock' : 'Block'}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setReporting(member)
                      setMenuFor(null)
                    }}
                    className={cn('flex w-full items-center gap-2 px-3 py-2 text-start text-sm hover:bg-text-hi/[0.05]', tok.text)}
                  >
                    <Flag size={14} aria-hidden="true" />
                    Report to Kinjy
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => setMenuFor(null)}
                    className={cn('flex w-full items-center gap-2 px-3 py-2 text-start text-sm hover:bg-text-hi/[0.05]', tok.low)}
                  >
                    <X size={14} aria-hidden="true" />
                    Close
                  </button>
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {more && (
        <button
          type="button"
          onClick={() =>
            kaluta.messages
              .participants(conversation.id, members?.length ?? 0)
              .then((page) => {
                setMembers((current) => [...(current ?? []), ...page.items])
                setMore(page.has_more)
              })
              .catch(() => setNote('Could not load more members'))
          }
          className={cn('mt-2 w-full rounded-xl border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold', tok.text, tok.hoverBg)}
        >
          Show more
        </button>
      )}
      {note && <p role="status" className={cn('mt-2 text-xs', tok.mid)}>{note}</p>}

      <ConfirmDialog
        open={confirm?.kind === 'remove'}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={`Remove ${confirm?.member.display_name ?? ''}?`}
        description="They leave the group at once and stop receiving its messages. The group is told. They can come back only if somebody adds or invites them, or through the group’s link."
        confirmLabel="Remove from the group"
        onConfirm={() => {
          const m = confirm?.member
          setConfirm(null)
          if (m) void run(m.user_id, async () => {
            await kaluta.messages.removeMember(conversation.id, m.user_id)
            return `${m.display_name} was removed.`
          }, 'Could not remove them')
        }}
      />
      <ConfirmDialog
        open={confirm?.kind === 'transfer'}
        onOpenChange={(open) => !open && setConfirm(null)}
        title={`Make ${confirm?.member.display_name ?? ''} the owner?`}
        description="They will be able to do everything you can, including removing you, appointing admins and deleting the group. You stay in the group as an admin. Only the new owner can hand it back."
        confirmLabel="Hand the group on"
        onConfirm={() => {
          const m = confirm?.member
          setConfirm(null)
          if (m) void run(m.user_id, async () => {
            await kaluta.messages.transferOwnership(conversation.id, m.user_id)
            return `${m.display_name} now owns the group. You are an admin.`
          }, 'Could not hand the group on')
        }}
      />
      <ConfirmDialog
        open={confirm?.kind === 'block'}
        onOpenChange={(open) => !open && setConfirm(null)}
        destructive={!confirm?.member.blocked}
        title={confirm?.member.blocked ? `Unblock ${confirm?.member.display_name ?? ''}?` : `Block ${confirm?.member.display_name ?? ''}?`}
        description={
          confirm?.member.blocked
            ? 'They will be able to message you, invite you and add you to groups again, as your settings allow.'
            : 'Everywhere on Kinjy: they cannot message you, invite you or add you to a group, and their messages in shared groups are folded away for you. They are not told. You both stay in this group unless one of you leaves.'
        }
        confirmLabel={confirm?.member.blocked ? 'Unblock' : 'Block'}
        onConfirm={() => {
          const m = confirm?.member
          setConfirm(null)
          if (m) void run(m.user_id, async () => {
            if (m.blocked) await kaluta.people.unblock(m.user_id)
            else await kaluta.people.block(m.user_id)
            return m.blocked ? `${m.display_name} is unblocked.` : `${m.display_name} is blocked.`
          }, 'Could not change the block')
        }}
      />
      {reporting && (
        <ReportDialog
          conversationId={conversation.id}
          target={{ kind: 'member', id: reporting.user_id, label: reporting.display_name }}
          onClose={() => setReporting(null)}
        />
      )}
    </div>
  )
}
