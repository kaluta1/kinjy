import { useCallback, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router'
import {
  Bell,
  CalendarDays,
  Check,
  Flag,
  Globe,
  History,
  Images,
  Link2,
  Lock,
  LogOut,
  Megaphone,
  MessageSquare,
  Pencil,
  Pin,
  PinOff,
  ScrollText,
  Search,
  Settings2,
  ShieldCheck,
  Trash2,
  UsersRound,
  X,
} from 'lucide-react'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { GroupSection, ReportDialog, TypeToConfirm } from '@/components/social/group/GroupBits'
import { ROLE_LABEL } from '@/components/social/group/groupText'
import { GroupLinkLimits, GroupNotifications, GroupRules } from '@/components/social/group/GroupSettings'
import GroupMembers from '@/components/social/group/GroupMembers'
import { GroupAuditLog, GroupFiles, GroupPins, GroupPlans } from '@/components/social/group/GroupShared'
import { copyLink, siteOrigin } from '@/lib/share'
import { shortLinkFor } from '@/lib/shortLink'
import { useAuth } from '@/hooks/useAuth'
import LinkQr from '@/components/ui-kit/LinkQr'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useAppTheme } from '@/components/appdemo/theme'
import { ApiError, kaluta, type Conversation, type GroupMember, type PersonBrief } from '@/lib/api'
import ProfileImagePicker from '@/components/dashboard/ProfileImagePicker'
import { cn } from '@/lib/utils'

/**
 * Everything about a group that is not a message.
 *
 * Who is in it, who runs it, what it is for, and the way out. The rules it
 * shows are the server's - a member sees the same list as an admin and simply
 * has no buttons on it, rather than seeing a tidier screen that hides what the
 * room is.
 */
/**
 * Who may come in, how to send them the link, who is waiting, and the pin.
 *
 * The link exists whatever the privacy: a public group lets its holder in, a
 * private one turns it into a request an admin answers. Nobody under 18, and
 * nobody into a room with a minor in it, comes in by link - the server says
 * so in one sentence that does not say which.
 */
