import { useEffect, useState } from 'react'
import { BarChart3, CalendarDays, CheckCheck, CheckSquare, Pin, Plus, Search, X } from 'lucide-react'
import { ApiError, kaluta, type MessageBrief, type PersonBrief } from '@/lib/api'
import { cn } from '@/lib/utils'
import { briefText } from './groupText'

/*
 * The thread's own tools for groups: the pinned bar, search, the replies of
 * one message, the "+" for polls, events and tasks, and "Seen by".
 */

function SidePanel({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-[85] flex items-end justify-center bg-black/55 sm:items-center sm:p-6" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="cloud-card flex max-h-[88svh] w-full max-w-lg flex-col overflow-hidden rounded-t-3xl sm:rounded-3xl">
        <header className="flex items-center gap-2 border-b border-[var(--cloud-border)] px-4 py-3">
          <h2 className="flex-1 text-sm font-bold text-text-hi">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-full p-1.5 text-text-mid hover:bg-text-hi/[0.07]">
            <X size={16} aria-hidden="true" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  )
}

/** The latest pinned message, across the top of the thread. */
export function PinnedBar({ conversationId, version, onJump }: { conversationId: string; version: number; onJump: (id: string) => void }) {
  const [pins, setPins] = useState<{ items: MessageBrief[]; profiles: Record<string, PersonBrief> } | null>(null)
  const [index, setIndex] = useState(0)
  useEffect(() => {
    let live = true
    kaluta.messages
      .pins(conversationId)
      .then((page) => {
        if (!live) return
        setPins(page)
        setIndex(0)
      })
      .catch(() => live && setPins(null))
    return () => {
      live = false
    }
  }, [conversationId, version])
  if (!pins || pins.items.length === 0) return null
  const shown = pins.items[index % pins.items.length]
  return (
    <button
      type="button"
      onClick={() => {
        onJump(shown.id)
        setIndex((i) => i + 1)
      }}
      className="flex w-full items-center gap-2 border-b border-[var(--cloud-border)] bg-gold/[0.06] px-3 py-2 text-start sm:px-5"
      aria-label={`Pinned message ${(index % pins.items.length) + 1} of ${pins.items.length}. Go to it.`}
    >
      <Pin size={13} className="shrink-0 text-gold-soft" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate text-xs text-text-mid">
        <span className="font-semibold text-text-hi">{pins.profiles[shown.sender_id]?.display_name ?? 'Member'}: </span>
        {briefText(shown)}
      </span>
      {pins.items.length > 1 && (
        <span className="shrink-0 text-[0.7rem] text-text-low">
          {(index % pins.items.length) + 1}/{pins.items.length}
        </span>
      )}
    </button>
  )
}

