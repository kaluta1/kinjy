import { useState } from 'react'
import { Link } from 'react-router'
import { Link2Off, MessageSquare, UsersRound } from 'lucide-react'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type LinkedGroupChat } from '@/lib/api'

/**
 * Group chats (in Messages) linked to this community.
 *
 * Said on the page itself, because the two are easy to confuse: a chat linked
 * here is joined through its own door - its link, or a request an admin
 * answers - and being in it makes nobody a member of the community, nor does
 * being a member put anybody in it.
 */
export default function CommunityGroupChats({
  slug,
  chats,
  canManage,
  onChanged,
}: {
  slug: string
  chats: LinkedGroupChat[]
  canManage: boolean
  onChanged: () => void
}) {
  const mine = useApi(() => (canManage ? kaluta.messages.conversations() : Promise.resolve(null)), [canManage])
  const [picked, setPicked] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [unlinking, setUnlinking] = useState<LinkedGroupChat | null>(null)
  const linked = new Set(chats.map((c) => c.id))
  // The chats this member runs and that are not linked yet: linking needs
  // them to run both the community and the chat.
  const runnable = (mine.data?.items ?? []).filter(
    (c) => c.kind === 'group' && !c.is_welcome && (c.my_role === 'owner' || c.my_role === 'admin') && !linked.has(c.id) && !c.community_id,
  )

  const act = async (action: () => Promise<unknown>, said: string, failure: string) => {
    setBusy(true)
    setNote(null)
    try {
      await action()
      setNote(said)
      onChanged()
      mine.reload()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : failure)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="cloud-card p-4 sm:p-5">
      <h2 className="flex items-center gap-2 text-base font-bold text-text-hi">
        <MessageSquare size={17} className="text-gold-soft" aria-hidden="true" />
        Group chats
      </h2>
      <p className="mt-1 text-xs leading-relaxed text-text-low">
        Conversations in Messages that this community points to. Joining a chat does not make you a member of the
        community, and community membership does not add you to a chat: each has its own door.
      </p>
      {chats.length === 0 ? (
        <p className="mt-3 text-sm text-text-mid">No group chat is linked yet.</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {chats.map((chat) => (
            <li key={chat.id} className="flex items-center gap-3 rounded-card-sm border border-[var(--cloud-border)] p-3">
              {chat.avatar_url ? (
                <img src={chat.avatar_url} alt="" className="h-10 w-10 shrink-0 rounded-full object-cover" />
              ) : (
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-gold/20 text-gold-soft">
                  <UsersRound size={18} aria-hidden="true" />
                </span>
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-semibold text-text-hi">{chat.title ?? 'Group chat'}</span>
                <span className="block text-xs text-text-low">
                  {chat.members_count} {chat.members_count === 1 ? 'member' : 'members'} ·{' '}
                  {chat.privacy === 'public' ? 'anyone with the link joins' : 'an admin lets people in'}
                </span>
              </span>
              {chat.my_state === 'member' ? (
                <Link to={`/messages?c=${chat.id}`} className="shrink-0 rounded-full bg-text-hi/[0.07] px-3 py-1.5 text-xs font-semibold text-text-hi">
                  Open
                </Link>
              ) : chat.my_state === 'pending' ? (
                <span className="shrink-0 text-xs text-amber-200">Request sent</span>
              ) : chat.join_path ? (
                <Link to={chat.join_path} className="shrink-0 rounded-full bg-gradient-to-br from-gold-soft to-gold px-3 py-1.5 text-xs font-bold text-ink">
                  {chat.privacy === 'public' ? 'Join the chat' : 'Ask to join'}
                </Link>
              ) : (
                <span className="shrink-0 text-xs text-text-low">Ask an admin</span>
              )}
              {canManage && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setUnlinking(chat)}
                  aria-label={`Unlink ${chat.title ?? 'this chat'}`}
                  title="Unlink from the community"
                  className="shrink-0 rounded-full p-1.5 text-text-low hover:bg-text-hi/[0.07] hover:text-red-300"
                >
                  <Link2Off size={14} aria-hidden="true" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <div className="mt-4 border-t border-[var(--cloud-border)] pt-3">
          <p className="text-xs font-semibold text-text-hi">Link one of your group chats</p>
          {runnable.length === 0 ? (
            <p className="mt-1 text-xs text-text-low">Only group chats you own or run as an admin can be linked, and none is available.</p>
          ) : (
            <div className="mt-2 flex flex-wrap gap-2">
              <select
                value={picked}
                onChange={(e) => setPicked(e.target.value)}
                aria-label="Group chat to link"
                className="min-w-0 flex-1 rounded-full border border-[var(--cloud-border)] bg-transparent px-3 py-1.5 text-sm text-text-hi"
              >
                <option value="">Choose a group chat…</option>
                {runnable.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title ?? 'Group chat'}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={!picked || busy}
                onClick={() =>
                  void act(
                    () => kaluta.communities.linkGroupChat(slug, picked),
                    'Linked. Members of the community can now see it here and ask to join.',
                    'Could not link the chat',
                  )
                }
                className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-50"
              >
                Link
              </button>
            </div>
          )}
        </div>
      )}
      {note && <p role="status" className="mt-2 text-xs text-text-mid">{note}</p>}
      <ConfirmDialog
        open={Boolean(unlinking)}
        onOpenChange={(open) => !open && setUnlinking(null)}
        title="Unlink this group chat?"
        description="It stops appearing on the community. Nobody is removed from the chat, and the chat is told the link was removed."
        confirmLabel="Unlink"
        onConfirm={() => {
          const chat = unlinking
          setUnlinking(null)
          if (chat) void act(() => kaluta.communities.unlinkGroupChat(slug, chat.id), 'Unlinked.', 'Could not unlink the chat')
        }}
      />
    </section>
  )
}