function GroupAccess({
  conversation,
  runs,
  onChanged,
}: {
  conversation: Conversation
  runs: boolean
  onChanged: () => void
}) {
  const { tok } = useAppTheme()
  const { user: me } = useAuth()
  const privacy = conversation.privacy ?? 'private'
  const [note, setNote] = useState<string | null>(null)
  const [needsKyc, setNeedsKyc] = useState(false)
  const [busy, setBusy] = useState(false)
  // The group's address once it has been asked for, so it can be drawn as a code.
  const [sharedUrl, setSharedUrl] = useState<string | null>(null)
  const [requests, setRequests] = useState<Array<GroupMember & { created_at: string }>>([])

  const loadRequests = useCallback(() => {
    if (!runs) return
    kaluta.messages
      .requests(conversation.id)
      .then((page) => setRequests(page.items))
      .catch(() => setRequests([]))
  }, [conversation.id, runs])
  useEffect(loadRequests, [loadRequests])

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true)
    setNote(null)
    setNeedsKyc(false)
    try {
      const said = await action()
      if (said) setNote(said)
      loadRequests()
      onChanged()
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) setNeedsKyc(true)
      setNote(err instanceof ApiError ? err.message : failure)
    } finally {
      setBusy(false)
    }
  }

  const share = () =>
    act(async () => {
      const { path } = await kaluta.messages.shareLink(conversation.id)
      // The member's own short link to the invitation: shorter to send, and
      // it credits them if somebody joins Kinjy through it.
      const url = me ? await shortLinkFor(me.id, path) : new URL(path, siteOrigin()).toString()
      setSharedUrl(url)
      return (await copyLink(url)) === 'copied' ? 'Link copied.' : `Copy this link: ${url}`
    }, 'Could not get the link')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void share()}
          disabled={busy}
          className="inline-flex items-center gap-1.5 rounded-full border border-gold/50 px-3 py-1 text-xs font-semibold text-gold-soft hover:bg-gold/10 disabled:opacity-50"
        >
          <Link2 size={12} aria-hidden="true" />
          Share link
        </button>
        <button
          type="button"
          aria-pressed={Boolean(conversation.pinned)}
          onClick={() =>
            void act(async () => {
              if (conversation.pinned) {
                await kaluta.messages.unpin(conversation.id)
                return 'Unpinned.'
              }
              await kaluta.messages.pin(conversation.id)
              return 'Pinned to the top of your list.'
            }, 'Could not change the pin')
          }
          disabled={busy}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-3 py-1 text-xs font-semibold hover:bg-text-hi/[0.05] disabled:opacity-50',
            tok.mid,
          )}
        >
          {conversation.pinned ? <PinOff size={12} aria-hidden="true" /> : <Pin size={12} aria-hidden="true" />}
          {conversation.pinned ? 'Unpin' : 'Pin'}
        </button>
      </div>

      {runs ? (
        <div role="radiogroup" aria-label="Who can join with the link" className="flex gap-2">
          {(['public', 'private'] as const).map((choice) => (
            <button
              key={choice}
              type="button"
              role="radio"
              aria-checked={privacy === choice}
              disabled={busy}
              onClick={() =>
                privacy !== choice &&
                void act(async () => {
                  const result = await kaluta.messages.setPrivacy(conversation.id, choice)
                  if (choice === 'private') return 'The group is private: the link now asks an admin.'
                  return result.admitted
                    ? `The group is public. ${result.admitted} waiting ${result.admitted === 1 ? 'person was' : 'people were'} let in.`
                    : 'The group is public: anyone with the link can join.'
                }, 'Could not change the privacy')
              }
              className={cn(
                'inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold disabled:opacity-50',
                privacy === choice
                  ? 'border-gold/60 bg-gold/15 text-gold-soft'
                  : cn('border-[var(--cloud-border)] hover:border-gold/40', tok.mid),
              )}
            >
              {choice === 'public' ? <Globe size={12} aria-hidden="true" /> : <Lock size={12} aria-hidden="true" />}
              {choice === 'public' ? 'Public' : 'Private'}
            </button>
          ))}
        </div>
      ) : null}
      <p className={cn('flex items-center gap-1.5 text-xs', tok.low)}>
        {privacy === 'public' ? <Globe size={12} aria-hidden="true" /> : <Lock size={12} aria-hidden="true" />}
        {privacy === 'public'
          ? 'Public: anyone with the link can join.'
          : 'Private: the link sends a request an admin answers.'}
      </p>

      {note && (
        <p role="status" className={cn('break-all text-xs', tok.mid)}>
          {note}
          {needsKyc && (
            <>
              {' '}
              <Link to="/dashboard?tab=verification" className="font-semibold text-gold-soft hover:underline">
                Pay the KYC fee
              </Link>
            </>
          )}
        </p>
      )}

      {sharedUrl && (
        <LinkQr
          url={sharedUrl}
          label={`the group ${conversation.title ?? ''}`.trim()}
          filename="kinjy-group"
          size={104}
          caption="Scan to open the group"
        />
      )}

      {runs && requests.length > 0 && (
        <div>
          <h4 className={cn('text-xs font-semibold', tok.text)}>
            {requests.length} waiting to join
          </h4>
          <ul className="mt-1 space-y-1">
            {requests.map((person) => (
              <li key={person.user_id} className="flex items-center gap-2">
                <MemberAvatar
                  handle={person.handle ?? undefined}
                  displayName={person.display_name}
                  avatarUrl={person.avatar_url}
                  size={28}
                />
                <span className={cn('min-w-0 flex-1 truncate text-xs', tok.text)}>{person.display_name}</span>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Let ${person.display_name} in`}
                  onClick={() =>
                    void act(async () => {
                      await kaluta.messages.answerRequest(conversation.id, person.user_id, 'approve')
                    }, 'Could not let them in')
                  }
                  className="rounded-full p-1.5 text-emerald-300 hover:bg-emerald-500/10"
                >
                  <Check size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  disabled={busy}
                  aria-label={`Turn ${person.display_name} away`}
                  onClick={() =>
                    void act(async () => {
                      await kaluta.messages.answerRequest(conversation.id, person.user_id, 'reject')
                    }, 'Could not answer')
                  }
                  className={cn('rounded-full p-1.5 hover:bg-text-hi/[0.07]', tok.low)}
                >
                  <X size={14} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function GroupPanel({
  conversation,
  meId,
  onClose,
  onChanged,
  onJump,
}: {
  conversation: Conversation
  /** Who is looking. Leaving needs an identity, not a role: two people can
   *  share a role and the first match is not necessarily you. */
  meId: string
  onClose: () => void
  /** The list and header are stale the moment anything here succeeds. */
  onChanged: () => void
  /** Go to a message in the thread (a pin, an event, a task). */
  onJump?: (messageId: string) => void
}) {
  const { tok } = useAppTheme()
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(conversation.title ?? '')
  const [description, setDescription] = useState(conversation.description ?? '')
  const [rules, setRules] = useState(conversation.rules ?? '')
  const [confirmLeave, setConfirmLeave] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [reporting, setReporting] = useState(false)

  const myRole = conversation.my_role ?? 'member'
  const runs = myRole === 'owner' || myRole === 'admin'
  const owner = myRole === 'owner'
  const count = conversation.participant_count ?? 0
  const jump = (messageId: string) => {
    onClose()
    onJump?.(messageId)
  }

  /**
   * Put an uploaded photo on the group, or take it off.
   *
   * Applied as soon as the upload is ready rather than behind the edit form's
   * Save: an uploaded image that is never saved is a stored file nothing
   * references, and closing the panel is exactly when that happens.
   */
  const changeImage =
    (key: 'avatar_asset_id' | 'cover_asset_id') => async (assetId: string | null) => {
      await kaluta.messages.editGroup(conversation.id, { [key]: assetId })
      onChanged()
    }

  const run = async (key: string, action: () => Promise<unknown>, failure: string) => {
    setBusy(key)
    setNote(null)
    try {
      await action()
      onChanged()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : failure)
    } finally {
      setBusy(null)
    }
  }

  // A dialog over the conversation, not a panel in place of it: the room
  // stays where it was, and closing this goes straight back to it.
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Group chat information"
      className="fixed inset-0 z-[80] flex items-end justify-center bg-black/55 backdrop-blur-[2px] sm:items-center sm:p-6"
      onClick={onClose}
    >
    <div
      onClick={(event) => event.stopPropagation()}
      className={cn(
        'flex max-h-[94svh] w-full max-w-2xl flex-col overflow-hidden rounded-t-3xl shadow-2xl sm:max-h-[88svh] sm:rounded-3xl',
        tok.card,
      )}
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* The group's face: its cover across the top, its photo over the
            edge of it. A group with neither gets the brand gradient rather
            than an empty grey box. */}
        <div className="relative">
          {conversation.cover_url ? (
            <img src={conversation.cover_url} alt="" className="h-36 w-full object-cover sm:h-48" />
          ) : (
            <div
              aria-hidden="true"
              className="h-36 w-full bg-gradient-to-r from-[#F0C878] via-[#F6EBD3] to-[#9CC3E6] sm:h-48"
            />
          )}
          <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-gradient-to-t from-black/35 to-transparent" />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close group information"
            className="absolute end-3 top-3 grid h-9 w-9 place-items-center rounded-full bg-black/45 text-white backdrop-blur hover:bg-black/60"
          >
            <X size={17} aria-hidden="true" />
          </button>
          <div className="absolute -bottom-11 start-5 sm:start-7">
            {conversation.avatar_url ? (
              <img
                src={conversation.avatar_url}
                alt=""
                className="h-24 w-24 rounded-full object-cover shadow-lg ring-4 ring-[var(--cloud)]"
              />
            ) : (
              <span className="grid h-24 w-24 place-items-center rounded-full bg-gold/25 text-gold-soft shadow-lg ring-4 ring-[var(--cloud)]">
                <UsersRound size={38} aria-hidden="true" />
              </span>
            )}
          </div>
        </div>

        <div className="px-5 pb-5 pt-14 sm:px-7">
        {editing && runs ? (
          <div className="mb-5 space-y-5">
            <ProfileImagePicker
              purpose="avatar"
              url={conversation.avatar_url ?? null}
              displayName={conversation.title ?? 'Group'}
              onChange={changeImage('avatar_asset_id')}
            />
            <ProfileImagePicker
              purpose="cover"
              url={conversation.cover_url ?? null}
              displayName={conversation.title ?? 'Group'}
              onChange={changeImage('cover_asset_id')}
            />
          </div>
        ) : null}

        {editing ? (
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void run(
                'edit',
                async () => {
                  await kaluta.messages.editGroup(conversation.id, {
                    title: title.trim(),
                    description: description.trim(),
                    rules: rules.trim(),
                  })
                  setEditing(false)
                },
                'Could not save the changes',
              )
            }}
            className="space-y-2"
          >
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={140}
              aria-label="Group name"
              placeholder="Group name"
              className={cn('w-full rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm', tok.text)}
            />
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2000}
              rows={3}
              aria-label="What this group is for"
              placeholder="What is this group for?"
              className={cn('w-full resize-none rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm', tok.text)}
            />
            <textarea
              value={rules}
              onChange={(e) => setRules(e.target.value)}
              maxLength={4000}
              rows={4}
              aria-label="Group rules"
              placeholder="Group rules - shown to people before they join or accept an invitation (optional)"
              className={cn('w-full resize-none rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm', tok.text)}
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy === 'edit' || !title.trim()}
                className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-50"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditing(false)
                  setTitle(conversation.title ?? '')
                  setDescription(conversation.description ?? '')
                  setRules(conversation.rules ?? '')
                }}
                className={cn('rounded-full px-4 py-1.5 text-xs font-semibold', tok.mid)}
              >
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <div>
            <h3 className={cn('text-[1.6rem] font-bold leading-tight tracking-[-0.02em]', tok.text)}>
              {conversation.title ?? 'Group'}
            </h3>
            <p className={cn('mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs', tok.low)}>
              <span className="inline-flex items-center gap-1">
                <MessageSquare size={12} aria-hidden="true" />
                Group chat
              </span>
              <span aria-hidden="true">·</span>
              <span className="inline-flex items-center gap-1">
                {(conversation.privacy ?? 'private') === 'public' || conversation.is_welcome ? (
                  <Globe size={12} aria-hidden="true" />
                ) : (
                  <Lock size={12} aria-hidden="true" />
                )}
                {conversation.is_welcome
                  ? 'Everybody on Kinjy'
                  : (conversation.privacy ?? 'private') === 'public'
                    ? 'Anyone with the link can join'
                    : 'Joining needs an admin'}
              </span>
              <span aria-hidden="true">·</span>
              <span>
                {count} {count === 1 ? 'member' : 'members'}
              </span>
              {myRole !== 'member' && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="inline-flex items-center gap-1 text-gold-soft">
                    <ShieldCheck size={11} aria-hidden="true" />
                    You are {myRole === 'owner' ? 'the owner' : `a ${ROLE_LABEL[myRole].toLowerCase()}`}
                  </span>
                </>
              )}
              {conversation.posting === 'admins' && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="inline-flex items-center gap-1">
                    <Megaphone size={11} aria-hidden="true" />
                    Announcements
                  </span>
                </>
              )}
            </p>
            <p className={cn('mt-3 whitespace-pre-wrap text-[0.95rem] leading-relaxed', tok.mid)}>
              {conversation.description || 'No description yet.'}
            </p>
            {conversation.rules && (
              <div className="mt-3 rounded-xl border border-[var(--cloud-border)] px-3 py-2">
                <p className={cn('flex items-center gap-1.5 text-xs font-semibold', tok.text)}>
                  <ScrollText size={12} aria-hidden="true" />
                  Group rules
                </p>
                <p className={cn('mt-1 whitespace-pre-wrap text-sm', tok.mid)}>{conversation.rules}</p>
              </div>
            )}
            {conversation.community_id && (
              <Link
                to={`/communities/${conversation.community_id}`}
                className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-gold-soft hover:underline"
              >
                <UsersRound size={12} aria-hidden="true" />
                Linked to a community
              </Link>
            )}
            {/* Two different things, and members mix them up: say which one this is. */}
            <p className={cn('mt-3 text-[0.72rem] leading-relaxed', tok.low)}>
              A group chat lives in Messages. It is not a Community: being in this chat gives no access to any
              community, paid space or family tree, and leaving it changes nothing there.
            </p>
            {runs && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className={cn(
                  'mt-3 inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-3 py-1 text-xs font-semibold hover:border-gold/40',
                  tok.mid,
                )}
              >
                <Pencil size={12} aria-hidden="true" />
                Edit name, description, rules and pictures
              </button>
            )}
          </div>
        )}

        <GroupSection title="Your notifications" icon={<Bell size={15} aria-hidden="true" />}>
          <GroupNotifications conversation={conversation} onChanged={onChanged} />
        </GroupSection>

        <GroupSection title="Members" icon={<UsersRound size={15} aria-hidden="true" />} badge={<span className={cn('text-xs', tok.low)}>{count}</span>}>
          <GroupMembers conversation={conversation} meId={meId} onChanged={onChanged} />
        </GroupSection>

        {/* Sharing, privacy, the pin and the queue, in this dialog rather
            than a second one stacked on it. */}
        {!conversation.is_welcome && (
          <GroupSection title="Sharing and joining" icon={<Link2 size={15} aria-hidden="true" />}>
            <GroupAccess conversation={conversation} runs={runs} onChanged={onChanged} />
            {runs && <GroupLinkLimits conversation={conversation} onChanged={onChanged} />}
          </GroupSection>
        )}

        {runs && (
          <GroupSection title="Group settings" icon={<Settings2 size={15} aria-hidden="true" />}>
            <GroupRules conversation={conversation} onChanged={onChanged} />
          </GroupSection>
        )}

        <GroupSection title="Pinned messages" icon={<Pin size={15} aria-hidden="true" />}>
          <GroupPins conversationId={conversation.id} onJump={jump} />
        </GroupSection>

        <GroupSection title="Photos, files and links" icon={<Images size={15} aria-hidden="true" />}>
          <GroupFiles conversationId={conversation.id} />
        </GroupSection>

        <GroupSection title="Events and tasks" icon={<CalendarDays size={15} aria-hidden="true" />}>
          <GroupPlans conversationId={conversation.id} onJump={jump} />
        </GroupSection>

        {runs && (
          <GroupSection title="Activity log" icon={<History size={15} aria-hidden="true" />}>
            <GroupAuditLog conversationId={conversation.id} />
          </GroupSection>
        )}

        {note && <p role="status" className="mt-3 text-xs text-amber-200">{note}</p>}
        </div>
      </div>

      <footer className="flex flex-wrap gap-2 border-t border-[var(--cloud-border)] px-5 py-3 sm:px-7">
        <button
          type="button"
          onClick={() => setReporting(true)}
          className={cn('inline-flex items-center justify-center gap-2 rounded-xl border border-[var(--cloud-border)] px-3 py-2 text-sm font-semibold hover:border-red-400/40', tok.mid)}
        >
          <Flag size={14} aria-hidden="true" />
          Report
        </button>
        {owner && !conversation.is_welcome ? (
          <>
            <p className={cn('min-w-0 flex-1 self-center text-xs', tok.low)}>
              {count > 1 ? 'To leave, first make somebody else the owner (Members).' : 'You are the only member.'}
            </p>
            <button
              type="button"
              onClick={() => {
                setDeleteError(null)
                setDeleting(true)
              }}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-red-400/40 px-3 py-2 text-sm font-semibold text-red-300 hover:bg-red-500/10"
            >
              <Trash2 size={14} aria-hidden="true" />
              Delete group
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={busy === 'leave'}
            onClick={() => setConfirmLeave(true)}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:border-red-400/40 hover:text-red-300"
          >
            <LogOut size={14} aria-hidden="true" />
            Leave this group
          </button>
        )}
      </footer>
    </div>
    <ConfirmDialog
      open={confirmLeave}
      onOpenChange={setConfirmLeave}
      title="Leave this group?"
      description="You stop receiving its messages and it leaves your list. The group is told you left. You can come back only if somebody adds or invites you, or through its link."
      confirmLabel="Leave the group"
      onConfirm={() => {
        setConfirmLeave(false)
        void run(
          'leave',
          async () => {
            await kaluta.messages.removeMember(conversation.id, meId)
            onClose()
          },
          'Could not leave the group',
        )
      }}
    />
    {deleting && (
      <TypeToConfirm
        title="Delete this group for everybody?"
        expected={conversation.title ?? ''}
        confirmLabel="Delete the group"
        busy={busy === 'delete'}
        error={deleteError}
        description={
          <>
            <p>
              Every message, photo reference, reaction, pin, poll, event and task in it is deleted from Kinjy’s
              database, for all {count} {count === 1 ? 'member' : 'members'}. It cannot be undone.
            </p>
            <p>Members are told the group was deleted. A record that you deleted it is kept.</p>
          </>
        }
        onClose={() => setDeleting(false)}
        onConfirm={(typed) => {
          setBusy('delete')
          setDeleteError(null)
          kaluta.messages
            .deleteGroup(conversation.id, typed)
            .then(() => {
              setDeleting(false)
              onClose()
              onChanged()
            })
            .catch((err) => setDeleteError(err instanceof ApiError ? err.message : 'Could not delete the group'))
            .finally(() => setBusy(null))
        }}
      />
    )}
    {reporting && (
      <ReportDialog
        conversationId={conversation.id}
        target={{ kind: 'group', id: conversation.id, label: 'this group' }}
        onClose={() => setReporting(false)}
      />
    )}
    </div>,
    document.body,
  )
}

/** The dialog that makes one: a name, what it is for, and who is in it. */
export function NewGroupDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  /** `invited`: how many were sent an invitation rather than added. */
  onCreated: (conversationId: string, invited: number) => void
}) {
  const { tok } = useAppTheme()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [rules, setRules] = useState('')
  // Uploaded before the group exists, applied when it is created.
  const [avatar, setAvatar] = useState<{ id: string; url: string | null } | null>(null)
  const [cover, setCover] = useState<{ id: string; url: string | null } | null>(null)
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<Array<PersonBrief & { user_id: string }>>([])
  const [chosen, setChosen] = useState<Array<PersonBrief & { user_id: string }>>([])
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

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
          const picked = new Set(chosen.map((p) => p.user_id))
          setFound(page.items.filter((p) => !picked.has(p.user_id)))
        })
        .catch(() => setFound([]))
    }, 200)
    return () => window.clearTimeout(timer)
  }, [query, chosen])

  const create = async () => {
    setBusy(true)
    setNote(null)
    try {
      const group = await kaluta.messages.createGroup({
        title: title.trim(),
        description: description.trim() || undefined,
        rules: rules.trim() || undefined,
        participantIds: chosen.map((p) => p.user_id),
        avatarAssetId: avatar?.id,
        coverAssetId: cover?.id,
      })
      onCreated(group.id, group.invited?.length ?? 0)
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not create the group')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="New group"
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className={cn('max-h-[80svh] w-full max-w-md overflow-hidden rounded-2xl', tok.card)}
      >
        <header className="flex items-center justify-between gap-2 border-b border-[var(--cloud-border)] px-4 py-3">
          <h2 className={cn('text-sm font-semibold', tok.text)}>New group</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className={cn('rounded-full p-1.5 hover:bg-text-hi/[0.07]', tok.mid)}
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        <div className="max-h-[calc(80svh-128px)] space-y-3 overflow-y-auto p-4">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={140}
            autoFocus
            aria-label="Group name"
            placeholder="Group name"
            className={cn(
              'w-full rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm',
              tok.text,
            )}
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={2000}
            rows={2}
            aria-label="What this group is for"
            placeholder="What is this group for? (optional)"
            className={cn(
              'w-full resize-none rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm',
              tok.text,
            )}
          />
          <textarea
            value={rules}
            onChange={(e) => setRules(e.target.value)}
            maxLength={4000}
            rows={2}
            aria-label="Group rules"
            placeholder="Group rules, shown before people join (optional)"
            className={cn(
              'w-full resize-none rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm',
              tok.text,
            )}
          />
          <details className="rounded-xl border border-[var(--cloud-border)] px-3 py-2">
            <summary className={cn('cursor-pointer text-xs font-semibold', tok.mid)}>Photo and cover (optional)</summary>
            <div className="mt-3 space-y-4">
              <ProfileImagePicker
                purpose="avatar"
                url={avatar?.url ?? null}
                displayName={title || 'Group'}
                onChange={async (id, url) => setAvatar(id ? { id, url: url ?? null } : null)}
              />
              <ProfileImagePicker
                purpose="cover"
                url={cover?.url ?? null}
                displayName={title || 'Group'}
                onChange={async (id, url) => setCover(id ? { id, url: url ?? null } : null)}
              />
            </div>
          </details>

          {chosen.length > 0 && (
            <ul className="flex flex-wrap gap-1.5">
              {chosen.map((person) => (
                <li key={person.user_id}>
                  <button
                    type="button"
                    onClick={() => setChosen((c) => c.filter((p) => p.user_id !== person.user_id))}
                    className="inline-flex items-center gap-1.5 rounded-full bg-text-hi/[0.07] px-2.5 py-1 text-xs"
                  >
                    <span className={tok.text}>{person.display_name}</span>
                    <X size={11} aria-hidden="true" className={tok.low} />
                  </button>
                </li>
              ))}
            </ul>
          )}

          <label className="relative flex items-center">
            <Search size={14} className="pointer-events-none absolute start-3 text-text-low" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Add people by name…"
              aria-label="Search for people to add"
              className={cn(
                'w-full rounded-full border border-[var(--cloud-border)] bg-transparent py-2 pe-3 ps-9 text-sm',
                tok.text,
              )}
            />
          </label>

          <ul className="space-y-1">
            {found.map((person) => (
              <li key={person.user_id}>
                <button
                  type="button"
                  onClick={() => {
                    setChosen((c) => [...c, person])
                    setQuery('')
                  }}
                  className="flex w-full items-center gap-2.5 rounded-xl p-2 text-start hover:bg-text-hi/[0.05]"
                >
                  <MemberAvatar
                    handle={person.handle}
                    displayName={person.display_name}
                    avatarUrl={person.avatar_url}
                    size={32}
                  />
                  <span className={cn('min-w-0 flex-1 truncate text-sm', tok.text)}>
                    {person.display_name}
                  </span>
                  <Check size={14} className="shrink-0 text-sky" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>

          {note && <p className="text-xs text-amber-200">{note}</p>}
          {/* Said before they find out by being refused: the server only lets
              you add people who accept messages from you. */}
          <p className={cn('text-[0.7rem]', tok.low)}>
            Each person’s settings decide: people who let you add them are added, people who only accept
            invitations are invited and join when they accept. Group messages travel encrypted and are
            stored encrypted on Kinjy’s servers, but they are not end-to-end encrypted: Kinjy’s servers can
            read them, which is what makes search and reports possible.
          </p>
        </div>

        <footer className="flex justify-end gap-2 border-t border-[var(--cloud-border)] px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className={cn('rounded-full px-4 py-2 text-sm font-semibold', tok.mid)}
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy || !title.trim() || chosen.length === 0}
            onClick={() => void create()}
            className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-50"
          >
            Create
          </button>
        </footer>
      </div>
    </div>
  )
}
