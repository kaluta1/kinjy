import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { MessagesSquare } from 'lucide-react'
import { ApiError, kaluta, type Forum } from '@/lib/api'
import { useApi } from '@/hooks/useApi'

const BTN = 'rounded-full border border-[var(--cloud-border)] px-3.5 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'
const FIELD = 'w-full rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm text-text-hi focus:border-gold/50 focus:outline-none'

/**
 * A community's forums: questions and answers that stay findable, beside its
 * posts. Who may read them is the community's own rule (the server checks it);
 * only the owner and moderators may open a new one.
 */
export default function CommunityForums({
  communityId,
  steward,
  member,
  onChanged,
}: {
  communityId: string
  steward: boolean
  member: boolean
  /** The page counts the forums for its tab. */
  onChanged?: () => void
}) {
  const forums = useApi(() => kaluta.forums.list({ community_id: communityId }), [communityId])
  const [creating, setCreating] = useState(false)

  return (
    <section aria-label="Forums" className="space-y-3">
      {steward && (
        <button type="button" className={BTN} onClick={() => setCreating(!creating)} aria-expanded={creating}>
          {creating ? 'Close' : 'New forum'}
        </button>
      )}
      {creating && (
        <NewForum
          communityId={communityId}
          onCreated={() => {
            setCreating(false)
            forums.reload()
            onChanged?.()
          }}
        />
      )}
      {forums.loading && <p className="caption">Loading forums…</p>}
      {forums.error && (
        <p className="cloud-card p-4 text-[0.95rem] text-text-mid">
          {member ? forums.error : 'The forums of this community are for its members.'}
        </p>
      )}
      {forums.data?.items.length === 0 && (
        <p className="cloud-card p-4 text-[0.95rem] text-text-mid">
          {steward ? 'No forum yet. Open one for the questions members keep asking.' : 'This community has no forum yet.'}
        </p>
      )}
      <ul className="space-y-3">
        {forums.data?.items.map((f: Forum) => (
          <li key={f.id}>
            <Link
              to={`/forums?forum=${encodeURIComponent(f.id)}`}
              className="cloud-card flex items-start gap-3 p-4 hover:border-gold/40"
            >
              <MessagesSquare size={18} className="mt-0.5 shrink-0 text-gold-soft" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block font-semibold text-text-hi [overflow-wrap:anywhere]">{f.name}</span>
                {f.description && (
                  <span className="mt-1 block text-[0.95rem] leading-relaxed text-text-mid">{f.description}</span>
                )}
                <span className="mt-1 block text-sm text-text-mid">
                  {f.threads_count} thread{f.threads_count === 1 ? '' : 's'}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

function NewForum({ communityId, onCreated }: { communityId: string; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (name.trim().length < 2) return
    setBusy(true)
    setError(null)
    try {
      await kaluta.forums.create({ name: name.trim(), description: description.trim(), community_id: communityId })
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the forum')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="cloud-card space-y-3 p-4">
      <label className="block">
        <span className="mb-1 block text-sm font-semibold text-text-mid">Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={140} required className={FIELD} />
      </label>
      <label className="block">
        <span className="mb-1 block text-sm font-semibold text-text-mid">What it is for</span>
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={2000} className={FIELD} />
      </label>
      {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
      <button type="submit" disabled={busy || name.trim().length < 2} className={BTN}>
        Create forum
      </button>
    </form>
  )
}
