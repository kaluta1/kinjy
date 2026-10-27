import { useState } from 'react'
import { Link } from 'react-router'
import { ArrowLeft, BookmarkX } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useApi } from '@/hooks/useApi'
import { briefText } from '@/components/social/group/groupText'
import { ApiError, kaluta } from '@/lib/api'

/**
 * The messages I saved, from every conversation. Private: nobody is told a
 * message was saved. A saved message is a bookmark, not a copy - one that is
 * deleted, expires, or that I can no longer read is no longer here.
 */
export default function MessagesSaved() {
  const saved = useApi(() => kaluta.messages.saved(), [])
  const [note, setNote] = useState<string | null>(null)

  const unsave = async (conversationId: string, messageId: string) => {
    setNote(null)
    try {
      await kaluta.messages.unsaveMessage(conversationId, messageId)
      saved.reload()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not remove it')
    }
  }

  return (
    <AppShell title="Saved messages">
      <Link to="/messages" className="mb-4 inline-flex items-center gap-1.5 text-sm text-text-mid hover:text-text-hi">
        <ArrowLeft size={15} className="rtl:rotate-180" aria-hidden="true" />
        Messages
      </Link>
      <p className="mb-4 text-sm text-text-mid">Only you see what you saved. Save a message from its menu in any conversation.</p>
      {saved.loading && <p className="caption">Loading…</p>}
      {saved.error && <p className="cloud-card p-4 text-sm text-text-mid">{saved.error}</p>}
      {saved.data && saved.data.items.length === 0 && <p className="cloud-card p-6 text-sm text-text-mid">Nothing saved yet.</p>}
      {note && <p role="status" className="mb-3 text-sm text-text-mid">{note}</p>}
      <ul className="space-y-2">
        {saved.data?.items.map((m) => (
          <li key={m.id} className="cloud-card flex items-start gap-3 p-4">
            <Link to={`/messages?c=${m.conversation_id}`} className="min-w-0 flex-1">
              <span className="block text-xs text-text-low">
                <span className="font-semibold text-text-hi">{saved.data?.profiles[m.sender_id]?.display_name ?? 'Member'}</span>
                {' · '}
                {m.conversation_kind === 'group' ? (m.conversation_title ?? 'Group chat') : 'Direct message'}
                {' · '}
                {new Date(m.created_at).toLocaleString()}
              </span>
              <span className="mt-1 line-clamp-3 block whitespace-pre-wrap text-sm text-text-mid">{briefText(m)}</span>
              {m.media_url && m.media_kind === 'image' && (
                <img src={m.media_url} alt={m.media_name ?? ''} loading="lazy" className="mt-2 max-h-40 rounded-card-sm object-cover" />
              )}
            </Link>
            <button
              type="button"
              onClick={() => void unsave(m.conversation_id, m.id)}
              aria-label="Remove from saved"
              title="Remove from saved"
              className="shrink-0 rounded-full p-2 text-text-mid hover:bg-text-hi/[0.07] hover:text-text-hi"
            >
              <BookmarkX size={16} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}
