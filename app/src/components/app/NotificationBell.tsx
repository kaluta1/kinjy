import { announce } from '@/lib/live'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { Bell } from 'lucide-react'
import { kaluta } from '@/lib/api'
import { useTopic } from '@/hooks/useRealtime'
import { useAppTheme } from '@/components/appdemo/theme'
import { cn } from '@/lib/utils'

interface Item {
  id: number
  kind: string
  title: string
  body: string | null
  link: string | null
  read: boolean
  created_at: string
}

function ago(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

/**
 * The bell, doing what a bell does.
 *
 * It used to be a link to /messages: no count, no list, no delivery. The store
 * and its endpoints had existed from the start and nothing called them, so a
 * member had no way to learn that anyone had replied to them.
 *
 * The count arrives two ways on purpose — fetched once on mount for what
 * happened while you were away, and pushed over the socket for what happens
 * while you are here. Either alone leaves a hole.
 */
export default function NotificationBell({ userId }: { userId?: string }) {
  const { tok } = useAppTheme()
  const [items, setItems] = useState<Item[]>([])
  const [unread, setUnread] = useState(0)
  const [open, setOpen] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)

  const load = () => {
    void kaluta.notifications
      .list()
      .then((page) => {
        setItems(page.items)
        setUnread(page.unread)
      })
      .catch(() => undefined)
  }

  useEffect(load, [])

  // Live. The server sends the new unread count with the notification, so the
  // badge never has to be recomputed by refetching the list.
  useTopic(userId ? `user:${userId}` : null, (event) => {
    if (event.type !== 'notification') return
    if (typeof event.unread === 'number') setUnread(event.unread)
    // Somebody followed or connected: the counts in the profile card are
    // stale from this moment, and used to stay so until a reload.
    if (event.kind === 'follow') announce('profile')
    if (event.kind === 'connection_request' || event.kind === 'connection_accepted') announce('connections')
    setItems((current) => [
      {
        id: Number(event.id ?? Date.now()),
        kind: event.kind ?? 'activity',
        title: String(event.title ?? 'Something happened'),
        body: (event.body as string | null) ?? null,
        link: (event.link as string | null) ?? null,
        read: false,
        created_at: event.created_at ?? new Date().toISOString(),
      },
      ...current,
    ])
  })

  // Close on an outside click, the way every menu should.
  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const openList = () => {
    setOpen((v) => !v)
    // Marked read on opening rather than per item: you have seen them.
    if (!open && unread > 0) {
      setUnread(0)
      setItems((current) => current.map((i) => ({ ...i, read: true })))
      void kaluta.notifications.markRead().catch(() => undefined)
    }
  }

  return (
    <div ref={boxRef} className="relative">
      <button
        type="button"
        onClick={openList}
        aria-label={unread > 0 ? `Notifications, ${unread} unread` : 'Notifications'}
        aria-expanded={open}
        className={cn(
          'relative flex h-9 w-9 items-center justify-center rounded-full',
          tok.hoverBg,
          tok.mid,
        )}
      >
        <Bell size={16} />
        {unread > 0 && (
          <span className="absolute -end-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-gold px-1 text-[0.6rem] font-bold text-ink">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          className={cn(
            'absolute end-0 top-11 z-50 w-80 rounded-card-md p-2 shadow-cloud',
            tok.cardSolid,
          )}
          role="dialog"
          aria-label="Notifications"
        >
          {items.length === 0 ? (
            <p className={cn('px-3 py-6 text-center text-xs', tok.low)}>
              Nothing yet. Replies, follows and invitations land here.
            </p>
          ) : (
            <ul className="max-h-96 space-y-0.5 overflow-y-auto pr-1 [scrollbar-width:thin]">
              {items.slice(0, 30).map((item) => {
                const inner = (
                  <>
                    <span className={cn('block text-xs font-semibold', tok.text)}>{item.title}</span>
                    {item.body && (
                      <span className={cn('mt-0.5 block truncate text-[0.7rem]', tok.low)}>
                        {item.body}
                      </span>
                    )}
                    <span className={cn('mt-0.5 block text-[0.65rem]', tok.low)}>
                      {ago(item.created_at)}
                    </span>
                  </>
                )
                return (
                  <li key={item.id}>
                    {item.link ? (
                      <Link
                        to={item.link}
                        onClick={() => setOpen(false)}
                        className={cn('block rounded-card-sm px-3 py-2', tok.hoverBg)}
                      >
                        {inner}
                      </Link>
                    ) : (
                      <div className="px-3 py-2">{inner}</div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
