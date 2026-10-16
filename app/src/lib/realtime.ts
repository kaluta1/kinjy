import { refreshAccessToken, tokens } from '@/lib/api'

export interface RealtimeEvent {
  type: string
  topic?: string
  /** chat */
  conversation_id?: string
  message_id?: string
  /** chat: the message a new message answers (id only) */
  reply_to_id?: string | null
  /** chat: the catalogue id of a sticker message */
  sticker_id?: string | null
  sender_id?: string
  encrypted?: boolean
  body?: string | null
  media_url?: string | null
  media_kind?: string | null
  media_name?: string | null
  media_type?: string | null
  media_size?: number | null
  /** The attachment is held back from this reader until they accept the group. */
  media_withheld?: boolean
  /** Set on a message_edited frame: when the text was changed. */
  edited_at?: string | null
  /** Set on a new message that is a sticker, so the bubble can draw it at once. */
  sticker?: { id: string; glyph: string; label: string; image_url: string | null } | null
  created_at?: string
  /** engagement */
  post_id?: string
  likes_count?: number
  comments_count?: number
  reposts_count?: number
  views_count?: number
  counts?: Record<string, number>
  total?: number
  actor?: string
  author_id?: string
  comment_id?: string
  parent_id?: string | null
  reply_to?: string | null
  depth?: number
  format?: string
  mature?: boolean
  kind?: string
  /** chat: pending-bubble reconciliation, read receipts, presence */
  client_id?: string | null
  user_id?: string
  read_at?: string
  online?: boolean
  last_seen?: string | null
  /** notifications */
  id?: number | string
  title?: string
  link?: string | null
  unread?: number
  /** forums */
  thread_id?: string
  reply_id?: string
  replies_count?: number
}

type Listener = (event: RealtimeEvent) => void

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

/** Close code the server uses for "your access token is missing or expired". */
const UNAUTHORIZED = 4401

function socketUrl(): string {
  // VITE_API_BASE may be absolute (http://localhost:8200/api) or relative
  // (/api behind the Vite proxy); both have to become a ws:// origin.
  // No token here: it goes in the first frame, so no access log records it.
  const httpUrl = BASE.startsWith('http') ? `${BASE}/ws` : `${window.location.origin}${BASE}/ws`
  return httpUrl.replace(/^http/, 'ws')
}

/**
 * One socket for the whole tab.
 *
 * Every live surface multiplexes over this single connection rather than
 * opening its own: a feed of twenty cards each wanting its own updates would
 * otherwise be twenty WebSockets, twenty handshakes and twenty keep-alives, and
 * browsers cap concurrent sockets per origin — the last cards would simply
 * never connect.
 *
 * Topics are reference-counted, so a card unmounting stops its subscription
 * only when no other card wants it, and the full set is re-sent on every
 * reconnect — a socket that comes back without resubscribing looks connected
 * and delivers nothing, which is the worst of both.
 */
class Realtime {
  private socket: WebSocket | null = null
  private retry = 0
  private retryTimer?: number
  private keepAlive?: number
  private wanted = false

  /** topic -> listeners. The empty string is the "everything" channel. */
  private listeners = new Map<string, Set<Listener>>()
  private statusListeners = new Set<(connected: boolean) => void>()
  private connected = false
  /** Topics asked for since the last frame — sent together, see queueSubscribe. */
  private pending = new Set<string>()
  private flushQueued = false

  private topics(): string[] {
    return [...this.listeners.keys()].filter(Boolean)
  }

  private setConnected(value: boolean) {
    if (this.connected === value) return
    this.connected = value
    this.statusListeners.forEach((fn) => fn(value))
  }

