import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, Globe, Lock, X } from 'lucide-react'
import MemberQueue from '@/components/community/MemberQueue'
import ProfileImagePicker from '@/components/dashboard/ProfileImagePicker'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useAppTheme } from '@/components/appdemo/theme'
import { ApiError, kaluta } from '@/lib/api'
import { cn } from '@/lib/utils'

/** A sheet from the bottom on a phone, a centred window from sm up. Closes on Escape and outside. */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const { tok } = useAppTheme()
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = previous
    }
  }, [onClose])
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/55 backdrop-blur-[2px] sm:items-center sm:p-6"
      onClick={onClose}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className={cn(
          'flex max-h-[92svh] w-full max-w-xl flex-col overflow-hidden rounded-t-3xl shadow-2xl sm:max-h-[88svh] sm:rounded-3xl',
          tok.card,
        )}
      >
        <div className="flex items-center justify-between gap-3 border-b border-[var(--cloud-border)] px-5 py-4">
          <h2 className="text-lg font-bold tracking-[-0.02em] text-text-hi">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-full p-2 text-text-mid hover:bg-text-hi/[0.07]"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 [scrollbar-width:thin]">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

const PRIVACY: Array<{ kind: 'public' | 'private' | 'secret'; label: string; hint: string }> = [
  { kind: 'public', label: 'Public', hint: 'Anyone can read it and join.' },
  { kind: 'private', label: 'Private', hint: 'Anyone can find it; joining sends you a request to approve.' },
  { kind: 'secret', label: 'Secret', hint: 'Unlisted; only a shared link opens it, and joining still needs your approval.' },
]

export interface CommunityForSettings {
  id: string
  name: string
  description: string
  kind: string
  public_content?: boolean
  avatar_url?: string | null
  cover_url?: string | null
}

/**
 * The owner's settings, in one window: the photo and the cover (applied as
 * soon as they are uploaded), the name and the description (saved with the
 * button), who can join, and whether anybody can read it.
 */
