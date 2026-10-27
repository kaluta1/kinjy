import { useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { ArrowLeft, ScrollText, UsersRound } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta } from '@/lib/api'

/**
 * Invitations to group chats, each with what the group is for and its rules,
 * answered here. Nobody is in a group they were invited to until they say yes.
 */
export default function MessagesInvites() {
  const navigate = useNavigate()
  const invites = useApi(() => kaluta.messages.myInvites(), [])
  const [busy, setBusy] = useState<number | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const answer = async (id: number, action: 'accept' | 'decline') => {
    setBusy(id)
    setNote(null)
    try {
      const result = await kaluta.messages.answerInvite(id, action)
      if (action === 'accept' && result.conversation_id) {
        navigate(`/messages?c=${result.conversation_id}`)
        return
      }
      setNote('Invitation declined. The group is not told why.')
      invites.reload()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not answer the invitation')
      invites.reload()
    } finally {
      setBusy(null)
    }
  }

  return (
    <AppShell title="Group invitations">
      <Link to="/messages" className="mb-4 inline-flex items-center gap-1.5 text-sm text-text-mid hover:text-text-hi">
        <ArrowLeft size={15} className="rtl:rotate-180" aria-hidden="true" />
        Messages
      </Link>
      <p className="mb-4 text-sm text-text-mid">
        Group chats people invited you to. You choose who may invite you in Settings, Privacy.
      </p>
      {invites.loading && <p className="caption">Loading…</p>}
      {invites.error && <p className="cloud-card p-4 text-sm text-text-mid">{invites.error}</p>}
      {invites.data && invites.data.items.length === 0 && (
        <p className="cloud-card p-6 text-sm text-text-mid">No invitation waiting.</p>
      )}
      {note && (
        <p role="status" className="mb-3 text-sm text-text-mid">
          {note}
        </p>
      )}
      <ul className="space-y-3">
        {invites.data?.items.map((invite) => (
          <li key={invite.id} className="cloud-card p-5">
            <div className="flex items-center gap-3">
              {invite.avatar_url ? (
                <img src={invite.avatar_url} alt="" className="h-12 w-12 rounded-full object-cover" />
              ) : (
                <span className="grid h-12 w-12 place-items-center rounded-full bg-gold/20 text-gold-soft">
                  <UsersRound size={22} aria-hidden="true" />
                </span>
              )}
              <div className="min-w-0">
                <p className="truncate text-base font-bold text-text-hi">{invite.title ?? 'Group chat'}</p>
                <p className="text-xs text-text-low">
                  Group chat · {invite.members_count} {invite.members_count === 1 ? 'member' : 'members'}
                  {invite.invited_by ? ` · invited by ${invite.invited_by}` : ''}
                </p>
              </div>
            </div>
            {invite.description && <p className="mt-3 whitespace-pre-wrap text-sm text-text-mid">{invite.description}</p>}
            {invite.rules && (
              <div className="mt-3 rounded-card-sm border border-white/10 px-3 py-2">
                <p className="flex items-center gap-1.5 text-xs font-semibold text-text-hi">
                  <ScrollText size={12} aria-hidden="true" />
                  Group rules
                </p>
                <p className="mt-1 whitespace-pre-wrap text-sm text-text-mid">{invite.rules}</p>
              </div>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy === invite.id}
                onClick={() => void answer(invite.id, 'accept')}
                className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-50"
              >
                {invite.rules ? 'Accept the rules and join' : 'Join'}
              </button>
              <button
                type="button"
                disabled={busy === invite.id}
                onClick={() => void answer(invite.id, 'decline')}
                className="rounded-full border border-white/15 px-5 py-2 text-sm font-semibold text-text-mid hover:text-text-hi disabled:opacity-50"
              >
                Decline
              </button>
            </div>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}
