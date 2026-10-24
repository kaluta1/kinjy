import { useState } from 'react'
import { Check, X } from 'lucide-react'
import { Sheet } from '@/components/community/CommunityDialogs'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta } from '@/lib/api'
import { htmlToText } from '@/lib/richtext'
import { cn } from '@/lib/utils'

const ACTION_LABEL: Record<string, string> = {
  approve: 'admitted',
  reject: 'refused the request of',
  ban: 'banned',
  unban: 'lifted the ban on',
  promote: 'made moderator',
  demote: 'removed moderator role from',
  remove: 'removed',
  invite: 'added',
  settings: 'changed the settings',
  spaces: 'changed the spaces',
  privacy: 'changed who can join',
  post_approve: 'approved a post',
  post_reject: 'refused a post',
  post_remove: 'removed a post',
  post_pin: 'pinned a post',
  post_unpin: 'unpinned a post',
}

/** Posts waiting for approval. For the owner and moderators. */
export function PendingPostsDialog({
  communityId,
  onClose,
  onChanged,
}: {
  communityId: string
  onClose: () => void
  onChanged: () => void
}) {
  const pending = useApi(() => kaluta.communities.pendingPosts(communityId), [communityId])
  const [busy, setBusy] = useState<string | null>(null)
  const [reasons, setReasons] = useState<Record<string, string>>({})
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const act = async (postId: string, action: 'approve' | 'reject') => {
    setBusy(postId)
    setNote(null)
    try {
      await kaluta.posts.moderate(postId, action, reasons[postId]?.trim() || undefined)
      setNote({ ok: true, text: action === 'approve' ? 'Approved: it is in the community now.' : 'Refused. Its author was told.' })
      pending.reload()
      onChanged()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    } finally {
      setBusy(null)
    }
  }

  const items = pending.data?.items ?? []
  return (
    <Sheet title={items.length ? `Posts to approve · ${items.length}` : 'Posts to approve'} onClose={onClose}>
      {note && (
        <p role={note.ok ? 'status' : 'alert'} className={cn('mb-3 text-sm', note.ok ? 'text-emerald-300' : 'text-red-300')}>
          {note.text}
        </p>
      )}
      {pending.loading && <p className="text-sm text-text-low">Loading…</p>}
      {pending.error && <p className="text-sm text-red-300">{pending.error}</p>}
      {!pending.loading && items.length === 0 && <p className="text-sm text-text-low">Nothing is waiting.</p>}
      <ul className="space-y-3">
        {items.map((post) => (
          <li key={post.id} className="rounded-2xl border border-[var(--cloud-border)] p-3.5">
            <p className="text-sm font-semibold text-text-hi">{post.author?.display_name ?? 'A member'}</p>
            <p className="mt-1 line-clamp-6 whitespace-pre-wrap break-words text-sm text-text-mid">
              {post.format === 'article' ? htmlToText(post.body) : post.body}
            </p>
            {post.media.length > 0 && <p className="caption mt-1">{post.media.length} attachment(s)</p>}
            <input
              value={reasons[post.id] ?? ''}
              onChange={(e) => setReasons((r) => ({ ...r, [post.id]: e.target.value }))}
              placeholder="Reason (optional, shown to the author if refused)"
              maxLength={500}
              className="mt-3 w-full rounded-full border border-[var(--cloud-border)] bg-transparent px-3 py-1.5 text-sm text-text-hi focus:border-gold/50 focus:outline-none"
            />
            <div className="mt-2 flex gap-2">
              <button type="button" disabled={busy === post.id} onClick={() => void act(post.id, 'approve')}
                className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40">
                <Check size={12} aria-hidden="true" /> Approve
              </button>
              <button type="button" disabled={busy === post.id} onClick={() => void act(post.id, 'reject')}
                className="inline-flex items-center gap-1.5 rounded-full px-4 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-400/10 disabled:opacity-40">
                <X size={12} aria-hidden="true" /> Refuse
              </button>
            </div>
          </li>
        ))}
      </ul>
    </Sheet>
  )
}

/** What the owner and moderators did, and why. For them only. */
export function AuditDialog({ communityId, onClose }: { communityId: string; onClose: () => void }) {
  const [offset, setOffset] = useState(0)
  const audit = useApi(() => kaluta.communities.audit(communityId, offset), [communityId, offset])
  const items = audit.data?.items ?? []
  return (
    <Sheet title="Activity log" onClose={onClose}>
      {audit.loading && <p className="text-sm text-text-low">Loading…</p>}
      {audit.error && <p className="text-sm text-red-300">{audit.error}</p>}
      {!audit.loading && items.length === 0 && <p className="text-sm text-text-low">Nothing recorded yet.</p>}
      <ul className="divide-y divide-[var(--cloud-border)]">
        {items.map((a) => (
          <li key={a.id} className="py-2.5 text-sm">
            <p className="text-text-hi">
              <span className="font-semibold">{a.actor.display_name ?? 'Somebody'}</span>{' '}
              {ACTION_LABEL[a.action] ?? a.action}
              {a.target ? ` ${a.target.display_name}` : ''}
            </p>
            {a.reason && <p className="mt-0.5 text-text-mid">“{a.reason}”</p>}
            <p className="caption mt-0.5">{new Date(a.created_at).toLocaleString()}</p>
          </li>
        ))}
      </ul>
      {(audit.data?.total ?? 0) > 50 && (
        <div className="mt-3 flex justify-between">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}
            className="text-sm font-semibold text-gold-soft disabled:opacity-40">Newer</button>
          <button type="button" disabled={offset + 50 >= (audit.data?.total ?? 0)} onClick={() => setOffset(offset + 50)}
            className="text-sm font-semibold text-gold-soft disabled:opacity-40">Older</button>
        </div>
      )}
    </Sheet>
  )
}