export function CommunitySettingsDialog({
  community,
  onClose,
  onChanged,
}: {
  community: CommunityForSettings
  onClose: () => void
  onChanged: () => void
}) {
  const [name, setName] = useState(community.name)
  const [description, setDescription] = useState(community.description ?? '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  // The name and description have their answer next to their own button:
  // the shared note sits at the bottom of the window, out of sight from here,
  // and a Save that seems to do nothing gets pressed again and again.
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState<{ ok: boolean; text: string } | null>(null)

  const save = async () => {
    setSaving(true)
    setSaved(null)
    try {
      await kaluta.communities.edit(community.id, { name: name.trim(), description: description.trim() })
      setSaved({ ok: true, text: 'Saved' })
      onChanged()
    } catch (err) {
      setSaved({ ok: false, text: err instanceof ApiError ? err.message : 'Could not save' })
    } finally {
      setSaving(false)
    }
  }

  const run = async (action: () => Promise<string>) => {
    setBusy(true)
    setNote(null)
    try {
      setNote({ ok: true, text: await action() })
      onChanged()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    } finally {
      setBusy(false)
    }
  }

  const changeImage = (key: 'avatar_asset_id' | 'cover_asset_id') => async (assetId: string | null) => {
    await kaluta.communities.edit(community.id, { [key]: assetId })
    onChanged()
  }

  return (
    <Sheet title="Edit community" onClose={onClose}>
      <div className="space-y-6">
        <section className="space-y-5">
          <ProfileImagePicker
            purpose="cover"
            url={community.cover_url ?? null}
            displayName={community.name}
            onChange={changeImage('cover_asset_id')}
          />
          <ProfileImagePicker
            purpose="avatar"
            url={community.avatar_url ?? null}
            displayName={community.name}
            onChange={changeImage('avatar_asset_id')}
          />
        </section>

        <form
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
          className="space-y-3"
        >
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-text-mid">Name</span>
            <input
              value={name}
              onChange={(e) => {
                setName(e.target.value)
                setSaved(null)
              }}
              maxLength={140}
              className="w-full rounded-[14px] border border-[var(--cloud-border)] bg-text-hi/[0.05] px-4 py-2.5 text-[0.95rem] text-text-hi focus:border-gold/60 focus:outline-none"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-semibold text-text-mid">Description</span>
            <textarea
              value={description}
              onChange={(e) => {
                setDescription(e.target.value)
                setSaved(null)
              }}
              rows={4}
              maxLength={4000}
              className="w-full resize-y rounded-[14px] border border-[var(--cloud-border)] bg-text-hi/[0.05] px-4 py-2.5 text-[0.95rem] text-text-hi focus:border-gold/60 focus:outline-none"
            />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={saving || busy || name.trim().length < 2}
              className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40"
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            {saved && (
              <span
                role={saved.ok ? 'status' : 'alert'}
                className={cn('inline-flex items-center gap-1 text-sm', saved.ok ? 'text-emerald-200' : 'text-red-200')}
              >
                {saved.ok && <Check size={14} aria-hidden="true" />}
                {saved.text}
              </span>
            )}
          </div>
        </form>

        {community.kind !== 'paid' && (
          <fieldset>
            <legend className="text-sm font-semibold text-text-hi">Who can join</legend>
            <div role="radiogroup" className="mt-2 flex flex-wrap gap-2">
              {PRIVACY.map((choice) => (
                <button
                  key={choice.kind}
                  type="button"
                  role="radio"
                  aria-checked={community.kind === choice.kind}
                  disabled={busy}
                  onClick={() =>
                    choice.kind !== community.kind &&
                    void run(async () => {
                      const result = await kaluta.communities.setPrivacy(community.id, choice.kind)
                      if (choice.kind === 'public') {
                        return result.admitted
                          ? `Public now. ${result.admitted} waiting request${result.admitted === 1 ? ' was' : 's were'} admitted.`
                          : 'Public now: anyone can join.'
                      }
                      return choice.kind === 'private'
                        ? 'Private now: joining sends you a request to approve.'
                        : 'Secret now: unlisted, opened only by a shared link.'
                    })
                  }
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full border px-4 py-2 text-sm font-semibold disabled:opacity-40',
                    community.kind === choice.kind
                      ? 'border-gold/60 bg-gold/15 text-gold-soft'
                      : 'border-[var(--cloud-border)] text-text-mid hover:border-gold/40',
                  )}
                >
                  {choice.kind === 'public' ? <Globe size={13} aria-hidden="true" /> : <Lock size={13} aria-hidden="true" />}
                  {choice.label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs text-text-low">{PRIVACY.find((c) => c.kind === community.kind)?.hint}</p>

            {community.kind === 'public' && (
              <label className="mt-4 flex items-start gap-2.5">
                <input
                  type="checkbox"
                  checked={Boolean(community.public_content)}
                  disabled={busy}
                  onChange={(event) =>
                    void run(async () => {
                      await kaluta.communities.setPublicContent(community.id, event.target.checked)
                      return event.target.checked
                        ? 'Anyone can read it now. Only members can post, like and comment.'
                        : 'Its posts are for members now.'
                    })
                  }
                  className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--gold)]"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-text-hi">Anyone can read this community</span>
                  <span className="mt-0.5 block text-xs text-text-low">
                    Its posts open for people who have not joined, and links to them preview outside Kinjy.
                  </span>
                </span>
              </label>
            )}
          </fieldset>
        )}

        {note && (
          <p role={note.ok ? 'status' : 'alert'} className={cn('text-sm', note.ok ? 'text-emerald-200' : 'text-red-200')}>
            {note.text}
          </p>
        )}
      </div>
    </Sheet>
  )
}

/**
 * Who is in the community. Everybody who may see it gets the list; the owner
 * and the moderators get the desk - the requests waiting, roles, bans.
 */
export function CommunityMembersDialog({
  communityId,
  canModerate,
  pending,
  onClose,
  onChanged,
}: {
  communityId: string
  canModerate: boolean
  pending: number
  onClose: () => void
  onChanged: () => void
}) {
  const roster = useRoster(communityId, !canModerate)
  return (
    <Sheet title={canModerate && pending > 0 ? `Members · ${pending} waiting` : 'Members'} onClose={onClose}>
      {canModerate ? (
        <MemberQueue communityId={communityId} canModerate onChanged={onChanged} bare />
      ) : roster.error ? (
        <p className="text-sm text-text-mid">{roster.error}</p>
      ) : !roster.items ? (
        <p className="text-sm text-text-low">Loading…</p>
      ) : (
        <ul className="space-y-1">
          {roster.items.map((m) => (
            <li key={m.user_id} className="flex items-center gap-3 rounded-xl px-1 py-1.5">
              <MemberAvatar
                handle={m.profile?.handle}
                displayName={m.profile?.display_name}
                avatarUrl={m.profile?.avatar_url ?? null}
                size={36}
              />
              <span
                className="min-w-0 flex-1 truncate text-sm text-text-hi"
                title={m.profile?.handle ? `@${m.profile.handle}` : undefined}
              >
                {m.profile?.display_name ?? 'Member'}
              </span>
              {m.role !== 'member' && (
                <span className="rounded-full bg-gold/15 px-2 py-0.5 text-[0.65rem] font-bold capitalize text-gold-soft">
                  {m.role}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Sheet>
  )
}

function useRoster(communityId: string, enabled: boolean) {
  const [items, setItems] = useState<
    Array<{ user_id: string; role: string; profile: { handle: string; display_name: string; avatar_url: string | null } | null }> | null
  >(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!enabled) return
    kaluta.communities
      .roster(communityId)
      .then((page) => setItems(page.items))
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the members'))
  }, [communityId, enabled])
  return { items, error }
}