  private send(frame: unknown) {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(frame))
  }

  /** Consecutive 4401 closes; a refresh that keeps failing must not spin. */
  private authFailures = 0

  /**
   * One frame for every topic asked for in the same tick.
   *
   * A feed mounts twenty cards at once and each wants its post's topic. The
   * server checks every post topic against the member's audience before
   * granting it (a circle post's topic is not for strangers), so twenty frames
   * meant twenty checks; one frame is one check.
   */
  private queueSubscribe(topic: string) {
    this.pending.add(topic)
    if (this.flushQueued) return
    this.flushQueued = true
    queueMicrotask(() => {
      this.flushQueued = false
      const topics = [...this.pending].filter((t) => this.listeners.has(t))
      this.pending.clear()
      if (topics.length) this.send({ action: 'subscribe', topics })
    })
  }

  private connect() {
    const token = tokens.access
    if (!token || this.socket || !this.wanted) return

    const socket = new WebSocket(socketUrl())
    this.socket = socket

    // The token is the first frame. The socket only counts as connected once
    // the server has accepted it and answered `ready` — an open socket that
    // was about to be refused is not a live one.
    socket.onopen = () => socket.send(JSON.stringify({ action: 'auth', token }))

    socket.onmessage = (event) => {
      let payload: RealtimeEvent
      try {
        payload = JSON.parse(event.data)
      } catch {
        return // keep-alive echoes and other non-JSON frames
      }
      if (payload.type === 'ready') {
        this.retry = 0
        this.authFailures = 0
        const topics = this.topics()
        if (topics.length) this.send({ action: 'subscribe', topics })
        // A periodic ping keeps intermediaries from culling an idle socket.
        this.keepAlive = window.setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) socket.send('ping')
        }, 25000)
        this.setConnected(true)
        return
      }
      this.listeners.get(payload.topic ?? '')?.forEach((fn) => fn(payload))
      // The "everything" channel still sees topic-scoped frames; chat relies on
      // it and should not have to enumerate its own conversations.
      if (payload.topic) this.listeners.get('')?.forEach((fn) => fn(payload))
    }

    socket.onclose = (event) => {
      this.socket = null
      this.setConnected(false)
      window.clearInterval(this.keepAlive)
      if (!this.wanted) return

      // The access token expired (the server closes at expiry) or was never
      // valid. Retrying with the same token would be refused forever, so get
      // a fresh one first and come straight back — the gap is then a
      // reconnect, which listeners catch up on, not an outage.
      if (event.code === UNAUTHORIZED && this.authFailures < 2) {
        this.authFailures += 1
        void refreshAccessToken().then((ok) => {
          // No refresh token, or it was revoked: the member is signed out and
          // there is nothing to reconnect as.
          if (ok) this.connect()
        })
        return
      }

      this.retry += 1
      this.retryTimer = window.setTimeout(
        () => this.connect(),
        Math.min(30000, 1000 * 2 ** this.retry),
      )
    }

    socket.onerror = () => socket.close()
  }

  private maybeClose() {
    if (this.listeners.size) return
    this.wanted = false
    window.clearTimeout(this.retryTimer)
    window.clearInterval(this.keepAlive)
    this.socket?.close()
    this.socket = null
  }

  /** Listen to one topic, or to everything when `topic` is empty. */
  subscribe(topic: string, listener: Listener): () => void {
    const fresh = !this.listeners.has(topic)
    const set = this.listeners.get(topic) ?? new Set<Listener>()
    set.add(listener)
    this.listeners.set(topic, set)

    this.wanted = true
    if (!this.socket) this.connect()
    else if (fresh && topic) this.queueSubscribe(topic)

    return () => {
      const current = this.listeners.get(topic)
      if (!current) return
      current.delete(listener)
      if (current.size) return
      this.listeners.delete(topic)
      // Only tell the server once nobody in this tab wants it any more.
      if (topic) this.send({ action: 'unsubscribe', topics: [topic] })
      this.maybeClose()
    }
  }

  /**
   * Send a frame to the server — ephemeral signals such as "typing" that are
   * not worth a request. Dropped silently while disconnected: a typing hint
   * that arrives late is worse than none.
   */
  emit(frame: Record<string, unknown>) {
    this.send(frame)
  }

  onStatus(listener: (connected: boolean) => void): () => void {
    this.statusListeners.add(listener)
    listener(this.connected)
    return () => {
      this.statusListeners.delete(listener)
    }
  }

  /** Drop the connection — used on sign-out, so the next member gets a fresh one. */
  reset() {
    this.listeners.clear()
    this.maybeClose()
  }
}

export const realtime = new Realtime()
