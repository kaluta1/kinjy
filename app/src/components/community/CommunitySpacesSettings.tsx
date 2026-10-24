import { useId, useState } from 'react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type CommunitySpace } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * The owner's settings for how the community works: its template, its spaces,
 * whether posts wait for approval, the welcome message and the rules.
 *
 * Every switch is applied by the server, which checks again; a space whose
 * feature is not built yet is shown, and cannot be switched on.
 */
export default function CommunitySpacesSettings({
  community,
  onChanged,
}: {
  community: {
    id: string
    template?: string
    spaces?: CommunitySpace[]
    post_approval?: 'none' | 'new_members' | 'all'
    welcome_message?: string | null
    rules?: string
  }
  onChanged: () => void
}) {
  const ids = useId()
  const templates = useApi(() => kaluta.communities.templates(), [])
  const [spaces, setSpaces] = useState<CommunitySpace[]>(community.spaces ?? [])
  const [template, setTemplate] = useState(community.template ?? 'interest')
  const [approval, setApproval] = useState(community.post_approval ?? 'none')
  const [welcome, setWelcome] = useState(community.welcome_message ?? '')
  const [rules, setRules] = useState(community.rules ?? '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const apply = async (fn: () => Promise<{ spaces: CommunitySpace[] }>, done: string) => {
    setBusy(true)
    setNote(null)
    try {
      const view = await fn()
      setSpaces(view.spaces)
      setNote({ ok: true, text: done })
      onChanged()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    } finally {
      setBusy(false)
    }
  }

  const field =
    'w-full rounded-2xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm text-text-hi focus:border-gold/50 focus:outline-none'

  return (
    <div className="space-y-6">
      <fieldset>
        <legend className="text-sm font-semibold text-text-hi">Spaces</legend>
        <p className="mt-1 text-xs text-text-low">
          What members find in the community. Switching a space off hides it; nothing in it is deleted.
        </p>
        <ul className="mt-3 space-y-2">
          {spaces.map((space) => (
            <li key={space.id} className="flex items-start gap-2.5">
              <input
                id={`${ids}-${space.id}`}
                type="checkbox"
                checked={space.enabled}
                disabled={busy || !space.available}
                onChange={(e) =>
                  void apply(
                    () => kaluta.communities.setSpaces(community.id, { [space.id]: e.target.checked }),
                    `${space.label} ${e.target.checked ? 'on' : 'off'}.`,
                  )
                }
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--gold)]"
              />
              <label htmlFor={`${ids}-${space.id}`} className={cn('min-w-0', !space.available && 'opacity-60')}>
                <span className="block text-sm font-semibold text-text-hi">
                  {space.label}
                  {!space.available && <span className="ms-2 text-xs font-normal text-text-low">not available yet</span>}
                </span>
                <span className="block text-xs text-text-low">
                  {space.description}
                  {space.posters === 'stewards' && space.available ? ' Only the owner and moderators post here.' : ''}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </fieldset>

      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          void apply(
            () =>
              kaluta.communities.settings(community.id, {
                template,
                post_approval: approval,
                welcome_message: welcome,
                rules,
              }),
            'Saved.',
          )
        }}
      >
        <label className="block">
          <span className="text-sm font-semibold text-text-hi">Kind of community</span>
          <select value={template} onChange={(e) => setTemplate(e.target.value)} className={cn(field, 'mt-1.5')}>
            {(templates.data?.templates ?? [{ id: template, label: template }]).map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-text-low">A label for the community. Changing it changes no access and no space.</span>
        </label>
        <label className="block">
          <span className="text-sm font-semibold text-text-hi">Posts wait for approval</span>
          <select
            value={approval}
            onChange={(e) => setApproval(e.target.value as typeof approval)}
            className={cn(field, 'mt-1.5')}
          >
            <option value="none">Never</option>
            <option value="new_members">From new members (their first week)</option>
            <option value="all">Always, except from the owner and moderators</option>
          </select>
        </label>
        <label className="block">
          <span className="text-sm font-semibold text-text-hi">Welcome message</span>
          <textarea value={welcome} onChange={(e) => setWelcome(e.target.value)} rows={3} maxLength={2000}
            placeholder="Sent once to each new member" className={cn(field, 'mt-1.5')} />
        </label>
        <label className="block">
          <span className="text-sm font-semibold text-text-hi">Rules</span>
          <textarea value={rules} onChange={(e) => setRules(e.target.value)} rows={4} maxLength={8000}
            placeholder="Shown to everybody who opens the community" className={cn(field, 'mt-1.5')} />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-40"
        >
          Save
        </button>
      </form>

      {note && (
        <p role={note.ok ? 'status' : 'alert'} className={cn('text-sm', note.ok ? 'text-emerald-300' : 'text-red-300')}>
          {note.text}
        </p>
      )}
    </div>
  )
}
