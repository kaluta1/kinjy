import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { ApiError, kaluta, type Conversation, type ConversationNotifications } from '@/lib/api'
import { cn } from '@/lib/utils'

function useAction(onChanged: () => void) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const run = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true)
    setNote(null)
    try {
      const said = await action()
      if (said) setNote(said)
      onChanged()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : failure)
    } finally {
      setBusy(false)
    }
  }
  return { busy, note, run }
}

function Choice<T extends string>({
  label,
  value,
  options,
  disabled,
  onPick,
}: {
  label: string
  value: T
  options: Array<{ id: T; label: string }>
  disabled?: boolean
  onPick: (id: T) => void
}) {
  const { tok } = useAppTheme()
  return (
    <div>
      <p className={cn('text-xs font-semibold', tok.text)}>{label}</p>
      <div role="radiogroup" aria-label={label} className="mt-1.5 flex flex-wrap gap-1.5">
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={value === o.id}
            disabled={disabled}
            onClick={() => value !== o.id && onPick(o.id)}
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-semibold disabled:opacity-50',
              value === o.id ? 'border-gold/60 bg-gold/15 text-gold-soft' : cn('border-[var(--cloud-border)] hover:border-gold/40', tok.mid),
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/** What this member hears from the group, and whether they share "Seen". */
export function GroupNotifications({ conversation, onChanged }: { conversation: Conversation; onChanged: () => void }) {
  const { tok } = useAppTheme()
  const [prefs, setPrefs] = useState<ConversationNotifications>(
    conversation.notifications ?? { level: 'all', muted_until: null, muted_forever: false, read_receipts: true },
  )
  const { busy, note, run } = useAction(onChanged)
  const set = (input: Parameters<typeof kaluta.messages.setNotifications>[1]) =>
    run(async () => {
      setPrefs(await kaluta.messages.setNotifications(conversation.id, input))
    }, 'Could not save this setting')
  const muted = prefs.muted_forever
    ? 'Muted until you turn it back on'
    : prefs.muted_until
      ? `Muted until ${new Date(prefs.muted_until).toLocaleString()}`
      : null

  return (
    <div className="space-y-4">
      <Choice
        label="Notify me about"
        value={prefs.level}
        disabled={busy}
        options={[
          { id: 'all', label: 'All messages' },
          { id: 'mentions', label: 'Mentions and replies' },
          { id: 'announcements', label: 'Announcements only' },
        ]}
        onPick={(level) => void set({ level })}
      />
      <div>
        <p className={cn('text-xs font-semibold', tok.text)}>Mute</p>
        {muted && <p className={cn('mt-0.5 text-xs', tok.low)}>{muted}</p>}
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {(
            [
              ['1h', '1 hour'],
              ['8h', '8 hours'],
              ['1d', '1 day'],
              ['1w', '1 week'],
              ['forever', 'Until I turn it on'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              disabled={busy}
              onClick={() => void set({ mute: id })}
              className={cn('rounded-full border border-[var(--cloud-border)] px-3 py-1 text-xs font-semibold hover:border-gold/40 disabled:opacity-50', tok.mid)}
            >
              {label}
            </button>
          ))}
          {muted && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void set({ mute: 'off' })}
              className="rounded-full border border-gold/50 px-3 py-1 text-xs font-semibold text-gold-soft disabled:opacity-50"
            >
              Unmute
            </button>
          )}
        </div>
      </div>
      <label className={cn('flex items-start gap-2 text-xs', tok.mid)}>
        <input
          type="checkbox"
          className="mt-0.5 accent-[#E5B556]"
          checked={prefs.read_receipts}
          disabled={busy}
          onChange={(e) => void set({ read_receipts: e.target.checked })}
        />
        <span>Let others see when I have read their messages (“Seen”)</span>
      </label>
      {note && <p role="status" className={cn('text-xs', tok.mid)}>{note}</p>}
    </div>
  )
}

/** Announcement mode, the member list, polls, history and disappearing messages. Admins only. */
export function GroupRules({ conversation, onChanged }: { conversation: Conversation; onChanged: () => void }) {
  const { tok } = useAppTheme()
  const { busy, note, run } = useAction(onChanged)
  const setting = (input: Parameters<typeof kaluta.messages.setGroupSettings>[1], said: string) =>
    run(async () => {
      await kaluta.messages.setGroupSettings(conversation.id, input)
      return said
    }, 'Could not change this setting')

  return (
    <div className="space-y-4">
      <Choice
        label="Who can write"
        value={conversation.posting ?? 'all'}
        disabled={busy}
        options={[
          { id: 'all', label: 'Every member' },
          { id: 'admins', label: 'Admins and moderators (announcements)' },
        ]}
        onPick={(posting) =>
          void setting(
            { posting },
            posting === 'admins' ? 'Announcement mode is on: members read and react.' : 'Everybody can write again.',
          )
        }
      />
      <Choice
        label="Who sees the member list"
        value={conversation.members_visible ?? 'all'}
        disabled={busy}
        options={[
          { id: 'all', label: 'Every member' },
          { id: 'admins', label: 'Admins only' },
        ]}
        onPick={(members_visible) =>
          void setting({ members_visible }, members_visible === 'admins' ? 'Members now see only who runs the group.' : 'Every member sees the member list.')
        }
      />
      <Choice
        label="Who can start polls"
        value={conversation.polls_by ?? 'all'}
        disabled={busy}
        options={[
          { id: 'all', label: 'Every member' },
          { id: 'admins', label: 'Admins and moderators' },
        ]}
        onPick={(polls_by) => void setting({ polls_by }, 'Saved.')}
      />
      {!conversation.is_welcome && (
        <Choice
          label="What new members can read"
          value={conversation.history_for_new ?? 'all'}
          disabled={busy}
          options={[
            { id: 'all', label: 'The whole conversation' },
            { id: 'from_join', label: 'Only from when they join' },
          ]}
          onPick={(history) =>
            void run(async () => {
              await kaluta.messages.setHistory(conversation.id, history)
              return history === 'all'
                ? 'New members read the earlier messages.'
                : 'New members read only from when they join - in the chat, search, pins, saved messages and files alike.'
            }, 'Could not change this setting')
          }
        />
      )}
      <Choice
        label="Disappearing messages"
        value={String(conversation.disappear_after_seconds ?? 0)}
        disabled={busy}
        options={[
          { id: '0', label: 'Off' },
          { id: String(24 * 3600), label: '24 hours' },
          { id: String(7 * 24 * 3600), label: '7 days' },
          { id: String(30 * 24 * 3600), label: '30 days' },
        ]}
        onPick={(seconds) =>
          void run(async () => {
            await kaluta.messages.setDisappearing(conversation.id, Number(seconds))
            return Number(seconds) ? 'New messages will be deleted after this time.' : 'New messages are kept.'
          }, 'Could not change this setting')
        }
      />
      {note && <p role="status" className={cn('text-xs', tok.mid)}>{note}</p>}
    </div>
  )
}

function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** The share link's limits: on or off, an end date, a number of uses, a new link. Admins only. */
export function GroupLinkLimits({ conversation, onChanged }: { conversation: Conversation; onChanged: () => void }) {
  const { tok } = useAppTheme()
  const link = conversation.invite_link
  const { busy, note, run } = useAction(onChanged)
  const [expires, setExpires] = useState(toLocalInput(link?.expires_at))
  const [uses, setUses] = useState(link?.max_uses ? String(link.max_uses) : '')
  const [confirmReset, setConfirmReset] = useState(false)
  if (!link) return null

  return (
    <div className="space-y-3 border-t border-[var(--cloud-border)] pt-3">
      <label className={cn('flex items-center gap-2 text-xs font-semibold', tok.text)}>
        <input
          type="checkbox"
          className="accent-[#E5B556]"
          checked={link.enabled}
          disabled={busy}
          onChange={(e) =>
            void run(async () => {
              await kaluta.messages.setInviteLink(conversation.id, { enabled: e.target.checked })
              return e.target.checked ? 'The link works again.' : 'The link is off: it leads nowhere until you turn it on.'
            }, 'Could not change the link')
          }
        />
        The group’s link is on
      </label>
      <p className={cn('text-xs', tok.low)}>
        Used {link.uses} {link.uses === 1 ? 'time' : 'times'}
        {link.max_uses ? ` of ${link.max_uses}` : ''}
        {link.expires_at ? ` · ends ${new Date(link.expires_at).toLocaleString()}` : ''}
      </p>
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void run(async () => {
            await kaluta.messages.setInviteLink(conversation.id, {
              expires_at: expires ? new Date(expires).toISOString() : null,
              max_uses: uses ? Number(uses) : null,
            })
            return 'The link’s limits are saved.'
          }, 'Could not save the limits')
        }}
      >
        <label className={cn('text-xs', tok.mid)}>
          Ends
          <input
            type="datetime-local"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            className={cn('mt-1 block rounded-xl border border-[var(--cloud-border)] bg-transparent px-2 py-1.5 text-xs', tok.text)}
          />
        </label>
        <label className={cn('text-xs', tok.mid)}>
          Uses at most
          <input
            type="number"
            min={1}
            value={uses}
            onChange={(e) => setUses(e.target.value)}
            placeholder="No limit"
            className={cn('mt-1 block w-28 rounded-xl border border-[var(--cloud-border)] bg-transparent px-2 py-1.5 text-xs', tok.text)}
          />
        </label>
        <button type="submit" disabled={busy} className="rounded-full border border-gold/50 px-3 py-1.5 text-xs font-semibold text-gold-soft disabled:opacity-50">
          Save limits
        </button>
      </form>
      <button
        type="button"
        disabled={busy}
        onClick={() => setConfirmReset(true)}
        className={cn('inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-3 py-1 text-xs font-semibold hover:border-red-400/40 disabled:opacity-50', tok.mid)}
      >
        <RefreshCw size={12} aria-hidden="true" />
        Replace the link
      </button>
      {note && <p role="status" className={cn('text-xs', tok.mid)}>{note}</p>}
      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Replace the group’s link?"
        description="The current link and its QR code stop working at once, wherever they were shared. People already in the group stay in it. You can share the new link afterwards."
        confirmLabel="Replace the link"
        onConfirm={() => {
          setConfirmReset(false)
          void run(async () => {
            await kaluta.messages.resetInviteLink(conversation.id)
            return 'A new link was made. The old one no longer works.'
          }, 'Could not replace the link')
        }}
      />
    </div>
  )
}
