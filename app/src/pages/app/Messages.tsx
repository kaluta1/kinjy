import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  CheckCheck,
  Clock,
  Download,
  File as FileIcon,
  FileText,
  Lock,
  MessageSquare,
  Mic,
  Pencil,
  Pin,
  Plus,
  Search,
  Send,
  ShieldOff,
  Square,
  Trash2,
  Users,
  Wifi,
  WifiOff,
  X,
} from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { useAppTheme } from '@/components/appdemo/theme'
import AttachmentMenu from '@/components/social/AttachmentMenu'
import MediaLightbox from '@/components/social/MediaLightbox'
import VoiceNotePlayer from '@/components/social/VoiceNotePlayer'
import {
  MessageMenu,
  MessageMenuButton,
  ReactionChips,
} from '@/components/social/MessageReactions'
import EmojiStickerButton from '@/components/social/EmojiStickerPanel'
import StickerFace from '@/components/social/StickerFace'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import AlertsToggle from '@/components/app/AlertsToggle'
import { playMessageChime, primeSound } from '@/lib/chime'
import MessageInput from '@/components/ui-kit/MessageInput'
import { formatChatText } from '@/lib/chatFormat'
import {
  ReplyBanner,
  ReplyButton,
  ReplyQuote,
  SwipeToReply,
  type QuoteTarget,
} from '@/components/social/MessageReply'
import { ConversationSkeleton, ThreadSkeleton } from '@/components/social/MessageSkeleton'
import MemberAvatar from '@/components/social/MemberAvatar'
import GroupPanel, { NewGroupDialog } from '@/components/social/GroupPanel'
import { useApi } from '@/hooks/useApi'
import { useRealtime } from '@/hooks/useRealtime'
import { useAuth } from '@/hooks/useAuth'
import { useMyProfile } from '@/hooks/useMyProfile'
import {
  ApiError,
  kaluta,
  type Conversation,
  type Message,
  type PersonBrief,
  type Presence,
  type Sticker,
} from '@/lib/api'
import LinkPreview from '@/components/social/LinkPreview'
import { LINK_RE, isLink, splitTrailing } from '@/lib/linkify'
import { FEATURES } from '@/lib/features'
import { realtime } from '@/lib/realtime'
import { cn } from '@/lib/utils'

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const list = window.matchMedia(query)
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return matches
}

/** A message as the thread holds it: possibly still on its way to the server. */
type ChatMessage = Message & {
  client_id?: string | null
  status?: 'sending' | 'failed'
  /** Upload progress, 0–1, while an attachment is on its way. */
  progress?: number
  /** A local object URL, so a photo shows before its upload finishes. */
  preview_url?: string | null
}

/** The server pages 50 at a time; a full page means there may be more above. */
const PAGE = 50
/** How long a "typing" hint lasts without a fresh frame. */
const TYPING_TTL_MS = 5000
/** The server relays one typing frame per 2s; sending more is wasted. */
const TYPING_SEND_EVERY_MS = 2000
/** media-service's limit, checked here too so a 2 GB file fails at once, not at 99%. */
const MAX_FILE_BYTES = 200 * 1024 * 1024
/** Consecutive messages from one sender within this window read as one burst. */
const BURST_MS = 5 * 60 * 1000

// --- dates -----------------------------------------------------------------
//
// Formatted in the app's language (the switcher in the top bar), not the
// browser's: a French interface saying "Yesterday" — or an English one saying
// "hier" — reads as a bug.

const DAY_MS = 86_400_000

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/** The first address in a message, for the card under it. */
function firstLink(body: string): string | null {
  for (const part of body.split(LINK_RE)) {
    if (part && isLink(part)) return splitTrailing(part).href
  }
  return null
}

/** Whole calendar days between a date and today: 0 today, 1 yesterday. */
function daysAgo(iso: string): number {
  return Math.round((startOfDay(new Date()) - startOfDay(new Date(iso))) / DAY_MS)
}

const capitalise = (text: string) => text.charAt(0).toLocaleUpperCase() + text.slice(1)

function timeOf(iso: string, locale: string): string {
  return new Date(iso).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
}

/** The separator between days in a thread: "Today", "Yesterday", "Monday", "Mon 22 September". */
function dayLabel(iso: string, locale: string): string {
  const date = new Date(iso)
  const days = daysAgo(iso)
  if (days <= 1) {
    return capitalise(new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(-days, 'day'))
  }
  if (days < 7) return capitalise(date.toLocaleDateString(locale, { weekday: 'long' }))
  return capitalise(
    date.toLocaleDateString(locale, {
      weekday: 'short',
      day: 'numeric',
      month: 'long',
      ...(date.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}),
    }),
  )
}

/** The stamp in the conversation list: a time today, then a day, then a date. */
function listStamp(iso: string, locale: string): string {
  const date = new Date(iso)
  const days = daysAgo(iso)
  if (days === 0) return timeOf(iso, locale)
  if (days === 1) return capitalise(new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(-1, 'day'))
  if (days < 7) return capitalise(date.toLocaleDateString(locale, { weekday: 'short' }))
  return date.toLocaleDateString(locale, {
    day: 'numeric',
    month: 'short',
    ...(date.getFullYear() !== new Date().getFullYear() ? { year: '2-digit' } : {}),
  })
}

/** The full moment, for a hover title: the bubble itself shows only the time. */
function fullStamp(iso: string, locale: string): string {
  return new Date(iso).toLocaleString(locale, { dateStyle: 'full', timeStyle: 'short' })
}

function lastSeenLabel(presence: Presence | undefined, locale: string): string | null {
  if (!presence) return null
  if (presence.online) return 'Online'
  if (!presence.last_seen) return 'Offline'
  const minutes = Math.round((Date.now() - new Date(presence.last_seen).getTime()) / 60000)
  if (minutes < 1) return 'Last seen just now'
  if (minutes < 60) return `Last seen ${minutes} min ago`
  const days = daysAgo(presence.last_seen)
  if (days === 0) return `Last seen today at ${timeOf(presence.last_seen, locale)}`
  return `Last seen ${listStamp(presence.last_seen, locale)} at ${timeOf(presence.last_seen, locale)}`
}

// --- attachments -------------------------------------------------------------

/** Mirrors media-service's classification, for the bubble shown before it answers. */
function kindOf(file: File): string {
  const type = file.type.toLowerCase()
  if (type.startsWith('image/') && !type.includes('svg')) return 'image'
  if (type.startsWith('video/')) return 'video'
  if (type.startsWith('audio/')) return 'audio'
  if (type === 'application/pdf') return 'document'
  return 'file'
}

