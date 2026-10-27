import { useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { Globe, Lock, ScrollText, UsersRound } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, kaluta } from '@/lib/api'

/**
 * Where a group's shared link lands.
 *
 * The group's face and what it is for, and one button: Join for a public
 * group, Ask to join for a private one. Never its messages or its members -
 * those are for the people inside.
 */
export default function JoinGroup() {
  const { code = '' } = useParams()
  const { user } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const preview = useApi(() => (user ? kaluta.messages.previewLink(code) : Promise.resolve(null)), [code, user?.id])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  if (!user) {
    return (
      <AppShell title="Join a group">
        <div className="cloud-card p-6 text-center">
          <p className="text-sm text-text-mid">Sign in to see this group and join it.</p>
          <Link
            to={`/join?mode=signin&next=${encodeURIComponent(location.pathname)}`}
            className="mt-4 inline-block rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink"
          >
            Sign in
          </Link>
        </div>
      </AppShell>
    )
  }

  const group = preview.data
  const join = async () => {
    setBusy(true)
    setNote(null)
    try {
      const result = await kaluta.messages.joinByLink(code)
      if (result.status === 'member' && result.conversation_id) {
        navigate(`/messages?c=${result.conversation_id}`)
        return
      }
      setNote('Request sent. An admin of the group will answer it.')
      preview.reload()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not join')
    } finally {
      setBusy(false)
    }
  }

  return (
    <AppShell title="Join a group chat">
      {preview.loading && <p className="caption">Loading…</p>}
      {preview.error && <p className="cloud-card p-4 text-sm text-text-mid">{preview.error}</p>}
      {group && (
        <div className="cloud-card overflow-hidden">
          {group.cover_url && <img src={group.cover_url} alt="" className="h-32 w-full object-cover" />}
          <div className="p-6">
            <div className="flex items-center gap-4">
              {group.avatar_url ? (
                <img src={group.avatar_url} alt="" className="h-16 w-16 rounded-full object-cover" />
              ) : (
                <span className="flex h-16 w-16 items-center justify-center rounded-full bg-gold/20 text-gold-soft">
                  <UsersRound size={28} aria-hidden="true" />
                </span>
              )}
              <div className="min-w-0">
                <h1 className="truncate text-2xl font-bold text-text-hi">{group.title ?? 'Group'}</h1>
                <p className="mt-1 inline-flex items-center gap-1.5 text-sm text-text-low">
                  {group.privacy === 'public' ? <Globe size={13} aria-hidden="true" /> : <Lock size={13} aria-hidden="true" />}
                  {group.privacy === 'public' ? 'Group chat · anyone with the link joins' : 'Group chat · an admin lets people in'} · {group.members_count}{' '}
                  {group.members_count === 1 ? 'member' : 'members'}
                </p>
              </div>
            </div>
            {group.description && (
              <p className="mt-4 whitespace-pre-wrap text-[0.95rem] text-text-mid">{group.description}</p>
            )}
            {group.rules && (
              <div className="mt-4 rounded-card-sm border border-white/10 px-4 py-3">
                <p className="flex items-center gap-1.5 text-sm font-semibold text-text-hi">
                  <ScrollText size={14} aria-hidden="true" />
                  Group rules
                </p>
                <p className="mt-1 whitespace-pre-wrap text-sm text-text-mid">{group.rules}</p>
              </div>
            )}
            <p className="mt-4 text-xs text-text-low">
              A group chat in Messages: joining it gives no access to any community. Its members see your name and
              profile photo.
            </p>

            <div className="mt-5">
              {group.my_state === 'member' && group.id ? (
                <Link
                  to={`/messages?c=${group.id}`}
                  className="inline-block rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink"
                >
                  Open the group
                </Link>
              ) : group.my_state === 'pending' ? (
                <span className="inline-block rounded-full bg-amber-500/10 px-5 py-2.5 text-sm font-semibold text-amber-200">
                  Waiting for an admin
                </span>
              ) : group.unavailable ? (
                <p className="text-sm text-amber-200">{group.unavailable}</p>
              ) : (
                <button
                  type="button"
                  onClick={() => void join()}
                  disabled={busy}
                  className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40"
                >
                  {group.privacy === 'public'
                    ? group.rules
                      ? 'Accept the rules and join'
                      : 'Join'
                    : group.rules
                      ? 'Accept the rules and ask to join'
                      : 'Ask to join'}
                </button>
              )}
            </div>
            {note && (
              <p role="status" className="mt-3 text-sm text-text-mid">
                {note}
              </p>
            )}
          </div>
        </div>
      )}
    </AppShell>
  )
}