/** Search one conversation, under the same rules as reading it. */
export function SearchPanel({ conversationId, onClose, onJump }: { conversationId: string; onClose: () => void; onJump: (id: string) => void }) {
  const [q, setQ] = useState('')
  const [result, setResult] = useState<Awaited<ReturnType<typeof kaluta.messages.search>> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const run = async () => {
    if (q.trim().length < 2) return
    setBusy(true)
    setError(null)
    try {
      setResult(await kaluta.messages.search(conversationId, q.trim()))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Search failed')
    } finally {
      setBusy(false)
    }
  }
  return (
    <SidePanel title="Search this conversation" onClose={onClose}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void run()
        }}
        className="relative flex items-center"
      >
        <Search size={14} className="pointer-events-none absolute start-3 text-text-low" aria-hidden="true" />
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoFocus
          type="search"
          placeholder="Words in a message or a file name…"
          aria-label="Search this conversation"
          className="w-full rounded-full border border-[var(--cloud-border)] bg-transparent py-2 pe-3 ps-9 text-sm text-text-hi"
        />
      </form>
      {busy && <p className="mt-3 text-xs text-text-low">Searching…</p>}
      {error && <p role="alert" className="mt-3 text-xs text-red-200">{error}</p>}
      {result && !busy && (
        <>
          {result.items.length === 0 ? (
            <p className="mt-3 text-xs text-text-low">Nothing found.</p>
          ) : (
            <ul className="mt-3 space-y-1.5">
              {result.items.map((hit) => (
                <li key={hit.id}>
                  <button
                    type="button"
                    onClick={() => {
                      onClose()
                      onJump(hit.id)
                    }}
                    className="w-full rounded-xl border border-[var(--cloud-border)] px-3 py-2 text-start hover:border-gold/40"
                  >
                    <span className="flex items-center gap-2 text-xs text-text-low">
                      <span className="font-semibold text-text-hi">{result.profiles[hit.sender_id]?.display_name ?? 'Member'}</span>
                      {new Date(hit.created_at).toLocaleString()}
                    </span>
                    <span className="mt-0.5 line-clamp-2 block text-sm text-text-mid">{hit.snippet}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {result.partial && (
            <p className="mt-2 text-[0.7rem] text-text-low">Only the most recent messages were searched. Messages are stored encrypted, so very long conversations are searched from the newest back.</p>
          )}
        </>
      )}
    </SidePanel>
  )
}

/** A message and its replies: a side conversation that keeps the main one readable. */
export function RepliesPanel({
  conversationId,
  messageId,
  onClose,
  onJump,
}: {
  conversationId: string
  messageId: string
  onClose: () => void
  onJump: (id: string) => void
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof kaluta.messages.replies>> | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    kaluta.messages
      .replies(conversationId, messageId)
      .then(setData)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the replies'))
  }, [conversationId, messageId])
  const line = (m: MessageBrief) => (
    <button
      type="button"
      onClick={() => {
        onClose()
        onJump(m.id)
      }}
      className="w-full rounded-xl px-3 py-2 text-start hover:bg-text-hi/[0.05]"
    >
      <span className="text-xs font-semibold text-text-hi">{data?.profiles[m.sender_id]?.display_name ?? 'Member'}</span>
      <span className="ms-2 text-[0.7rem] text-text-low">{new Date(m.created_at).toLocaleString()}</span>
      <span className="block whitespace-pre-wrap text-sm text-text-mid">{briefText(m)}</span>
    </button>
  )
  return (
    <SidePanel title="Replies" onClose={onClose}>
      {error && <p role="alert" className="text-xs text-red-200">{error}</p>}
      {!data && !error && <p className="text-xs text-text-low">Loading…</p>}
      {data && (
        <>
          <div className="rounded-xl border border-gold/30 bg-gold/[0.05]">{line(data.root)}</div>
          <ul className="mt-2 space-y-0.5 border-s-2 border-[var(--cloud-border)] ps-2">
            {data.items.map((m) => (
              <li key={m.id}>{line(m)}</li>
            ))}
          </ul>
          <p className="mt-3 text-[0.7rem] text-text-low">To add to it, reply to any of these messages in the conversation.</p>
        </>
      )}
    </SidePanel>
  )
}

/** The "+" beside the message box in a group: a poll, an event, a task. */
export function CardMenuButton({ canPoll, onPick }: { canPoll: boolean; onPick: (kind: 'poll' | 'event' | 'task') => void }) {
  const [open, setOpen] = useState(false)
  const item = 'flex w-full items-center gap-2.5 rounded-card-sm px-3 py-2 text-sm text-text-hi hover:bg-white/10'
  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Add a poll, an event or a task"
        aria-expanded={open}
        className="grid h-10 w-10 place-items-center rounded-full text-text-mid hover:bg-text-hi/[0.07] hover:text-gold-soft"
      >
        <Plus size={17} aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" className="cloud-card absolute bottom-12 start-0 z-20 w-44 rounded-card-md p-1 shadow-xl">
          {canPoll && (
            <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); onPick('poll') }}>
              <BarChart3 size={15} aria-hidden="true" />
              Poll
            </button>
          )}
          <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); onPick('event') }}>
            <CalendarDays size={15} aria-hidden="true" />
            Event
          </button>
          <button type="button" role="menuitem" className={item} onClick={() => { setOpen(false); onPick('task') }}>
            <CheckSquare size={15} aria-hidden="true" />
            Task
          </button>
        </div>
      )}
    </div>
  )
}

/** "Seen by N" under my latest message in a group, from the server's receipts. */
export function GroupSeenBy({ conversationId, messageId, version }: { conversationId: string; messageId: string; version: number }) {
  const [state, setState] = useState<{ read: number; members: number; readers: Array<{ display_name: string }> | null } | null>(null)
  useEffect(() => {
    let live = true
    const timer = window.setTimeout(() => {
      kaluta.messages
        .receipts(conversationId, messageId)
        .then((r) => live && setState(r))
        .catch(() => live && setState(null))
    }, 400)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [conversationId, messageId, version])
  if (!state) return null
  const names = state.readers?.map((r) => r.display_name).join(', ')
  return (
    <span className="caption mt-0.5 inline-flex items-center gap-1" title={names || undefined}>
      <CheckCheck size={12} className={cn(state.read ? 'text-gold-soft' : '')} aria-hidden="true" />
      {state.read ? `Seen by ${state.read} of ${state.members}` : 'Sent'}
    </span>
  )
}