function sizeLabel(bytes: number | null | undefined): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`
}

function extensionOf(name: string | null | undefined): string {
  const match = /\.([a-z0-9]{1,6})$/i.exec(name ?? '')
  return match ? match[1].toUpperCase() : 'FILE'
}

/** The recorder's own container: webm/opus in Chrome and Firefox, mp4/aac in Safari. */
function recorderType(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined
  return ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg'].find((type) =>
    MediaRecorder.isTypeSupported(type),
  )
}

/**
 * The time of a message, and for my own the delivery ticks: one line, so a short
 * message or a voice note does not grow a row of its own for it.
 * `onOwnBubble`: drawn on my own bubble, which is the platform's gold and the
 * same blue in every theme - so the meta is white at reduced opacity rather
 * than the thread's grey, which disappears against it.
 */
function MessageMeta({
  message,
  locale,
  mine,
  onOwnBubble,
  isRead,
}: {
  message: ChatMessage
  locale: string
  mine: boolean
  onOwnBubble: boolean
  isRead: boolean
}) {
  const sending = message.status === 'sending'
  const failed = message.status === 'failed'
  return (
    <span
      className={cn(
        'inline-flex select-none items-center gap-1 whitespace-nowrap text-[0.68rem] leading-none',
        onOwnBubble ? 'text-ink/60' : 'text-text-low',
      )}
      title={fullStamp(message.created_at, locale)}
    >
      <span>
        {sending
          ? message.media_kind
            ? `Uploading ${Math.round((message.progress ?? 0) * 100)}%`
            : 'Sending…'
          : timeOf(message.created_at, locale)}
        {/* Said, not hidden: an edit nobody can see is a way to change what
            somebody appears to have agreed to after they agreed to it. */}
        {message.edited_at && !message.deleted && ' · edited'}
      </span>
      {mine && !failed && (
        <span className="inline-flex items-center">
          {sending ? (
            <Clock size={11} className="opacity-70" aria-label="Sending" />
          ) : isRead ? (
            <CheckCheck size={13} className={onOwnBubble ? 'text-ink' : 'text-gold-soft'} aria-label="Seen" />
          ) : (
            <CheckCheck size={13} className="opacity-45" aria-label="Delivered" />
          )}
        </span>
      )}
    </span>
  )
}

/** One attachment, drawn for what it is. */
function Attachment({
  message,
  onOpenImage,
  sender,
  mine,
  metaSlot,
}: {
  message: ChatMessage
  onOpenImage: (url: string) => void
  sender?: { handle?: string; display_name?: string; avatar_url?: string | null } | null
  mine: boolean
  metaSlot?: React.ReactNode
}) {
  const url = message.media_url ?? message.preview_url ?? null
  const kind = message.media_kind ?? 'file'

  // Held back by the server: this reader has not accepted the group yet.
  if (message.media_withheld) {
    return (
      <p className="flex items-center gap-2 rounded-card-sm border border-dashed border-[var(--cloud-border)] px-3 py-2 text-sm text-text-low">
        <Lock size={13} aria-hidden="true" />
        {kind === 'image' ? 'A photo' : kind === 'video' ? 'A video' : 'An attachment'} is hidden until you accept
        this group.
      </p>
    )
  }

  if (url && kind === 'image') {
    return (
      <button type="button" onClick={() => onOpenImage(url)} className="block overflow-hidden rounded-card-sm">
        <img
          src={url}
          alt={message.media_name ?? 'Photo'}
          loading="lazy"
          className="max-h-72 w-auto max-w-full object-cover"
        />
      </button>
    )
  }
  if (url && kind === 'video') {
    return <video src={url} controls preload="metadata" className="max-h-80 max-w-full rounded-card-sm" />
  }
  if (url && kind === 'audio') {
    return (
      <VoiceNotePlayer
        src={url}
        senderAvatarUrl={sender?.avatar_url}
        senderDisplayName={sender?.display_name}
        senderHandle={sender?.handle}
        mine={mine}
        metaSlot={metaSlot}
      />
    )
  }

  // Documents and everything else: a card that says what it is and downloads it.
  const Icon = kind === 'document' ? FileText : FileIcon
  const card = (
    <span className="flex min-w-[200px] max-w-[280px] items-center gap-3 rounded-card-sm border border-white/10 bg-ink-2/50 px-3 py-2.5">
      <Icon size={22} className="shrink-0 text-gold-soft" aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-text-hi">{message.media_name ?? 'File'}</span>
        <span className="caption block">
          {extensionOf(message.media_name)}
          {message.media_size ? ` · ${sizeLabel(message.media_size)}` : ''}
        </span>
      </span>
      {message.media_url && <Download size={16} className="shrink-0 text-text-mid" aria-hidden="true" />}
    </span>
  )
  return message.media_url ? (
    <a
      href={message.media_url}
      target="_blank"
      rel="noopener noreferrer"
      download={message.media_name ?? true}
      aria-label={`Download ${message.media_name ?? 'file'}`}
    >
      {card}
    </a>
  ) : (
    card
  )
}

/**
 * Fold incoming messages into the thread: once per id, in time order.
 *
 * The same message can arrive by three routes — the POST response, the socket
 * frame (which may beat the POST response back), and the catch-up after a
 * reconnect — so every route goes through here rather than appending. A pending
 * bubble is replaced by the stored message carrying the same `client_id`.
 */
function mergeMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  let next = current
  for (const message of incoming) {
    const known = next.findIndex((m) => m.id === message.id)
    if (known >= 0) {
      // Already here — possibly from the POST response, which knows less than
      // the socket frame or the list (no attachment URL). Fill in, never erase.
      const merged = { ...next[known], ...message, status: undefined }
      next = next.map((m, i) => (i === known ? merged : m))
      continue
    }
    const pending = message.client_id
      ? next.findIndex((m) => m.client_id === message.client_id)
      : -1
    next =
      pending >= 0
        ? next.map((m, i) =>
            // Keep the local preview until the stored copy has its own URL.
            i === pending ? { ...m, ...message, status: undefined, progress: undefined } : m,
          )
        : [...next, message]
  }
  if (next === current) return current
  return [...next].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  )
}

/** What to call a thread: its title, or simply the other people in it. */
function titleOf(conversation: Conversation, others: string[]): string {
  if (conversation.title) return conversation.title
  const names = others
    .map((id) => conversation.profiles?.[id]?.display_name)
    .filter((name): name is string => Boolean(name))
  if (names.length) return names.join(', ')
  // Only when the profile could not be resolved at all — better a short id than
  // an empty row, but it should be rare enough to notice.
  return others[0] ? `@${others[0].slice(0, 12)}` : 'Conversation'
}

/**
 * The face of a conversation.
 *
 * A group has its own photo and its own name, so it gets both. It used to
 * borrow the first other participant's avatar, which meant a group wore one of
 * its members as its face - and an uploaded group photo was stored and then
 * never drawn anywhere. With no photo it falls back to the initials of the
 * group's name, which is at least about the group.
 *
 * A direct conversation is the other person, as before.
 */
function avatarFor(
  conversation: Conversation,
  others: string[],
): { handle: string; display_name: string; avatar_url: string | null } | PersonBrief | undefined {
  if (conversation.kind === 'group') {
    return {
      handle: '',
      display_name: titleOf(conversation, others),
      avatar_url: conversation.avatar_url ?? null,
    }
  }
  return conversation.profiles?.[others[0]]
}

/** An avatar with the member's online dot. Never a link here: a tap means "write". */
/**
 * Messenger's grouping: inside a run from one person the corners that face
 * their side tighten, so consecutive bubbles read as one block.
 */
function bubbleShape(mine: boolean, first: boolean, last: boolean): string {
  if (mine) return cn('rounded-[18px]', !first && 'rounded-se-[5px]', !last && 'rounded-ee-[5px]')
  return cn('rounded-[18px]', !first && 'rounded-ss-[5px]', !last && 'rounded-es-[5px]')
}

function PresenceAvatar({
  profile,
  online,
  size = 36,
  group = false,
}: {
  profile: PersonBrief | { handle: string; display_name: string; avatar_url: string | null } | null | undefined
  online?: boolean
  size?: number
  /** A room rather than a person: only changes what is drawn with no photo. */
  group?: boolean
}) {
  return (
    <span className="relative inline-flex shrink-0">
      <MemberAvatar
        handle={null}
        displayName={profile?.display_name ?? profile?.handle}
        avatarUrl={profile?.avatar_url}
        size={size}
        group={group}
      />
      {online && (
        <span
          aria-label="Online"
          className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-ink bg-emerald-400"
        />
      )}
    </span>
  )
}

export default function Messages() {
  const { user } = useAuth()
  const me = useMyProfile()
  const { lang: locale } = useAppTheme()
  const [params, setParams] = useSearchParams()
  const requestedId = params.get('c')
  const paramsRef = useRef(params)
  paramsRef.current = params
  const navigate = useNavigate()
  // Below `lg` the list and the thread are separate screens; ?c= says which.
  const wide = useMediaQuery('(min-width: 1024px)')
  const wideRef = useRef(wide)
  wideRef.current = wide
  const showThread = wide || Boolean(requestedId)
  /** Whether opening the current thread added a history entry to go back over. */
  const pushedThread = useRef(false)

  const conversations = useApi<{ items: Conversation[] }>(() => kaluta.messages.conversations(), [])
  const connections = useApi(() => kaluta.connections.list(), [])

  const [activeId, setActiveId] = useState<string | null>(null)
  // Socket callbacks are created once; refs keep them reading live values.
  const activeIdRef = useRef<string | null>(null)
  activeIdRef.current = activeId
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const messagesRef = useRef<ChatMessage[]>([])
  messagesRef.current = messages
  const [hasOlder, setHasOlder] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [loadingThread, setLoadingThread] = useState(false)
  const [draft, setDraft] = useState('')
  // The message the next send answers, and the one just jumped to from a quote.
  const [replyTo, setReplyTo] = useState<ChatMessage | null>(null)
  const [highlightId, setHighlightId] = useState<string | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // The open message menu, anchored to its bubble by id. By id rather than by
  // a captured rect: the panel is fixed to the viewport, so a rect measured at
  // the moment of the click leaves it behind as soon as the thread scrolls.
  const [menuFor, setMenuFor] = useState<string | null>(null)
  // The sticker packs, as the server curates them.
  const stickerPacks = useApi(() => kaluta.messages.stickers(), [])
  /** A sticker from the catalogue by its id, for a bubble drawn before the server has answered. */
  const stickerById = (id: string | null | undefined): Sticker | null => {
    if (!id) return null
    for (const pack of stickerPacks.data?.packs ?? []) {
      const found = pack.stickers.find((s) => s.id === id)
      if (found) return found
    }
    return null
  }
  // The message being edited and the text so far - apart from the composer's
  // draft, so abandoning an edit does not swallow what was already typed below.
  const [editing, setEditing] = useState<ChatMessage | null>(null)
  const [editDraft, setEditDraft] = useState('')
  // The message awaiting a yes, so the dialog can name what is about to happen.
  const [confirmDelete, setConfirmDelete] = useState<ChatMessage | null>(null)
  // Message ids already announced. A reconnect replays everything missed, and
  // without this the catch-up is a burst of chimes.
  const seenForSound = useRef<Set<string>>(new Set())
  /**
   * Who wrote what is on screen, as the thread itself reports it.
   *
   * The conversation list used to carry a profile for every member of every
   * room. The welcome group holds the whole platform, so that was most of the
   * platform on every poll of the list. The senders of the page being read are
   * a handful of people, and they are the only ones a thread has to name.
   */
  const [senders, setSenders] = useState<Record<string, PersonBrief>>({})
  const [peer, setPeer] = useState('')
  const [composing, setComposing] = useState(false)
  const [creatingGroup, setCreatingGroup] = useState(false)
  // The group's own screen: who is in it, who runs it, what it is for.
  const [showGroup, setShowGroup] = useState(false)
  // Narrowing the list you already have: nothing is fetched.
  const [chatQuery, setChatQuery] = useState('')
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [results, setResults] = useState<Array<PersonBrief & { user_id: string }>>([])
  const [searched, setSearched] = useState(false)
  // Kept apart from `error`, which renders in the thread pane on the right —
  // a refusal to *start* a conversation belongs next to the person you clicked,
  // not in a column you are not looking at.
  const [startError, setStartError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [presence, setPresence] = useState<Record<string, Presence>>({})
  /** conversation -> member -> expiry (ms). */
  const [typing, setTyping] = useState<Record<string, Record<string, number>>>({})
  /** Live read receipts, layered over each conversation's `read_state`. */
  const [reads, setReads] = useState<Record<string, Record<string, string>>>({})
  const lastTypingSent = useRef(0)

  // --- scrolling ------------------------------------------------------------
  const listRef = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const scrollMode = useRef<'bottom' | 'opened' | 'keep' | { restoreFrom: number }>('opened')

  const onScroll = () => {
    const el = listRef.current
    if (el) nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }

  /**
   * Where the member had got to when they opened this conversation.
   *
   * Captured on opening and not read again, because `markRead` moves the mark
   * forward about a second after the thread appears - ask it later and every
   * conversation looks as though it had been read to the end.
   */
  const readMarkOnOpen = useRef<string | null>(null)

  // After every change to the thread: stay at the bottom when the reader was
  // there (or just sent something), hold position when history was prepended,
  // and otherwise leave the reader where they are — yanking someone who
  // scrolled up to read back down to a new message loses their place.
  useLayoutEffect(() => {
    const el = listRef.current
    if (!el) return
    const mode = scrollMode.current
    if (typeof mode === 'object') {
      el.scrollTop = el.scrollHeight - mode.restoreFrom
    } else if (mode === 'opened') {
      // Opening a conversation lands on the newest message.
      //
      // It used to land on the first thing not yet read, which sounds kinder
      // and is not: a thread you last opened a month ago has a read mark a
      // month old, so "the first unread" is a message near the top and the
      // conversation opened in its own history. The member then scrolls down
      // past everything to reach the part they came for, which is where the
      // conversation actually is. Every messenger opens at the bottom for
      // that reason.
      //
      // What was behind the request - not losing your place - is served by
      // the unread divider below, which marks where you stopped without
      // moving you there.
      el.scrollTop = el.scrollHeight
    } else if (nearBottom.current) {
      el.scrollTop = el.scrollHeight
    }
    scrollMode.current = 'keep'
  }, [messages, user?.id])

  // --- reading ---------------------------------------------------------------
  /** Only when the thread is actually in front of the member. */
  const markRead = useCallback((conversationId: string) => {
    if (document.visibilityState !== 'visible') return
    void kaluta.messages.markRead(conversationId).catch(() => undefined)
  }, [])

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && activeIdRef.current) {
        markRead(activeIdRef.current)
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [markRead])

  /**
   * `how` decides the history entry. On a phone the list and the thread are
   * two screens, so opening a thread from the list pushes one — the back
   * gesture must return to the list, not leave Messages. On a wide screen
   * both are visible and the URL only records which thread is open.
   */
  const openThread = useCallback(
    async (conversationId: string, how: 'user' | 'url' = 'user') => {
      // Opening a thread by hand is a gesture, and a gesture is the only moment a
      // browser lets audio start. Unlocking here means the first message to
      // arrive can be heard; doing it later means the first one is always silent.
      if (how === 'user') primeSound()
      setActiveId(conversationId)
      activeIdRef.current = conversationId
      setReplyTo(null)
      if (how !== 'url') {
        const push = how === 'user' && !wideRef.current && !paramsRef.current.get('c')
        pushedThread.current = push
        setParams(
          (current) => {
            const next = new URLSearchParams(current)
            next.set('c', conversationId)
            return next
          },
          { replace: !push },
        )
      }
      setLoadingThread(true)
      setError(null)
      setMessages([])
      try {
        const page = await kaluta.messages.list(conversationId)
        if (activeIdRef.current !== conversationId) return
        scrollMode.current = 'opened'
        setSenders(page.profiles ?? {})
        setMessages(page.items)
        setHasOlder(page.items.length >= PAGE)
        markRead(conversationId)
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not load the conversation')
      } finally {
        setLoadingThread(false)
      }
    },
    [markRead, setParams],
  )

  // Which thread to show: only the one the URL names (a notification, the
  // Message button on a profile, a tap on the list). Nothing opens by itself:
  // opening a thread marks it read, so the member chooses what to read — and
  // on any screen size, the list comes first.
  useEffect(() => {
    if (requestedId) {
      if (requestedId !== activeIdRef.current) void openThread(requestedId, 'url')
      return
    }
    // No conversation named (first visit, or back on the list): nothing is open
    // and nothing is being read.
    activeIdRef.current = null
    setActiveId(null)
  }, [requestedId, openThread])

  const backToList = () => {
    if (pushedThread.current) {
      pushedThread.current = false
      navigate(-1)
    } else {
      setParams(
        (current) => {
          const next = new URLSearchParams(current)
          next.delete('c')
          return next
        },
        { replace: true },
      )
    }
  }

  const loadOlder = async () => {
    const conversationId = activeIdRef.current
    const oldest = messagesRef.current.find((m) => !m.status)
    if (!conversationId || !oldest) return
    setLoadingOlder(true)
    try {
      const page = await kaluta.messages.list(conversationId, { before: oldest.id })
      if (activeIdRef.current !== conversationId) return
      // Older history can be from people who have not spoken since.
      setSenders((current) => ({ ...current, ...(page.profiles ?? {}) }))
      const el = listRef.current
      scrollMode.current = { restoreFrom: el ? el.scrollHeight - el.scrollTop : 0 }
      setMessages((current) => mergeMessages(current, page.items))
      setHasOlder(page.items.length >= PAGE)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load older messages')
    } finally {
      setLoadingOlder(false)
    }
  }

  // --- sending ---------------------------------------------------------------
  /** Files waiting for (re)delivery, by client id — what "tap to retry" re-sends. */
  const filesRef = useRef(new Map<string, { file: File; body: string | null }>())
  /** Object URLs made for local previews, released when the page goes. */
  const previewsRef = useRef(new Set<string>())
  useEffect(() => {
    const previews = previewsRef.current
    return () => previews.forEach((url) => URL.revokeObjectURL(url))
  }, [])

  /**
   * Upload (if there is a file), then send, in the background — the bubble is
   * already on screen. The client id travels with the request, so a retry
   * after a lost response finds the stored message instead of sending it twice.
   */
  const deliver = useCallback(
    async (
      conversationId: string,
      clientId: string,
      body: string | null,
      file?: File,
      replyToId?: string | null,
      stickerId?: string | null,
    ) => {
      // Clear the last complaint before making a new attempt. It was only ever
      // set and never unset, so one failed send left "could not send" on
      // screen through every message that went out afterwards - telling
      // somebody their conversation is broken while it plainly is not.
      setError(null)
      try {
        let mediaId: string | undefined
        if (file) {
          const asset = await kaluta.media.uploadWithProgress(
            file,
            (fraction) =>
              setMessages((current) =>
                current.map((m) => (m.client_id === clientId && m.status ? { ...m, progress: fraction } : m)),
              ),
            { purpose: 'chat' },
          )
          mediaId = asset.id
        }
        const created = await kaluta.messages.send(conversationId, { body, mediaId, clientId, replyToId, stickerId })
        filesRef.current.delete(clientId)
        if (activeIdRef.current !== conversationId) return
        // Usually the socket frame has already brought the full message; this
        // covers the case where it has not. Only the fields known here are
        // set, so it never erases what the frame filled in.
        setMessages((current) =>
          mergeMessages(current, [
            {
              id: created.id,
              client_id: clientId,
              sender_id: user!.id,
              encrypted: false,
              ciphertext_b64: null,
              body,
              kind: stickerId ? 'sticker' : file ? 'media' : 'text',
              sticker_id: stickerId ?? null,
              reply_to_id: replyToId ?? null,
              created_at: created.created_at,
            },
          ]),
        )
      } catch (err) {
        setMessages((current) =>
          current.map((m) => (m.client_id === clientId && m.status ? { ...m, status: 'failed' } : m)),
        )
        setError(err instanceof ApiError ? err.message : 'Could not send — check your connection.')
      }
    },
    [user],
  )

  /** One message per file; the caption rides on the first. */
  const sendFiles = (files: File[], caption: string) => {
    const conversationId = activeIdRef.current
    if (!conversationId || !user || !files.length) return
    const tooBig = files.find((file) => file.size > MAX_FILE_BYTES)
    if (tooBig) {
      setError(`“${tooBig.name}” is larger than the 200 MB limit.`)
      return
    }
    setError(null)
    scrollMode.current = 'bottom'
    const pending: ChatMessage[] = files.map((file, index) => {
      const clientId = crypto.randomUUID()
      const body = index === 0 && caption ? caption : null
      filesRef.current.set(clientId, { file, body })
      const replyToId = index === 0 ? replyTo?.id : null
      const kind = kindOf(file)
      const preview = ['image', 'video', 'audio'].includes(kind) ? URL.createObjectURL(file) : null
      if (preview) previewsRef.current.add(preview)
      return {
        id: `local_${clientId}`,
        client_id: clientId,
        status: 'sending',
        progress: 0,
        preview_url: preview,
        sender_id: user.id,
        encrypted: false,
        ciphertext_b64: null,
        body,
        kind: 'media',
        reply_to_id: replyToId ?? null,
        media_kind: kind,
        media_name: file.name,
        media_type: file.type,
        media_size: file.size,
        created_at: new Date().toISOString(),
      }
    })
    setMessages((current) => [...current, ...pending])
    // One after another: parallel uploads share the same uplink, so none of
    // them finishes sooner, and they would land out of order.
    void (async () => {
      for (const message of pending) {
        const entry = filesRef.current.get(message.client_id!)
        if (entry) await deliver(conversationId, message.client_id!, entry.body, entry.file, message.reply_to_id)
      }
    })()
  }

  const [staged, setStaged] = useState<File[]>([])
  const stage = (files: FileList | File[] | null) => {
    // Copied now, not inside the updater: a FileList is live, and the input
    // is cleared right after this call, which would empty it first.
    const picked = files ? Array.from(files) : []
    if (!picked.length) return
    setStaged((current) => [...current, ...picked].slice(0, 10))
  }

  // Called by the form's submit and by Enter in the composer, which is not a
  // submit now that the box is a textarea.
  const send = (event?: React.FormEvent) => {
    event?.preventDefault()
    const conversationId = activeIdRef.current
    const text = draft.trim()
    if (!conversationId || !user) return
    if (staged.length) {
      sendFiles(staged, text)
      setStaged([])
      setDraft('')
      setReplyTo(null)
      return
    }
    if (!text) return
    const replyToId = replyTo?.id ?? null
    setReplyTo(null)
    setDraft('')
    setError(null)
    const clientId = crypto.randomUUID()
    scrollMode.current = 'bottom'
    setMessages((current) => [
      ...current,
      {
        id: `local_${clientId}`,
        client_id: clientId,
        status: 'sending',
        sender_id: user.id,
        encrypted: false,
        ciphertext_b64: null,
        body: text,
        kind: 'text',
        reply_to_id: replyToId,
        created_at: new Date().toISOString(),
      },
    ])
    void deliver(conversationId, clientId, text, undefined, replyToId)
  }

  const retry = (message: ChatMessage) => {
    const conversationId = activeIdRef.current
    if (!conversationId || !message.client_id) return
    const entry = filesRef.current.get(message.client_id)
    if (!entry && !message.body && !message.sticker_id) return
    setError(null)
    setMessages((current) =>
      current.map((m) => (m.client_id === message.client_id ? { ...m, status: 'sending', progress: 0 } : m)),
    )
    void deliver(
      conversationId,
      message.client_id,
      entry ? entry.body : message.body,
      entry?.file,
      message.reply_to_id,
      message.sticker_id,
    )
  }

  /** A sticker from the panel, sent as a message of its own (answering the message being replied to, if any). */
  const sendSticker = (stickerId: string) => {
    const conversationId = activeIdRef.current
    if (!conversationId || !user) return
    const replyToId = replyTo?.id ?? null
    setReplyTo(null)
    setError(null)
    const clientId = crypto.randomUUID()
    scrollMode.current = 'bottom'
    setMessages((current) => [
      ...current,
      {
        id: `local_${clientId}`,
        client_id: clientId,
        status: 'sending',
        sender_id: user.id,
        encrypted: false,
        ciphertext_b64: null,
        body: null,
        kind: 'sticker',
        sticker_id: stickerId,
        sticker: stickerById(stickerId),
        reply_to_id: replyToId,
        created_at: new Date().toISOString(),
      },
    ])
    void deliver(conversationId, clientId, null, undefined, replyToId, stickerId)
  }

  // --- voice messages ------------------------------------------------------------
  const [recordingSince, setRecordingSince] = useState<number | null>(null)
  const [, setTick] = useState(0)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const discardRecording = useRef(false)

  useEffect(() => {
    if (recordingSince === null) return
    const timer = window.setInterval(() => setTick((n) => n + 1), 500)
    return () => window.clearInterval(timer)
  }, [recordingSince])

  const startRecording = async () => {
    const type = recorderType()
    if (!type || !navigator.mediaDevices?.getUserMedia) {
      setError('This browser cannot record voice messages.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const recorder = new MediaRecorder(stream, { mimeType: type })
      const chunks: Blob[] = []
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data)
      }
      recorder.onstop = () => {
        // Release the microphone at once — the browser's "recording" dot
        // staying on after the message is sent is how trust gets lost.
        stream.getTracks().forEach((track) => track.stop())
        setRecordingSince(null)
        recorderRef.current = null
        if (discardRecording.current || !chunks.length) return
        const base = type.split(';')[0]
        const extension = base.split('/')[1] === 'mp4' ? 'm4a' : base.split('/')[1]
        sendFiles([new File(chunks, `voice-message.${extension}`, { type: base })], '')
      }
      discardRecording.current = false
      recorder.start()
      recorderRef.current = recorder
      setRecordingSince(Date.now())
    } catch {
      setError('Microphone access was refused. Allow it in the browser to record.')
    }
  }

  const stopRecording = (discard: boolean) => {
    discardRecording.current = discard
    recorderRef.current?.stop()
  }

  const recordingFor = recordingSince === null ? 0 : Math.floor((Date.now() - recordingSince) / 1000)

  // Emoji go into the text at the caret. The caret is read from the field itself (it keeps
  // its selection while the panel has focus) and put back after the render, just behind the
  // new characters, so the member carries on typing where they were.
  const draftRef = useRef(draft)
  useLayoutEffect(() => {
    draftRef.current = draft
  })
  const caretRef = useRef<number | null>(null)
  const insertEmoji = (text: string) => {
    const field = inputRef.current
    const current = draftRef.current
    const chained = caretRef.current !== null // another insertion in the same tick
    const start = chained ? caretRef.current! : (field?.selectionStart ?? current.length)
    const end = chained ? start : (field?.selectionEnd ?? start)
    caretRef.current = start + text.length
    draftRef.current = current.slice(0, start) + text + current.slice(end)
    onDraftChange(draftRef.current)
  }
  useLayoutEffect(() => {
    if (caretRef.current === null) return
    inputRef.current?.setSelectionRange(caretRef.current, caretRef.current)
    caretRef.current = null
  }, [draft])

  const onDraftChange = (value: string) => {
    setDraft(value)
    const conversationId = activeIdRef.current
    const now = Date.now()
    if (conversationId && value.trim() && now - lastTypingSent.current > TYPING_SEND_EVERY_MS) {
      lastTypingSent.current = now
      realtime.emit({ action: 'typing', conversation_id: conversationId })
    }
  }

  const [lightbox, setLightbox] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)

  // --- starting a conversation -------------------------------------------------
  /** Open (or reuse) a thread with this member. */
  const startWith = async (userId: string) => {
    setError(null)
    setStartError(null)
    try {
      const created = await kaluta.messages.start([userId])
      setPeer('')
      setResults([])
      setSearched(false)
      setComposing(false)
      conversations.reload()
      void openThread(created.id)
    } catch (err) {
      // Usually their `who_can_message` setting refusing. The server's sentence
      // already says what to do next, so it is shown verbatim.
      setStartError(err instanceof ApiError ? err.message : 'Could not start the conversation')
    }
  }

  const answerInvitation = async (userId: string, accept: boolean) => {
    try {
      await kaluta.connections.respond(userId, accept)
      connections.reload()
    } catch (err) {
      setStartError(err instanceof ApiError ? err.message : 'Could not answer the invitation')
    }
  }

  // Debounced: a request per keystroke would be a request per keystroke, and
  // the last one to arrive is not necessarily the last one typed.
  useEffect(() => {
    const query = peer.trim()
    if (query.length < 2) {
      setResults([])
      setSearched(false)
      return
    }
    const timer = window.setTimeout(() => {
      void kaluta.people
        .search(query)
        .then((r) => {
          setResults(r.items)
          setSearched(true)
        })
        .catch(() => {
          setResults([])
          setSearched(true)
        })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [peer])

  // --- presence ----------------------------------------------------------------
  const friends = useMemo(() => connections.data?.accepted ?? [], [connections.data])
  const invitations = connections.data?.incoming ?? []

  /**
   * Connections you have never written to.
   *
   * They have no conversation, so they are in no list - and a connection you
   * cannot find is a connection you do not write to. They sit under the
   * conversations, where one tap starts one, rather than in a strip above
   * them that pushed the conversations off the screen.
   */
  const unwritten = useMemo(() => {
    const spoken = new Set<string>()
    for (const c of conversations.data?.items ?? []) {
      // Direct conversations only. Being in the welcome group together is not
      // having written to each other, and now that the welcome group holds
      // everybody, counting groups here would empty this list completely.
      if (c.kind === 'group') continue
      for (const p of c.participants) if (p !== user?.id) spoken.add(p)
    }
    return friends
      .filter((f) => !spoken.has(f.user_id))
      .sort((a, b) =>
        (a.profile?.display_name ?? '').localeCompare(b.profile?.display_name ?? ''),
      )
  }, [friends, conversations.data, user])

  const presenceIds = useMemo(() => {
    const ids = new Set<string>(friends.map((f) => f.user_id))
    // Groups are skipped on purpose: asking who is online among every member
    // of a room that holds the whole platform is a presence poll the size of
    // the platform, every few seconds, for a green dot nobody is looking at.
    for (const c of conversations.data?.items ?? []) {
      if (c.kind !== 'group') c.participants.forEach((p) => ids.add(p))
    }
    if (user) ids.delete(user.id)
    return [...ids].sort()
  }, [friends, conversations.data, user])
  const presenceKey = presenceIds.join(',')

  const refreshPresence = useCallback(() => {
    if (!presenceKey) return
    void kaluta.messages
      .presence(presenceKey.split(','))
      .then((r) => setPresence((current) => ({ ...current, ...r.items })))
      .catch(() => undefined)
  }, [presenceKey])

  useEffect(refreshPresence, [refreshPresence])

  // The friends strip, its "see all" panel and its local filter are gone:
  // the conversations are what this column is for. The list below still
  // steps aside while the member is searching for a person to write to.

  // --- reactions, edit, delete ---------------------------------------------------
  /** React, swap, or take it back - the server decides which from what is there. */
  const react = async (message: ChatMessage, emoji: string) => {
    const conversationId = activeIdRef.current
    if (!conversationId || message.status || message.id.startsWith('local_')) return
    try {
      const result = await kaluta.messages.react(conversationId, message.id, emoji)
      setMessages((current) =>
        current.map((m) =>
          m.id === message.id ? { ...m, reactions: result.counts, my_reaction: result.mine } : m,
        ),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not react — check your connection.')
    }
  }

  /** Save an edit. The thread updates from the server's answer, not from the draft. */
  const saveEdit = async () => {
    const conversationId = activeIdRef.current
    const target = editing
    const text = editDraft.trim()
    if (!conversationId || !target) return
    if (!text || text === (target.body ?? '')) {
      setEditing(null)
      return
    }
    setEditing(null)
    try {
      const saved = await kaluta.messages.edit(conversationId, target.id, text)
      setMessages((current) =>
        current.map((m) => (m.id === target.id ? { ...m, body: saved.body, edited_at: saved.edited_at } : m)),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save that edit.')
    }
  }

  /**
   * Delete a message. Confirmed first: there is no undo, by design - the words
   * are removed from the database rather than hidden.
   */
  const removeMessage = async (message: ChatMessage) => {
    const conversationId = activeIdRef.current
    if (!conversationId) return
    setConfirmDelete(null)
    try {
      await kaluta.messages.remove(conversationId, message.id)
      setMessages((current) =>
        current.map((m) =>
          m.id === message.id
            ? { ...m, deleted: true, body: null, media_kind: null, media_url: null, sticker: null, reactions: {}, my_reaction: null }
            : m,
        ),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not delete that message.')
    }
  }

  // --- live events ---------------------------------------------------------------
  const { connected } = useRealtime((event) => {
    switch (event.type) {
      case 'message': {
        if (event.conversation_id === activeIdRef.current && event.message_id) {
          setMessages((current) =>
            mergeMessages(current, [
              {
                id: event.message_id!,
                client_id: event.client_id ?? null,
                sender_id: event.sender_id ?? '',
                encrypted: Boolean(event.encrypted),
                ciphertext_b64: null,
                body: event.body ?? null,
                kind: event.kind ?? 'text',
                sticker_id: event.kind === 'sticker' ? (event.sticker_id ?? null) : null,
                sticker: event.kind === 'sticker' ? (event.sticker ?? null) : null,
                reply_to_id: event.reply_to_id ?? null,
                media_url: event.media_url ?? null,
                media_kind: event.media_kind ?? null,
                media_name: event.media_name ?? null,
                media_type: event.media_type ?? null,
                media_size: event.media_size ?? null,
                media_withheld: Boolean(event.media_withheld),
                created_at: event.created_at ?? new Date().toISOString(),
              },
            ]),
          )
          if (event.sender_id !== user?.id) markRead(event.conversation_id)
        }
        // A sound for a message somebody else sent. Not for your own, which you
        // just watched leave, and not for a frame echoing a message already on
        // screen - a reconnect replays what was missed, and a thread that chimes
        // six times on catch-up is a thread people mute.
        if (
          event.sender_id &&
          event.sender_id !== user?.id &&
          event.message_id &&
          !seenForSound.current.has(event.message_id)
        ) {
          seenForSound.current.add(event.message_id)
          playMessageChime()
        }
        // Whoever sent a message has stopped typing it.
        if (event.conversation_id && event.sender_id) {
          const { conversation_id: cid, sender_id: sid } = event
          setTyping((current) => {
            if (!current[cid]?.[sid]) return current
            const rest = { ...current[cid] }
            delete rest[sid]
            return { ...current, [cid]: rest }
          })
        }
        conversations.reload()
        break
      }
      case 'message_reaction': {
        if (!event.message_id) break
        const { message_id: reacted } = event
        setMessages((current) =>
          current.map((m) => (m.id === reacted ? { ...m, reactions: (event.counts as Record<string, number>) ?? {} } : m)),
        )
        break
      }
      case 'message_edited': {
        if (!event.message_id) break
        const { message_id: edited } = event
        setMessages((current) =>
          current.map((m) =>
            m.id === edited
              ? { ...m, body: (event.body as string) ?? m.body, edited_at: (event.edited_at as string) ?? new Date().toISOString() }
              : m,
          ),
        )
        break
      }
      case 'message_deleted': {
        if (!event.message_id) break
        const { message_id: removed } = event
        // The bubble stays and empties, rather than vanishing: a message
        // disappearing out of the middle of a thread somebody is reading
        // resequences the conversation under them.
        setMessages((current) =>
          current.map((m) =>
            m.id === removed
              ? { ...m, deleted: true, body: null, media_kind: null, media_url: null, sticker: null, reactions: {}, my_reaction: null }
              : m,
          ),
        )
        conversations.reload()
        break
      }
      case 'read': {
        if (!event.conversation_id || !event.user_id || !event.read_at) break
        const { conversation_id: cid, user_id: uid, read_at } = event
        setReads((current) => ({ ...current, [cid]: { ...current[cid], [uid]: read_at } }))
        // My own read, from this tab or another: the unread badge is stale.
        if (uid === user?.id) conversations.reload()
        break
      }
      case 'typing': {
        if (!event.conversation_id || !event.user_id) break
        const { conversation_id: cid, user_id: uid } = event
        const expires = Date.now() + TYPING_TTL_MS
        setTyping((current) => ({ ...current, [cid]: { ...current[cid], [uid]: expires } }))
        window.setTimeout(() => {
          setTyping((current) => {
            const at = current[cid]?.[uid]
            if (!at || at > Date.now()) return current
            const rest = { ...current[cid] }
            delete rest[uid]
            return { ...current, [cid]: rest }
          })
        }, TYPING_TTL_MS + 100)
        break
      }
      case 'presence': {
        if (!event.user_id) break
        const uid = event.user_id
        setPresence((current) => ({
          ...current,
          [uid]: { online: Boolean(event.online), last_seen: event.last_seen ?? null },
        }))
        break
      }
    }
  })

  // Catch up whenever the socket (re)connects. Frames sent while it was down —
  // a network drop, a laptop lid, the token expiring — are never replayed by
  // the server, so without this they would simply be missing until a reload.
  // The first connect counts too: a message can land between the thread's
  // initial fetch and the socket becoming ready.
  const reloadConversations = conversations.reload
  useEffect(() => {
    if (!connected) return
    reloadConversations()
    refreshPresence()
    const conversationId = activeIdRef.current
    if (!conversationId) return
    const last = [...messagesRef.current].reverse().find((m) => !m.status)
    void kaluta.messages
      .list(conversationId, { after: last?.id })
      .then((page) => {
        // The member may have switched threads while this was in flight.
        if (activeIdRef.current === conversationId) {
          setMessages((current) => mergeMessages(current, page.items))
        }
      })
      .catch(() => undefined)
    // refreshPresence is deliberately left out: a new friend must not trigger
    // a whole catch-up, only a presence fetch (handled by its own effect).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected, reloadConversations])

  // --- derived view state ---------------------------------------------------------
  const active = conversations.data?.items.find((c) => c.id === activeId)
  const activeOthers = active ? active.participants.filter((p) => p !== user?.id) : []

  const readAt = useCallback(
    (conversation: Conversation, uid: string): string | null =>
      reads[conversation.id]?.[uid] ?? conversation.read_state?.[uid] ?? null,
    [reads],
  )

  // The read mark is captured the first time each conversation becomes the
  // active one - once, and before `markRead` moves it forward a second later.
  // Reading it inside the loader instead looked simpler and was wrong: that
  // callback closes over whichever conversation was active when it was
  // created, which on a quick switch is the previous one.
  const capturedFor = useRef<string | null>(null)
  useEffect(() => {
    if (!active || !user || capturedFor.current === active.id) return
    capturedFor.current = active.id
    readMarkOnOpen.current = readAt(active, user.id)
  }, [active, user, readAt])

  /** A member of the open conversation, whether the thread or the list named them. */
  const personIn = (userId: string): PersonBrief | undefined =>
    senders[userId] ?? active?.profiles?.[userId]

  const typingNames = active
    ? Object.entries(typing[active.id] ?? {})
        .filter(([uid, until]) => uid !== user?.id && until > Date.now())
        .map(([uid]) => personIn(uid)?.display_name ?? 'Someone')
    : []

  // The receipt goes under my latest stored message only — one "Seen" per
  // thread, where the eye lands, not a tick-mark on every bubble.
  const nameOf = (message: ChatMessage) =>
    message.sender_id === user?.id ? 'You' : (personIn(message.sender_id)?.display_name ?? 'Member')

  /**
   * Put a message's words on the clipboard.
   *
   * The browser API first, and a hidden textarea when it is not there: the
   * clipboard API needs a secure context, so on anything served over plain
   * http - which includes most ways of looking at a staging build - Copy
   * would do nothing at all and say nothing about it.
   */
  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Falls through to the older way rather than giving up.
    }
    try {
      const scratch = document.createElement('textarea')
      scratch.value = text
      scratch.setAttribute('readonly', '')
      scratch.style.position = 'fixed'
      scratch.style.opacity = '0'
      document.body.appendChild(scratch)
      scratch.select()
      document.execCommand('copy')
      document.body.removeChild(scratch)
    } catch {
      setError('The message could not be copied.')
    }
  }

  const startReply = (message: ChatMessage) => {
    // A bubble still on its way has no id the server knows yet.
    if (message.status || message.id.startsWith('local_')) return
    setReplyTo(message)
    inputRef.current?.focus()
  }

  /** The original is built from what the thread already holds; the server never sends its text. */
  const quoteOf = (message: ChatMessage): QuoteTarget | null => {
    if (!message.reply_to_id) return null
    if (message.reply_to_deleted) return { state: 'deleted' }
    const original = messages.find((m) => m.id === message.reply_to_id)
    return original ? { state: 'found', name: nameOf(original), message: original } : { state: 'older' }
  }

  const jumpTo = (id: string) => {
    const el = document.getElementById(`msg-${id}`)
    if (!el) return
    el.scrollIntoView({ block: 'center' })
    setHighlightId(id)
    window.setTimeout(() => setHighlightId((current) => (current === id ? null : current)), 1500)
  }

  /**
   * The first message that arrived after the member last read this thread.
   *
   * Null when they were up to date, which is the common case and draws
   * nothing. Computed from the mark captured at opening: `markRead` moves the
   * real one to the end a second later, so reading it live would mean the
   * divider never appeared.
   */
  const firstUnreadId = (() => {
    const mark = readMarkOnOpen.current
    if (!mark) return null
    const since = new Date(mark).getTime()
    return (
      messages.find((m) => m.sender_id !== user?.id && new Date(m.created_at).getTime() > since)
        ?.id ?? null
    )
  })()

  const lastMine = [...messages].reverse().find((m) => m.sender_id === user?.id && !m.status)
  const seenBy =
    active && lastMine
      ? activeOthers.filter((uid) => {
          const at = readAt(active, uid)
          return at !== null && new Date(at).getTime() >= new Date(lastMine.created_at).getTime()
        })
      : []

  // The other person, when there is exactly one of them. A group has no
  // single profile behind its name, and a conversation whose profile never
  // resolved has no handle to go to.
  const headerProfile =
    active && active.kind === 'direct' && activeOthers.length === 1
      ? (active.profiles?.[activeOthers[0]] ?? null)
      : null

  const headerSubtitle = (() => {
    if (!active) return null
    if (typingNames.length) return `${typingNames.join(', ')} ${typingNames.length > 1 ? 'are' : 'is'} typing…`
    if (active.kind === 'direct' && activeOthers[0]) return lastSeenLabel(presence[activeOthers[0]], locale)
    // The real size, which is not the number of faces the list was sent: a
    // group's participant list is capped there, and this one says 1823.
    // No "N online" either - presence is not polled for a whole room, so the
    // honest answer is none and the honest thing is not to claim a number.
    const total = active.participant_count ?? activeOthers.length + 1
    return `${total} ${total === 1 ? 'member' : 'members'}`
  })()

  return (
    <AppShell rail={false} full>
      {/* One workspace, as Messenger draws it: the chats on the left, the open
          conversation on the right, in a single card. On a phone the open
          thread is the whole screen. */}
      {/* Whatever the shell's full mode left us, and no magic numbers: the
          chrome measures itself. Square corners and no gap, because the two
          panels are the screen here rather than cards sitting on a page. */}
      <div className="grid h-full min-h-0 min-w-0 grid-cols-[minmax(0,1fr)] gap-0 lg:grid-cols-[360px_minmax(0,1fr)]">
        <div className={cn('cloud-card flex min-h-0 min-w-0 flex-col overflow-hidden !rounded-none', showThread && 'hidden lg:flex')}>
          <div className="space-y-3 px-4 pb-2 pt-4">
            <div className="flex items-center gap-2">
              {/* The way out. This screen takes the whole window and hides the
                  chip row that every other page navigates by, so without this
                  the only exit is the browser's back button. */}
              <Link
                to="/hub"
                aria-label="Leave messages"
                title="Back to Kinjy"
                className="-ms-1 shrink-0 rounded-full p-1.5 text-text-mid hover:bg-text-hi/[0.07] hover:text-text-hi"
              >
                <ArrowLeft size={20} className="rtl:rotate-180" aria-hidden="true" />
              </Link>
              <h1 className="text-[26px] font-bold leading-none tracking-[-0.03em] text-text-hi">Chats</h1>
              {connected ? (
                <span title="Connected" className="inline-flex text-success">
                  <Wifi size={13} aria-hidden="true" />
                  <span className="sr-only">Connected</span>
                </span>
              ) : (
                <span
                  role="status"
                  className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-semibold text-amber-200"
                >
                  <WifiOff size={12} aria-hidden="true" />
                  Reconnecting…
                </span>
              )}
              <div className="ms-auto flex items-center gap-2">
                {/* One switch for both the arrival sound and browser
                    notifications. A sound nobody can switch off is a reason to
                    close the tab, and two controls for one intention was one
                    too many. */}
                <AlertsToggle />
                <button
                  type="button"
                  onClick={() => setComposing((v) => !v)}
                  aria-expanded={composing}
                  aria-label="New conversation"
                  title="New conversation"
                  className={cn(
                    'grid h-9 w-9 place-items-center rounded-full text-text-hi transition-colors hover:bg-text-hi/[0.1]',
                    composing ? 'bg-gold/20 text-gold-soft' : 'bg-text-hi/[0.07]',
                  )}
                >
                  <Plus size={17} aria-hidden="true" />
                </button>
                {/* A group is a different thing from a conversation with one
                    person: it has a name, a purpose and people you choose
                    before it exists, so it asks for them first rather than
                    starting empty. */}
                <button
                  type="button"
                  onClick={() => setCreatingGroup(true)}
                  aria-label="New group"
                  title="New group"
                  className="grid h-9 w-9 place-items-center rounded-full bg-text-hi/[0.07] text-text-hi transition-colors hover:bg-text-hi/[0.1]"
                >
                  <Users size={16} aria-hidden="true" />
                </button>
              </div>
            </div>

            <label className="relative flex items-center">
              <Search size={15} className="pointer-events-none absolute start-3.5 text-text-low" aria-hidden="true" />
              <input
                type="search"
                value={chatQuery}
                onChange={(e) => setChatQuery(e.target.value)}
                placeholder="Search chats"
                aria-label="Search your conversations"
                className="w-full rounded-full border border-transparent bg-text-hi/[0.07] py-2.5 pe-4 ps-10 text-[15px] text-text-hi placeholder:text-text-low focus:border-gold/50 focus:bg-transparent focus:outline-none"
              />
            </label>

            <div className="flex gap-2" role="group" aria-label="Filter conversations">
              {([['All', false], ['Unread', true]] as const).map(([label, value]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => setUnreadOnly(value)}
                  aria-pressed={unreadOnly === value}
                  className={cn(
                    'rounded-full px-3.5 py-1.5 text-[0.82rem] font-semibold transition-colors',
                    unreadOnly === value ? 'bg-gold/20 text-gold-soft' : 'text-text-mid hover:bg-text-hi/[0.07]',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-2 pb-3">
          {/* The conversations come first. A strip of every connection used
              to sit above them - fourteen faces to scroll past before the
              messages, on a page whose whole job is the messages. Starting a
              conversation is what the + above is for, and the search finds
              people by name.

              The invitations stay: a request to connect is the step before a
              conversation, and it was only living inside that strip by
              accident of layout. */}
          <section aria-label="Invitations to connect" className="px-2">
            {/* Answered here too: a friend request is the step before a
                conversation, so it belongs where conversations start. */}
            {invitations.length > 0 && (
              <div className="mt-2 space-y-1.5 rounded-card-sm border border-gold/25 bg-gold/5 p-2.5">
                <p className="caption">
                  {invitations.length} invitation{invitations.length > 1 ? 's' : ''} to connect
                </p>
                {invitations.map((invite) => (
                  <div key={invite.user_id} className="flex items-center gap-2">
                    <PresenceAvatar profile={invite.profile} size={28} />
                    <span className="min-w-0 flex-1 truncate text-sm text-text-hi">
                      {invite.profile?.display_name ?? 'Member'}
                    </span>
                    <button
                      type="button"
                      onClick={() => void answerInvitation(invite.user_id, true)}
                      aria-label={`Accept ${invite.profile?.display_name ?? 'invitation'}`}
                      className="rounded-full bg-gold/90 p-1.5 text-ink hover:bg-gold"
                    >
                      <Check size={13} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => void answerInvitation(invite.user_id, false)}
                      aria-label={`Decline ${invite.profile?.display_name ?? 'invitation'}`}
                      className="rounded-full border border-white/12 p-1.5 text-text-mid hover:text-text-hi"
                    >
                      <X size={13} aria-hidden="true" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <div className="space-y-3 px-2">
            {/* Starting a conversation with someone who is not yet a friend is
                an action, so it is a button — not a form permanently occupying
                the top of the list. */}
            {composing && (
              <div>
                <input
                  id="peer-search"
                  value={peer}
                  onChange={(e) => setPeer(e.target.value)}
                  placeholder="Search by name or @handle…"
                  aria-label="Search for someone to message"
                  autoFocus
                  className="w-full rounded-full border border-white/10 bg-ink-2/60 px-3 py-2 text-xs text-text-hi placeholder:text-text-low focus:border-gold/40 focus:outline-none"
                />

                {peer.trim().length > 0 && peer.trim().length < 2 && (
                  <p className="caption mt-1.5">Keep typing — two characters at least.</p>
                )}

                {results.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {results.map((person) => (
                      <li key={person.user_id}>
                        <button
                          type="button"
                          onClick={() => void startWith(person.user_id)}
                          className="flex w-full items-center gap-2.5 rounded-card-sm px-2 py-1.5 text-start hover:bg-white/5"
                        >
                          <PresenceAvatar profile={person} online={presence[person.user_id]?.online} size={28} />
                          <span className="min-w-0">
                            <span className="block truncate text-sm text-text-hi">{person.display_name}</span>
                            {/* The city, if we know it. Two people can share
                                a name and the handle told you nothing useful
                                about which was which either. */}
                            {person.city && (
                              <span className="caption block truncate">{person.city}</span>
                            )}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                {searched && peer.trim().length >= 2 && results.length === 0 && (
                  <p className="caption mt-2">
                    Nobody found. Someone who turned off discovery in their privacy
                    settings will not appear here.
                  </p>
                )}
              </div>
            )}

            {startError && (
              <p role="alert" className="rounded-card-sm border border-amber-300/30 bg-amber-300/10 px-2.5 py-2 text-xs text-amber-100">
                {startError}
              </p>
            )}

            {/* The conversations, unless the member is looking for a person.
                "See all" opens the whole list of connections and the search
                box finds people to write to; in both cases the answer being
                looked for is a person, and the conversations underneath were
                a second list to scroll past to reach it. */}
            {!searched && conversations.loading && !conversations.data && (
              <ConversationSkeleton count={4} />
            )}
            {!searched && conversations.data?.items.length === 0 && (
              <p className="text-sm text-text-low">
                No conversations yet. Tap a friend above, or use “New”.
              </p>
            )}

            {!searched && (() => {
              const needle = chatQuery.trim().toLowerCase()
              const shown = (conversations.data?.items ?? []).filter((conversation) => {
                if (unreadOnly && !((conversation.unread ?? 0) > 0)) return false
                if (!needle) return true
                const others = conversation.participants.filter((p) => p !== user?.id)
                return (
                  titleOf(conversation, others).toLowerCase().includes(needle) ||
                  (conversation.last_message?.preview ?? '').toLowerCase().includes(needle)
                )
              })
              if (conversations.data && conversations.data.items.length > 0 && shown.length === 0) {
                return (
                  <p className="px-2 py-3 text-sm text-text-low">
                    {unreadOnly && !needle ? 'Nothing unread.' : 'No conversation matches.'}
                  </p>
                )
              }
              return (
                <ul className="space-y-0.5">
                  {shown.map((conversation) => {
                    const others = conversation.participants.filter((p) => p !== user?.id)
                    const typingHere = Object.entries(typing[conversation.id] ?? {}).some(
                      ([uid, until]) => uid !== user?.id && until > Date.now(),
                    )
                    const unread = (conversation.unread ?? 0) > 0
                    return (
                      <li key={conversation.id}>
                        <button
                          type="button"
                          onClick={() => void openThread(conversation.id)}
                          aria-current={activeId === conversation.id ? 'true' : undefined}
                          className={cn(
                            'flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-start transition-colors',
                            activeId === conversation.id ? 'bg-gold/15' : 'hover:bg-text-hi/[0.05]',
                          )}
                        >
                          <PresenceAvatar
                            profile={avatarFor(conversation, others)}
                            online={conversation.kind === 'direct' && presence[others[0]]?.online}
                            group={conversation.kind === 'group'}
                            size={52}
                          />
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5 text-[15px] font-semibold leading-tight text-text-hi">
                              <span className="truncate">{titleOf(conversation, others)}</span>
                              {conversation.pinned && (
                                <Pin size={12} className="shrink-0 text-gold" aria-label="Pinned" />
                              )}
                            </span>
                            <span className="mt-0.5 flex items-center gap-1.5 text-[0.82rem] leading-tight">
                              <span
                                className={cn(
                                  'flex min-w-0 items-center gap-1',
                                  unread ? 'font-semibold text-text-hi' : 'text-text-low',
                                )}
                              >
                                {typingHere ? (
                                  <span className="text-gold-soft">typing…</span>
                                ) : conversation.last_message ? (
                                  <span className="truncate">
                                    {conversation.last_message.sender_id === user?.id && 'You: '}
                                    {conversation.last_message.preview}
                                  </span>
                                ) : (
                                  <>
                                    {conversation.encrypted || conversation.sealed_at_rest ? (
                                      <Lock size={11} aria-hidden="true" />
                                    ) : (
                                      <ShieldOff size={11} aria-hidden="true" />
                                    )}
                                    No messages yet
                                  </>
                                )}
                              </span>
                              <span
                                aria-hidden="true"
                                className="shrink-0 text-text-low"
                                title={fullStamp(conversation.last_message_at, locale)}
                              >
                                · {listStamp(conversation.last_message_at, locale)}
                              </span>
                            </span>
                          </span>
                          {unread && (
                            <span
                              className="ms-auto grid h-5 min-w-5 shrink-0 place-items-center rounded-full bg-gradient-to-br from-gold-soft to-gold px-1.5 text-[0.7rem] font-bold text-ink"
                              aria-label={`${conversation.unread} unread`}
                            >
                              {conversation.unread}
                            </span>
                          )}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )
            })()}

            {/* The connections you have not written to, after the ones you
                have. Same row, same tap: the only difference is that there is
                no conversation behind it yet. */}
            {!searched && !unreadOnly && unwritten.length > 0 && (
              <section aria-labelledby="unwritten-heading" className="pt-2">
                <h2 id="unwritten-heading" className="caption px-2 pb-1">
                  Say hello
                </h2>
                <ul className="space-y-0.5">
                  {unwritten.map((friend) => (
                    <li key={friend.user_id}>
                      <button
                        type="button"
                        onClick={() => void startWith(friend.user_id)}
                        className="flex w-full items-center gap-3 rounded-card-sm p-2.5 text-start hover:bg-text-hi/[0.05]"
                      >
                        <PresenceAvatar
                          profile={friend.profile}
                          online={presence[friend.user_id]?.online}
                          size={52}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[0.95rem] font-semibold text-text-hi">
                            {friend.profile?.display_name ?? 'Member'}
                          </span>
                          <span className="caption block truncate">
                            {presence[friend.user_id]
                              ? lastSeenLabel(presence[friend.user_id], locale)
                              : 'No messages yet'}
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </div>
          </div>
        </div>

        {/* The group's own screen, in place of the thread. On a phone there
            is no room for both, and on a desktop a third column would squeeze
            the messages - and you are looking at the group, not at them. */}
        {showGroup && active?.kind === 'group' && user && (
          <GroupPanel
            conversation={active}
            meId={user.id}
            onClose={() => setShowGroup(false)}
            onChanged={() => conversations.reload()}
          />
        )}

        {/* Thread */}
        <div
          className={cn(
            'cloud-card relative flex min-h-0 min-w-0 flex-col overflow-hidden !rounded-none',
            showGroup && active?.kind === 'group' && 'hidden',
            !showThread && 'hidden',
            dragging && 'ring-2 ring-inset ring-gold/50',
          )}
          onDragOver={(e) => {
            if (!active || !e.dataTransfer.types.includes('Files')) return
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false)
          }}
          onDrop={(e) => {
            if (!active) return
            e.preventDefault()
            setDragging(false)
            stage(e.dataTransfer.files)
          }}
        >
          {dragging && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-[inherit] bg-ink/70 text-sm font-semibold text-gold-soft">
              Drop to attach
            </div>
          )}
          {!active ? (
            <div className="m-auto max-w-xs px-6 text-center">
              <span className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-gold/15 text-gold-soft">
                <MessageSquare size={28} aria-hidden="true" />
              </span>
              <p className="mt-5 text-lg font-bold tracking-[-0.02em] text-text-hi">
                {loadingThread ? 'Loading…' : 'Your messages'}
              </p>
              {!loadingThread && (
                <p className="mt-1.5 text-sm leading-relaxed text-text-low">Pick a conversation or a friend.</p>
              )}
            </div>
          ) : (
            <>
              <header className="flex items-center gap-3 border-b border-[var(--cloud-border)] px-3 py-3 sm:px-5">
                <button
                  type="button"
                  onClick={backToList}
                  aria-label="Back to conversations"
                  className="-ms-1 shrink-0 rounded-full p-1.5 text-text-mid hover:text-text-hi lg:hidden"
                >
                  <ArrowLeft size={18} className="rtl:rotate-180" aria-hidden="true" />
                </button>
                {/* The person's face and name lead to the person. In a one
                    to one conversation that is the obvious thing to tap and
                    it did nothing; in a group there is no single profile to
                    go to, so there the header stays what it was. The status
                    line below is not part of the link - it is what they are
                    doing, not who they are. */}
                {headerProfile ? (
                  <Link
                    to={`/u/${headerProfile.handle}`}
                    aria-label={`View ${headerProfile.display_name}'s profile`}
                    className="shrink-0 rounded-full"
                  >
                    <PresenceAvatar
                      profile={headerProfile}
                      online={presence[activeOthers[0]]?.online}
                      size={44}
                    />
                  </Link>
                ) : (
                  <PresenceAvatar
                    profile={avatarFor(active, activeOthers)}
                    online={active.kind === 'direct' && presence[activeOthers[0]]?.online}
                    group={active.kind === 'group'}
                    size={44}
                  />
                )}
                <div className="min-w-0">
                  {headerProfile ? (
                    <Link
                      to={`/u/${headerProfile.handle}`}
                      className="block truncate text-base font-bold leading-tight text-text-hi hover:text-gold-soft hover:underline"
                    >
                      {titleOf(active, activeOthers)}
                    </Link>
                  ) : (
                    active.kind === 'group' ? (
                      // A group's name opens the group, the way a person's
                      // name opens the person.
                      <button
                        type="button"
                        onClick={() => setShowGroup(true)}
                        className="block max-w-full truncate text-start text-base font-bold leading-tight text-text-hi hover:text-gold-soft hover:underline"
                      >
                        {titleOf(active, activeOthers)}
                      </button>
                    ) : (
                      <p className="truncate text-base font-bold leading-tight text-text-hi">{titleOf(active, activeOthers)}</p>
                    )
                  )}
                  <p
                    className={cn('truncate text-xs text-text-low', typingNames.length > 0 && 'text-gold-soft')}
                    aria-live="polite"
                  >
                    {headerSubtitle}
                  </p>
                </div>
                {/* Three different promises, and the label says exactly which
                    one holds: "encrypted" alone would let storage encryption
                    pass for end-to-end, which it is not. */}
                <span
                  aria-label={
                    active.encrypted
                      ? 'End-to-end encrypted'
                      : active.sealed_at_rest
                        ? 'Encrypted on our servers'
                        : 'Not encrypted'
                  }
                  className="caption ms-auto hidden shrink-0 items-center gap-1 sm:inline-flex"
                  title={
                    active.encrypted
                      ? 'End-to-end encrypted — only the people in this conversation can read it.'
                      : active.sealed_at_rest
                        ? 'Messages and files are stored encrypted on Kinjy’s servers, so a copy of the database reveals nothing. This is not end-to-end encryption: Kinjy’s servers can read them.'
                        : 'Stored without encryption on Kinjy’s servers.'
                  }
                >
                  {active.encrypted || active.sealed_at_rest ? (
                    <Lock size={11} aria-hidden="true" />
                  ) : (
                    <ShieldOff size={11} aria-hidden="true" />
                  )}
                  {/* The padlock alone. The sentence beside it was the
                      longest thing in the header, repeated above every
                      conversation, and it says the same thing every time -
                      the title still carries the exact wording for anyone who
                      wants to know which of the three promises holds. */}
                </span>
              </header>

              {/* justify-start with a top margin that collapses, not
                  justify-end: a flex column that justifies to the end makes
                  the overflowing top unreachable in every browser that has
                  ever shipped, so a long thread could not be scrolled back.
                  mt-auto on the first child does the same job and resolves to
                  zero the moment the content is taller than the box. */}
              <div ref={listRef} onScroll={onScroll} className="flex flex-1 flex-col overflow-y-auto px-3 py-3 sm:px-5">
                {/* Pushes a short conversation down to sit on the composer,
                    the way a thread does everywhere else. Without it four
                    messages hung at the top of the screen with a void
                    underneath them. */}
                <div className="mt-auto" aria-hidden="true" />
                {hasOlder && (
                  <div className="mb-2 text-center">
                    <button
                      type="button"
                      onClick={() => void loadOlder()}
                      disabled={loadingOlder}
                      className="rounded-full border border-white/12 px-3 py-1 text-xs text-text-mid hover:text-text-hi disabled:opacity-50"
                    >
                      {loadingOlder ? 'Loading…' : 'Load older messages'}
                    </button>
                  </div>
                )}
                {loadingThread && <ThreadSkeleton count={6} />}
                {!loadingThread && messages.length === 0 && (
                  <p className="text-sm text-text-low">No messages yet. Say hello.</p>
                )}
                {messages.map((message, index) => {
                  // The room talking about itself: somebody joined, left, was
                  // made an admin, the name changed. Nobody said it, so it
                  // gets no bubble, no avatar and no sender - a line across
                  // the thread, in order with everything else, which is why
                  // it is stored as a message rather than drawn as a banner.
                  if (message.kind === 'system') {
                    return (
                      <p
                        key={message.client_id ?? message.id}
                        className="my-3 text-center text-[0.72rem] text-text-low"
                      >
                        {message.body}
                      </p>
                    )
                  }
                  const mine = message.sender_id === user?.id
                  const sender = personIn(message.sender_id)
                  const previous = messages[index - 1]
                  // A new day gets a separator; within a day, messages from the
                  // same person a few minutes apart sit together as one burst.
                  const newDay =
                    !previous ||
                    startOfDay(new Date(previous.created_at)) !== startOfDay(new Date(message.created_at))
                  const burst =
                    !newDay &&
                    previous.sender_id === message.sender_id &&
                    new Date(message.created_at).getTime() - new Date(previous.created_at).getTime() < BURST_MS
                  const hasText = !message.encrypted && Boolean(message.body?.trim())
                  const mediaOnly = Boolean(message.media_kind) && !hasText
                  // A sticker stands on its own, without a bubble, unless it answers a message
                  // (then the quote needs one to sit in).
                  const isSticker = !message.deleted && message.kind === 'sticker' && Boolean(message.sticker_id)
                  const sticker = isSticker ? (message.sticker ?? stickerById(message.sticker_id)) : null
                  const bareSticker = Boolean(sticker) && !message.reply_to_id
                  // Is the next bubble part of the same run? Decides the corners and
                  // where the sender's avatar and the time are drawn.
                  const following = messages[index + 1]
                  const burstNext =
                    Boolean(following) &&
                    startOfDay(new Date(following.created_at)) === startOfDay(new Date(message.created_at)) &&
                    following.sender_id === message.sender_id &&
                    new Date(following.created_at).getTime() - new Date(message.created_at).getTime() < BURST_MS
                  const isAudio = message.media_kind === 'audio'
                  // A run shows one time, on its last bubble; hover has the full stamp.
                  // An edited message always shows its time, which is where "edited" is said:
                  // a change nobody can see is a way to alter what somebody agreed to.
                  const showMeta = !burstNext || Boolean(message.status) || Boolean(message.edited_at)
                  // Where the time goes: inside a voice note, at the end of the text's last
                  // line, or (a photo, a sticker) on a row of its own. Never a row after text.
                  const metaInPlayer = isAudio && !hasText && !message.encrypted
                  const metaInText = hasText || (message.encrypted && !isSticker)
                  const linkInBody = hasText ? firstLink(message.body ?? '') : null
                  const metaOnItsOwnRow = !metaInPlayer && !metaInText
                  const seenByThem =
                    mine && activeOthers.some((uid) => {
                      const at = readAt(active, uid)
                      return at !== null && new Date(at).getTime() >= new Date(message.created_at).getTime()
                    })
                  const meta = (
                    <MessageMeta
                      message={message}
                      locale={locale}
                      mine={mine}
                      onOwnBubble={mine && !bareSticker}
                      isRead={seenByThem}
                    />
                  )
                  const attachmentSender = mine
                    ? user && { handle: user.handle, display_name: user.display_name, avatar_url: me?.avatar_url ?? sender?.avatar_url ?? null }
                    : sender
                  return (
                    <div key={message.client_id ?? message.id}>
                      {/* Where you stopped last time. The thread opens at the
                          newest message, so this is not a place you are taken
                          to - it is a mark you meet on the way back up, which
                          is what somebody looking for "what did I miss"
                          actually needs. Drawn from the read mark as it was
                          when the conversation opened, before marking it read
                          moved it to the end. */}
                      {firstUnreadId === message.id && (
                        <div className="my-4 flex items-center gap-3" role="separator">
                          <span className="h-px flex-1 bg-gold/40" />
                          <span className="rounded-full bg-gold/15 px-3 py-1 text-xs font-semibold text-gold-soft">
                            Unread messages
                          </span>
                          <span className="h-px flex-1 bg-gold/40" />
                        </div>
                      )}
                      {newDay && (
                        <div className="my-4 flex items-center gap-3" role="separator">
                          <span className="h-px flex-1 bg-[var(--cloud-border)]" />
                          <span className="rounded-full bg-text-hi/[0.06] px-3 py-1 text-xs font-semibold text-text-low">
                            {dayLabel(message.created_at, locale)}
                          </span>
                          <span className="h-px flex-1 bg-[var(--cloud-border)]" />
                        </div>
                      )}
                      <div
                        id={`msg-${message.id}`}
                        /*
                         * Right-click opens the message's own menu instead of
                         * the browser's, which knows nothing about messages.
                         *
                         * On the whole row, not on the bubble: the bubble is
                         * only as wide as its words, so on a two-word message
                         * it was a target barely larger than the text, and a
                         * click a few pixels off did nothing at all. Every
                         * desktop messenger answers anywhere on the line.
                         */
                        onContextMenu={(event) => {
                          if (message.deleted || message.status || message.id.startsWith('local_')) return
                          // Only when the selection is in *this* message: the
                          // native menu is where Copy lives for selected text.
                          // It used to check for a selection anywhere on the
                          // page, so one left over from earlier - in the
                          // search box, in another message - silently killed
                          // the right-click everywhere until it was cleared.
                          const selection = window.getSelection()
                          if (
                            selection &&
                            !selection.isCollapsed &&
                            event.currentTarget.contains(selection.anchorNode)
                          ) {
                            return
                          }
                          event.preventDefault()
                          setMenuFor(message.id)
                        }}
                        className={cn(
                          'flex flex-col rounded-card-md',
                          mine ? 'items-end' : 'items-start',
                          burst ? 'mt-0.5' : 'mt-3',
                          highlightId === message.id && 'bg-gold/20',
                        )}
                      >
                        {!mine && active.kind === 'group' && !burst && (
                          <span className="mb-0.5 ms-9 text-xs text-text-low">{sender?.display_name ?? 'Member'}</span>
                        )}
                        <div className={cn('flex w-full items-end gap-2', mine ? 'justify-end' : 'justify-start')}>
                        {!mine && (
                          <span className="w-7 shrink-0" aria-hidden={burstNext ? 'true' : undefined}>
                            {!burstNext && <PresenceAvatar profile={sender} size={28} />}
                          </span>
                        )}
                        {/* Still the anchor the menu is positioned over, even
                            though the right-click is handled on the row. */}
                        <div
                          id={`bubble-${message.id}`}
                          className="group/bubble relative flex min-w-0 max-w-[min(75%,34rem)] items-stretch"
                        >
                        {/* Beside the bubble, out of the flow: they must not widen it or push the reactions off its edge. */}
                        <div className={cn('absolute inset-y-0 flex items-center', mine ? 'end-full' : 'start-full')}>
                          {!message.deleted && (
                            <ReplyButton
                              onClick={() => startReply(message)}
                              label={`Reply to ${nameOf(message)}`}
                            />
                          )}
                          {!message.deleted && !message.status && !message.id.startsWith('local_') && (
                            <MessageMenuButton
                              label={`Options for the message from ${nameOf(message)}`}
                              onOpen={() => setMenuFor(message.id)}
                            />
                          )}
                        </div>
                        <SwipeToReply
                          onReply={() => startReply(message)}
                          onLongPress={() => setMenuFor(message.id)}
                          disabled={Boolean(message.status) || Boolean(message.deleted)}
                        >
                        <div
                          className={cn(
                            'min-w-0',
                            bareSticker ? 'p-0' : metaInPlayer ? 'px-2.5 py-1.5' : mediaOnly ? 'p-1.5' : 'px-3.5 py-[7px]',
                            !bareSticker && bubbleShape(mine, !burst, !burstNext),
                            bareSticker
                              ? 'text-text-hi'
                              : mine
                                // The platform's own gold, not Messenger's
                                // blue. Borrowing another product's signature
                                // colour made this one screen look like it
                                // came from somewhere else.
                                ? 'bg-gradient-to-br from-gold-soft to-gold text-ink'
                                : 'bg-text-hi/[0.07] text-text-hi',
                            message.status === 'sending' && 'opacity-80',
                            message.status === 'failed' && 'border border-red-400/40',
                          )}
                        >
                          {!message.deleted && quoteOf(message) && (
                            <ReplyQuote
                              target={quoteOf(message)!}
                              mine={mine}
                              onJump={() => message.reply_to_id && jumpTo(message.reply_to_id)}
                            />
                          )}
                          {message.deleted && (
                            <p className={cn('text-[15px] italic leading-snug', mine ? 'text-white/70' : 'text-text-low')}>
                              This message was deleted
                            </p>
                          )}
                          {sticker && (
                            <span role="img" aria-label={sticker.label} title={sticker.label} className="block">
                              <StickerFace
                                sticker={sticker}
                                className="block text-[3.75rem]"
                                imageClassName="h-28 w-28 max-w-full object-contain"
                              />
                            </span>
                          )}
                          {isSticker && !sticker && (
                            <p className={cn('text-[15px] italic leading-snug', mine ? 'text-white/70' : 'text-text-low')}>
                              Sticker no longer available
                            </p>
                          )}
                          {message.media_kind && (
                            <div className={cn('relative', hasText && '-mx-2 -mt-0.5 mb-1.5')}>
                              <Attachment
                                message={message}
                                onOpenImage={setLightbox}
                                sender={attachmentSender}
                                mine={mine}
                                metaSlot={showMeta ? meta : undefined}
                              />
                              {message.status === 'sending' && !metaInPlayer && (
                                <div className="absolute inset-x-2 bottom-2 h-1 overflow-hidden rounded-full bg-black/40">
                                  <div
                                    className="h-full bg-gold"
                                    style={{ width: `${Math.round((message.progress ?? 0) * 100)}%` }}
                                  />
                                </div>
                              )}
                            </div>
                          )}
                          {metaInText && (
                            <p className="relative whitespace-pre-wrap break-words text-[15px] leading-snug">
                              {message.encrypted ? (
                                <span className="italic text-text-low">
                                  Encrypted — only your device can read this.
                                </span>
                              ) : (
                                formatChatText(message.body ?? '', message.id)
                              )}
                              {showMeta && (
                                <>
                                  {/* An unseen copy of the time keeps room for it at the end of the last
                                      line, or pushes it to a new line when that line is full; the real one
                                      sits on top of it, bottom right. */}
                                  <span aria-hidden="true" className="invisible ms-2.5 inline-flex align-bottom">
                                    {meta}
                                  </span>
                                  <span className="absolute bottom-0 end-0 flex items-end">{meta}</span>
                                </>
                              )}
                            </p>
                          )}
                          {metaOnItsOwnRow && showMeta && (
                            <div className={cn('flex justify-end', mediaOnly || bareSticker ? 'px-1.5 pt-1' : 'mt-1')}>
                              {meta}
                            </div>
                          )}
                          {/* The card for an address somebody sent. Only for
                              the first one - a message with four links is a
                              list, not four cards - and never for an
                              encrypted body, where there is no text to read a
                              link out of in the first place. */}
                          {hasText && !message.deleted && linkInBody && (
                            <div className="mt-1.5 max-w-[320px]">
                              <LinkPreview url={linkInBody} />
                            </div>
                          )}
                        </div>
                        </SwipeToReply>
                        </div>
                        </div>
                        {!message.deleted && (
                          <ReactionChips
                            counts={message.reactions ?? {}}
                            mine={message.my_reaction}
                            ownMessage={mine}
                            onToggle={(emoji) => void react(message, emoji)}
                          />
                        )}
                        {message.status === 'failed' && (
                          <button
                            type="button"
                            onClick={() => retry(message)}
                            className="mt-1 inline-flex items-center gap-1 text-xs text-red-200 hover:underline"
                          >
                            <AlertCircle size={12} aria-hidden="true" />
                            Not sent — tap to retry
                          </button>
                        )}
                        {lastMine && message.id === lastMine.id && (
                          <span className="caption mt-0.5 inline-flex items-center gap-1">
                            {seenBy.length ? (
                              <>
                                <CheckCheck size={12} className="text-gold-soft" aria-hidden="true" />
                                {active.kind === 'direct' ? 'Seen' : `Seen by ${seenBy.length}`}
                              </>
                            ) : (
                              <>
                                <Check size={12} aria-hidden="true" />
                                Sent
                              </>
                            )}
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
                {typingNames.length > 0 && (
                  <div className="mt-3 flex" aria-hidden="true">
                    <span className="inline-flex gap-1 rounded-[18px] bg-text-hi/[0.07] px-3.5 py-3">
                      {[0, 1, 2].map((i) => (
                        <span
                          key={i}
                          className="h-1.5 w-1.5 animate-bounce rounded-full bg-text-mid"
                          style={{ animationDelay: `${i * 150}ms` }}
                        />
                      ))}
                    </span>
                  </div>
                )}
              </div>

              {/* Files waiting to go: reviewed, captioned, removable. */}
              {staged.length > 0 && (
                <ul className="flex flex-wrap gap-2 px-3 pt-2 sm:px-5" aria-label="Attachments to send">
                  {staged.map((file, index) => (
                    <li
                      key={`${file.name}-${index}`}
                      className="flex max-w-[220px] items-center gap-2 rounded-card-sm border border-white/10 bg-ink-2/60 py-1.5 pe-1.5 ps-2.5"
                    >
                      <FileIcon size={14} className="shrink-0 text-gold-soft" aria-hidden="true" />
                      <span className="min-w-0">
                        <span className="block truncate text-xs text-text-hi">{file.name}</span>
                        <span className="caption block">{sizeLabel(file.size)}</span>
                      </span>
                      <button
                        type="button"
                        onClick={() => setStaged((current) => current.filter((_, i) => i !== index))}
                        aria-label={`Remove ${file.name}`}
                        className="rounded-full p-1 text-text-mid hover:text-text-hi"
                      >
                        <X size={12} aria-hidden="true" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {/* The end padding keeps Send clear of the assistant orb, which
                  rests in the bottom-end corner (lib/floating.ts, slot 0) —
                  exactly where a full-height thread puts its composer. No
                  orb, no gap: Send keeps the full width. */}
              {/* A request the viewer has not answered. In a group this is a
                  minor who was added by somebody else: they read the text, and
                  attachments wait for this button. */}
              {active?.accepted === false && (
                <div className="mx-3 mb-1 flex flex-wrap items-center gap-2 rounded-card-sm border border-gold/35 bg-gold/[0.07] px-3 py-2 sm:mx-5">
                  <p className="min-w-0 flex-1 text-xs text-text-mid">
                    {active.kind === 'group'
                      ? 'You were added to this group. Photos and files from others stay hidden until you accept it.'
                      : 'This is a message request. Accept it to receive photos and files.'}
                  </p>
                  <button
                    type="button"
                    onClick={() =>
                      void kaluta.messages.accept(active.id).then(() => {
                        conversations.reload()
                        void openThread(active.id, 'url')
                      })
                    }
                    className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink"
                  >
                    Accept
                  </button>
                </div>
              )}
              {/* Editing happens where the message is read, not in a dialog over it:
                  the point of an edit is the words around it. */}
              {editing && (
                <div className="mx-3 mb-1 rounded-card-sm border border-gold/35 bg-gold/[0.07] px-3 py-2 sm:mx-5">
                  <p className="mb-1.5 inline-flex items-center gap-1 text-xs font-semibold text-text-mid">
                    <Pencil size={11} aria-hidden="true" />
                    Editing a message
                  </p>
                  <MessageInput
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    onSubmit={() => void saveEdit()}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') setEditing(null)
                    }}
                    autoFocus
                    aria-label="Edit message"
                    className="w-full rounded-xl border border-transparent bg-text-hi/[0.07] px-4 py-2.5 text-[0.95rem] text-text-hi focus:border-gold/50 focus:outline-none"
                  />
                  <div className="mt-2 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void saveEdit()}
                      className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-3 py-1 text-xs font-bold text-ink"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(null)}
                      className="rounded-full border border-[var(--cloud-border)] px-3 py-1 text-xs font-semibold text-text-mid hover:text-text-hi"
                    >
                      Cancel
                    </button>
                    <span className="text-xs text-text-low">Everyone will see it was edited.</span>
                  </div>
                </div>
              )}
              {replyTo && (
                <ReplyBanner name={nameOf(replyTo)} message={replyTo} onCancel={() => setReplyTo(null)} />
              )}
              <form onSubmit={send} className={cn('flex items-center gap-1.5 px-3 py-3 sm:px-5', FEATURES.assistant && 'pe-14 lg:pe-12')}>
                {recordingSince !== null ? (
                  <>
                    <button
                      type="button"
                      onClick={() => stopRecording(true)}
                      aria-label="Discard the recording"
                      className="shrink-0 rounded-full border border-white/12 p-2.5 text-text-mid hover:text-red-200"
                    >
                      <Trash2 size={15} aria-hidden="true" />
                    </button>
                    <span className="flex flex-1 items-center gap-2 rounded-full border border-red-400/30 bg-red-400/10 px-4 py-2.5 text-sm text-red-100" role="status">
                      <span className="h-2 w-2 animate-pulse rounded-full bg-red-400" aria-hidden="true" />
                      Recording {Math.floor(recordingFor / 60)}:{String(recordingFor % 60).padStart(2, '0')}
                    </span>
                    <button
                      type="button"
                      onClick={() => stopRecording(false)}
                      aria-label="Stop and send the voice message"
                      className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-gradient-to-br from-gold-soft to-gold text-ink"
                    >
                      <Square size={15} aria-hidden="true" />
                    </button>
                  </>
                ) : (
                  <>
                    <AttachmentMenu onFiles={stage} />
                    <EmojiStickerButton packs={stickerPacks.data?.packs ?? null} onEmoji={insertEmoji} onSticker={sendSticker} />
                    {/* A textarea, not an input: a message can have more
                        than one line. Enter sends on a keyboard, Shift+Enter
                        breaks the line, and on a phone Enter breaks the line
                        because there is no Shift to hold beside it. The box
                        is rounded like a pill while it is one line and
                        squares off as it grows, so it never looks like a
                        stretched pill. */}
                    <MessageInput
                      ref={inputRef}
                      value={draft}
                      onChange={(e) => onDraftChange(e.target.value)}
                      onSubmit={() => void send()}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape' && replyTo) {
                          e.preventDefault()
                          setReplyTo(null)
                        }
                      }}
                      onPaste={(e) => {
                        // A pasted screenshot is an attachment, not text.
                        if (e.clipboardData.files.length) {
                          e.preventDefault()
                          stage(e.clipboardData.files)
                        }
                      }}
                      placeholder={staged.length ? 'Add a caption…' : 'Write a message…'}
                      aria-label="Message"
                      className={cn(
                        'w-full min-w-0 border border-transparent bg-text-hi/[0.07] px-4 py-2.5 text-[15px] text-text-hi placeholder:text-text-low focus:border-gold/50 focus:bg-transparent focus:outline-none',
                        draft.includes('\n') ? 'rounded-card-sm' : 'rounded-[20px]',
                      )}
                    />
                    {draft.trim() || staged.length ? (
                      <button
                        type="submit"
                        aria-label="Send"
                        className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-gradient-to-br from-gold-soft to-gold text-ink"
                      >
                        <Send size={15} aria-hidden="true" />
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void startRecording()}
                        aria-label="Record a voice message"
                        className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-text-mid hover:bg-text-hi/[0.07] hover:text-gold-soft"
                      >
                        <Mic size={15} aria-hidden="true" />
                      </button>
                    )}
                  </>
                )}
              </form>
            </>
          )}
          {error && (
            <p role="alert" className="px-3 pb-2 text-sm text-red-200 sm:px-5">
              {error}
            </p>
          )}
        </div>
      </div>
      {menuFor && (() => {
        const target = messages.find((m) => m.id === menuFor)
        if (!target || target.deleted) return null
        const own = target.sender_id === user?.id
        const text = target.encrypted ? '' : (target.body ?? '')
        return (
          <MessageMenu
            anchor={`bubble-${menuFor}`}
            current={target.my_reaction}
            // Text the server can read: not a sticker, not an end-to-end
            // encrypted message. The server refuses both anyway; the point is
            // not to offer what will fail.
            canEdit={own && !target.encrypted && target.kind !== 'sticker' && Boolean(text.trim())}
            canDelete={own}
            onPick={(emoji) => void react(target, emoji)}
            onReply={() => startReply(target)}
            onCopy={text.trim() ? () => void copyText(text) : undefined}
            onEdit={() => {
              setEditing(target)
              setEditDraft(text)
            }}
            onDelete={() => setConfirmDelete(target)}
            onClose={() => setMenuFor(null)}
          />
        )
      })()}
      {lightbox && (
        <MediaLightbox
          media={[{ url: lightbox, kind: 'image', alt_text: null }]}
          index={0}
          onClose={() => setLightbox(null)}
        />
      )}
      <ConfirmDialog
        open={Boolean(confirmDelete)}
        onOpenChange={(open) => !open && setConfirmDelete(null)}
        title="Delete this message?"
        description="The text is removed for everyone in this conversation, not hidden. It cannot be recovered."
        confirmLabel="Delete message"
        onConfirm={() => confirmDelete && void removeMessage(confirmDelete)}
      />
      {creatingGroup && (
        <NewGroupDialog
          onClose={() => setCreatingGroup(false)}
          onCreated={(id) => {
            setCreatingGroup(false)
            conversations.reload()
            setParams({ c: id })
          }}
        />
      )}
    </AppShell>
  )
}
