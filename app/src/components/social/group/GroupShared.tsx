import { useEffect, useState } from 'react'
import { CalendarDays, CheckSquare, File as FileIcon, Link2, Square } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { ApiError, kaluta, type GroupAuditEntry, type GroupEvent, type GroupTask } from '@/lib/api'
import { briefText } from './groupText'
import { cn } from '@/lib/utils'

function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    load()
      .then((d) => live && setData(d))
      .catch((err) => live && setError(err instanceof ApiError ? err.message : 'Could not load this'))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return { data, error, setData }
}

function Empty({ text }: { text: string }) {
  const { tok } = useAppTheme()
  return <p className={cn('text-xs', tok.low)}>{text}</p>
}

function when(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** Pinned messages, newest pin first. Clicking one goes to it in the thread. */
export function GroupPins({ conversationId, onJump }: { conversationId: string; onJump: (messageId: string) => void }) {
  const { tok } = useAppTheme()
  const { data, error } = useLoad(() => kaluta.messages.pins(conversationId), [conversationId])
  if (error) return <Empty text={error} />
  if (!data) return <Empty text="Loading…" />
  if (data.items.length === 0) return <Empty text="Nothing is pinned. Admins and moderators pin messages from the message’s menu." />
  return (
    <ul className="space-y-1.5">
      {data.items.map((m) => (
        <li key={m.id}>
          <button type="button" onClick={() => onJump(m.id)} className="w-full rounded-xl border border-[var(--cloud-border)] px-3 py-2 text-start hover:border-gold/40">
            <span className={cn('block text-xs font-semibold', tok.text)}>{data.profiles[m.sender_id]?.display_name ?? 'Member'}</span>
            <span className={cn('line-clamp-2 text-sm', tok.mid)}>{briefText(m)}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}


/** What was shared, in three tabs. Links to files are signed and expire like the thread's. */
export function GroupFiles({ conversationId }: { conversationId: string }) {
  const { tok } = useAppTheme()
  const [kind, setKind] = useState<'media' | 'files' | 'links'>('media')
  const { data, error } = useLoad(() => kaluta.messages.files(conversationId, kind), [conversationId, kind])
  return (
    <div>
      <div role="tablist" aria-label="Shared" className="mb-3 flex gap-1.5">
        {(
          [
            ['media', 'Photos and videos'],
            ['files', 'Files'],
            ['links', 'Links'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={kind === id}
            onClick={() => setKind(id)}
            className={cn(
              'rounded-full border px-3 py-1 text-xs font-semibold',
              kind === id ? 'border-gold/60 bg-gold/15 text-gold-soft' : cn('border-[var(--cloud-border)]', tok.mid),
            )}
          >
            {label}
          </button>
        ))}
      </div>
      {error ? (
        <Empty text={error} />
      ) : !data ? (
        <Empty text="Loading…" />
      ) : data.items.length === 0 ? (
        <Empty text={kind === 'links' ? 'No links shared yet.' : kind === 'media' ? 'No photos or videos yet.' : 'No files yet.'} />
      ) : kind === 'media' ? (
        <ul className="grid grid-cols-3 gap-1.5 sm:grid-cols-4">
          {data.items.map((m) => (
            <li key={m.id}>
              <a href={m.media_url ?? '#'} target="_blank" rel="noreferrer" className="block aspect-square overflow-hidden rounded-lg bg-text-hi/[0.05]">
                {m.media_kind === 'image' && m.media_url ? (
                  <img src={m.media_url} alt={m.media_name ?? ''} loading="lazy" className="h-full w-full object-cover" />
                ) : (
                  <span className={cn('grid h-full w-full place-items-center text-xs', tok.mid)}>Video</span>
                )}
              </a>
            </li>
          ))}
        </ul>
      ) : kind === 'files' ? (
        <ul className="space-y-1">
          {data.items.map((m) => (
            <li key={m.id}>
              <a href={m.media_url ?? '#'} target="_blank" rel="noreferrer" className={cn('flex items-center gap-2 rounded-xl px-2 py-1.5 text-sm hover:bg-text-hi/[0.05]', tok.text)}>
                <FileIcon size={14} className="shrink-0 text-gold-soft" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{m.media_name ?? 'File'}</span>
                <span className={cn('shrink-0 text-xs', tok.low)}>{new Date(m.created_at).toLocaleDateString()}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : (
        <ul className="space-y-1">
          {data.items.map((m, i) => (
            <li key={`${m.id}-${i}`}>
              <a href={m.url} target="_blank" rel="noreferrer noopener" className={cn('flex items-center gap-2 rounded-xl px-2 py-1.5 text-sm hover:bg-text-hi/[0.05]', tok.text)}>
                <Link2 size={14} className="shrink-0 text-gold-soft" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{m.url}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Upcoming events and the group's tasks, with their own actions. */
export function GroupPlans({ conversationId, onJump }: { conversationId: string; onJump: (messageId: string) => void }) {
  const { tok } = useAppTheme()
  const events = useLoad(() => kaluta.messages.events(conversationId), [conversationId])
  const tasks = useLoad(() => kaluta.messages.tasks(conversationId), [conversationId])
  const [note, setNote] = useState<string | null>(null)

  const tick = async (task: GroupTask) => {
    try {
      const next = await kaluta.messages.updateTask(conversationId, task.id, { done: !task.done })
      tasks.setData((d) => (d ? { items: d.items.map((t) => (t.id === task.id ? { ...t, ...next } : t)) } : d))
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'Could not change the task')
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h4 className={cn('mb-1.5 flex items-center gap-1.5 text-xs font-semibold', tok.text)}>
          <CalendarDays size={13} aria-hidden="true" />
          Upcoming events
        </h4>
        {!events.data ? (
          <Empty text={events.error ?? 'Loading…'} />
        ) : events.data.items.length === 0 ? (
          <Empty text="No upcoming event. Create one from the + beside the message box." />
        ) : (
          <ul className="space-y-1.5">
            {events.data.items.map((e: GroupEvent) => (
              <li key={e.id}>
                <button
                  type="button"
                  onClick={() => e.message_id && onJump(e.message_id)}
                  className="w-full rounded-xl border border-[var(--cloud-border)] px-3 py-2 text-start hover:border-gold/40"
                >
                  <span className={cn('block text-sm font-semibold', tok.text)}>{e.title}</span>
                  <span className={cn('block text-xs', tok.mid)}>
                    {when(e.starts_at)} · {e.counts.going} going{e.my_response ? ` · you: ${e.my_response.replace('_', ' ')}` : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div>
        <h4 className={cn('mb-1.5 flex items-center gap-1.5 text-xs font-semibold', tok.text)}>
          <CheckSquare size={13} aria-hidden="true" />
          Tasks
        </h4>
        {!tasks.data ? (
          <Empty text={tasks.error ?? 'Loading…'} />
        ) : tasks.data.items.length === 0 ? (
          <Empty text="No tasks yet." />
        ) : (
          <ul className="space-y-1">
            {tasks.data.items.map((t) => (
              <li key={t.id} className="flex items-start gap-2 rounded-xl px-1 py-1">
                <button
                  type="button"
                  disabled={!t.can_complete}
                  onClick={() => void tick(t)}
                  aria-label={t.done ? `Mark "${t.title}" as not done` : `Mark "${t.title}" as done`}
                  className="mt-0.5 shrink-0 text-gold-soft disabled:opacity-40"
                >
                  {t.done ? <CheckSquare size={16} aria-hidden="true" /> : <Square size={16} aria-hidden="true" />}
                </button>
                <span className="min-w-0 flex-1">
                  <span className={cn('block text-sm', tok.text, t.done && 'line-through opacity-60')}>{t.title}</span>
                  <span className={cn('block text-xs', tok.low)}>
                    {t.assignee_name ? `For ${t.assignee_name}` : 'For anybody'}
                    {t.due_at ? ` · due ${when(t.due_at)}` : ''}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
        {note && <p role="status" className={cn('mt-1 text-xs', tok.mid)}>{note}</p>}
      </div>
    </div>
  )
}

/** The group's sensitive changes: who changed a role, removed somebody, handed it on. */
export function GroupAuditLog({ conversationId }: { conversationId: string }) {
  const { tok } = useAppTheme()
  const { data, error } = useLoad(() => kaluta.messages.audit(conversationId), [conversationId])
  if (error) return <Empty text={error} />
  if (!data) return <Empty text="Loading…" />
  if (data.items.length === 0) return <Empty text="Nothing recorded yet." />
  return (
    <ul className="space-y-1.5">
      {data.items.map((a: GroupAuditEntry) => (
        <li key={a.id} className="text-xs">
          <span className={tok.text}>{a.summary}</span>
          {a.detail && <span className={cn('ms-1', tok.low)}>({a.detail})</span>}
          <span className={cn('block', tok.low)}>{when(a.created_at)}</span>
        </li>
      ))}
    </ul>
  )
}

