import { useState } from 'react'
import { BarChart3, CalendarDays, CheckSquare, ExternalLink, MapPin, Square, X } from 'lucide-react'
import ConfirmDialog from '@/components/ui-kit/ConfirmDialog'
import { ApiError, kaluta, type GroupEvent, type GroupPoll, type GroupTask } from '@/lib/api'
import { cn } from '@/lib/utils'

/*
 * The cards a group posts into its thread - a poll, an event, a task - and the
 * forms that make them. Drawn inside a message bubble, so they keep to the
 * bubble's colours: `mine` is the gold bubble with dark text.
 */

function useCard<T>(initial: T) {
  const [card, setCard] = useState<T>(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const act = async (action: () => Promise<T>) => {
    setBusy(true)
    setError(null)
    try {
      setCard(await action())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not work. Try again.')
    } finally {
      setBusy(false)
    }
  }
  return { card, setCard, busy, error, act }
}

const sub = (mine: boolean) => (mine ? 'text-ink/70' : 'text-text-low')

export function PollCard({ conversationId, poll, mine }: { conversationId: string; poll: GroupPoll; mine: boolean }) {
  const { card, busy, error, act } = useCard(poll)
  const [picked, setPicked] = useState<number[]>(poll.my_votes)
  const voted = card.my_votes.length > 0
  const total = card.options.reduce((n, o) => n + (o.votes ?? 0), 0)

  const choose = (id: number) => {
    if (card.closed) return
    if (!card.multiple) {
      setPicked([id])
      void act(() => kaluta.messages.vote(conversationId, card.id, [id]))
      return
    }
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  }

  return (
    <div className="min-w-[220px] max-w-[320px]">
      <p className="flex items-center gap-1.5 text-xs font-semibold">
        <BarChart3 size={13} aria-hidden="true" />
        Poll{card.multiple ? ' · several answers' : ''}
        {card.closed && ' · closed'}
      </p>
      <p className="mt-1 text-[15px] font-semibold leading-snug">{card.question}</p>
      <ul className="mt-2 space-y-1.5" role={card.multiple ? 'group' : 'radiogroup'} aria-label={card.question}>
        {card.options.map((o) => {
          const share = card.results_visible && total ? Math.round(((o.votes ?? 0) / total) * 100) : 0
          const chosen = picked.includes(o.id)
          return (
            <li key={o.id}>
              <button
                type="button"
                role={card.multiple ? 'checkbox' : 'radio'}
                aria-checked={chosen}
                disabled={busy || card.closed}
                onClick={() => choose(o.id)}
                className={cn(
                  'relative w-full overflow-hidden rounded-xl border px-3 py-1.5 text-start text-sm disabled:cursor-default',
                  mine ? 'border-ink/25' : 'border-[var(--cloud-border)]',
                  chosen && (mine ? 'border-ink/60' : 'border-gold/60'),
                )}
              >
                {card.results_visible && (
                  <span
                    aria-hidden="true"
                    className={cn('absolute inset-y-0 start-0', mine ? 'bg-ink/10' : 'bg-gold/15')}
                    style={{ width: `${share}%` }}
                  />
                )}
                <span className="relative flex items-center gap-2">
                  <span className="min-w-0 flex-1">{o.text}</span>
                  {card.results_visible && <span className="shrink-0 text-xs font-semibold">{o.votes ?? 0}</span>}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      {card.multiple && !card.closed && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void act(() => kaluta.messages.vote(conversationId, card.id, picked))}
          className={cn('mt-2 rounded-full px-3 py-1 text-xs font-bold', mine ? 'bg-ink/15' : 'bg-gold/20 text-gold-soft')}
        >
          {picked.length ? 'Vote' : voted ? 'Take back my vote' : 'Vote'}
        </button>
      )}
      <p className={cn('mt-1.5 text-xs', sub(mine))}>
        {card.results_visible
          ? `${card.voters ?? 0} ${card.voters === 1 ? 'person voted' : 'people voted'}`
          : card.results === 'after_close'
            ? 'Results are shown when the poll closes'
            : 'Results are shown once you vote'}
        {card.closes_at && !card.closed && ` · closes ${new Date(card.closes_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}`}
        {' · who voted for what is never shown'}
      </p>
      {card.can_close && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void act(() => kaluta.messages.closePoll(conversationId, card.id))}
          className={cn('mt-1 text-xs font-semibold underline', sub(mine))}
        >
          Close the poll
        </button>
      )}
      {error && <p role="alert" className="mt-1 text-xs text-red-300">{error}</p>}
    </div>
  )
}

function eventTimes(event: GroupEvent) {
  const start = new Date(event.starts_at)
  const mine = start.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' })
  let there: string | null = null
  try {
    const local = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (event.timezone && event.timezone !== local) {
      there = start.toLocaleString(undefined, { timeZone: event.timezone, timeStyle: 'short', dateStyle: 'medium' }) + ` (${event.timezone})`
    }
  } catch {
    there = null
  }
  return { mine, there }
}

export function EventCard({ conversationId, event, mine }: { conversationId: string; event: GroupEvent; mine: boolean }) {
  const { card, busy, error, act } = useCard(event)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const { mine: at, there } = eventTimes(card)
  return (
    <div className="min-w-[220px] max-w-[320px]">
      <p className="flex items-center gap-1.5 text-xs font-semibold">
        <CalendarDays size={13} aria-hidden="true" />
        Event{card.cancelled && ' · cancelled'}
      </p>
      <p className={cn('mt-1 text-[15px] font-semibold leading-snug', card.cancelled && 'line-through')}>{card.title}</p>
      <p className="mt-1 text-sm">{at}</p>
      {there && <p className={cn('text-xs', sub(mine))}>There: {there}</p>}
      {card.location && (
        <p className="mt-1 flex items-start gap-1 text-sm">
          <MapPin size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          {card.location}
        </p>
      )}
      {card.online_url && (
        <a href={card.online_url} target="_blank" rel="noreferrer noopener" className="mt-1 inline-flex items-center gap-1 text-sm font-semibold underline">
          <ExternalLink size={13} aria-hidden="true" />
          Join online
        </a>
      )}
      {card.description && <p className="mt-1 whitespace-pre-wrap text-sm">{card.description}</p>}
      <p className={cn('mt-1.5 text-xs', sub(mine))}>
        {card.counts.going} going · {card.counts.maybe} maybe · {card.counts.not_going} not going
      </p>
      {!card.cancelled && (
        <div className="mt-2 flex flex-wrap gap-1.5" role="radiogroup" aria-label="Will you come?">
          {(
            [
              ['going', 'Going'],
              ['maybe', 'Maybe'],
              ['not_going', 'Can’t go'],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              role="radio"
              aria-checked={card.my_response === id}
              disabled={busy}
              onClick={() => void act(() => kaluta.messages.respondEvent(conversationId, card.id, id))}
              className={cn(
                'rounded-full border px-3 py-1 text-xs font-semibold',
                mine ? 'border-ink/30' : 'border-[var(--cloud-border)]',
                card.my_response === id && (mine ? 'bg-ink/15' : 'border-gold/60 bg-gold/15 text-gold-soft'),
              )}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {card.can_manage && (
        <button
          type="button"
          disabled={busy}
          onClick={() => setConfirmCancel(true)}
          className={cn('mt-1.5 text-xs font-semibold underline', sub(mine))}
        >
          Cancel the event
        </button>
      )}
      {error && <p role="alert" className="mt-1 text-xs text-red-300">{error}</p>}
      <ConfirmDialog
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="Cancel this event?"
        description="It stays in the conversation, marked as cancelled. Everybody who answered Going or Maybe is told."
        confirmLabel="Cancel the event"
        cancelLabel="Keep it"
        onConfirm={() => {
          setConfirmCancel(false)
          void act(() => kaluta.messages.cancelEvent(conversationId, card.id))
        }}
      />
    </div>
  )
}

export function TaskCard({ conversationId, task, mine }: { conversationId: string; task: GroupTask; mine: boolean }) {
  const { card, busy, error, act } = useCard(task)
  return (
    <div className="min-w-[200px] max-w-[320px]">
      <p className="flex items-center gap-1.5 text-xs font-semibold">
        <CheckSquare size={13} aria-hidden="true" />
        Task
      </p>
      <button
        type="button"
        disabled={busy || !card.can_complete}
        onClick={() => void act(() => kaluta.messages.updateTask(conversationId, card.id, { done: !card.done }))}
        aria-label={card.done ? 'Mark as not done' : 'Mark as done'}
        className="mt-1 flex w-full items-start gap-2 text-start disabled:cursor-default"
      >
        {card.done ? <CheckSquare size={18} className="mt-0.5 shrink-0" aria-hidden="true" /> : <Square size={18} className="mt-0.5 shrink-0" aria-hidden="true" />}
        <span className={cn('text-[15px] font-semibold leading-snug', card.done && 'line-through opacity-70')}>{card.title}</span>
      </button>
      <p className={cn('mt-1 text-xs', sub(mine))}>
        {card.assignee_name ? `For ${card.assignee_name}` : 'For anybody in the group'}
        {card.due_at ? ` · due ${new Date(card.due_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}` : ''}
      </p>
      {error && <p role="alert" className="mt-1 text-xs text-red-300">{error}</p>}
    </div>
  )
}

/* --- the forms ------------------------------------------------------------- */

const field = 'w-full rounded-xl border border-white/12 bg-text-hi/[0.05] px-3 py-2 text-sm text-text-hi placeholder:text-text-low focus:border-gold/50 focus:outline-none'

function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="fixed inset-0 z-[85] flex items-end justify-center bg-black/55 sm:items-center sm:p-6" onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} className="cloud-card max-h-[92svh] w-full max-w-md overflow-y-auto rounded-t-3xl p-5 sm:rounded-3xl">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="flex-1 text-base font-bold text-text-hi">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded-full p-1.5 text-text-mid hover:bg-text-hi/[0.07]">
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

function Submit({ busy, disabled, label }: { busy: boolean; disabled?: boolean; label: string }) {
  return (
    <button type="submit" disabled={busy || disabled} className="mt-4 w-full rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink disabled:opacity-50">
      {busy ? 'Posting…' : label}
    </button>
  )
}

function useSubmit(onDone: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'That did not work. Try again.')
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, submit }
}

export function PollForm({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const [question, setQuestion] = useState('')
  const [options, setOptions] = useState(['', ''])
  const [multiple, setMultiple] = useState(false)
  const [results, setResults] = useState<GroupPoll['results']>('always')
  const [closes, setCloses] = useState('')
  const { busy, error, submit } = useSubmit(onClose)
  const filled = options.map((o) => o.trim()).filter(Boolean)
  return (
    <Sheet title="New poll" onClose={onClose}>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          void submit(() =>
            kaluta.messages.createPoll(conversationId, {
              question: question.trim(),
              options: filled,
              multiple,
              results,
              closes_at: closes ? new Date(closes).toISOString() : null,
            }),
          )
        }}
      >
        <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={300} placeholder="Question" aria-label="Question" autoFocus className={field} />
        {options.map((o, i) => (
          <div key={i} className="flex gap-1.5">
            <input
              value={o}
              onChange={(e) => setOptions((all) => all.map((x, j) => (j === i ? e.target.value : x)))}
              maxLength={200}
              placeholder={`Answer ${i + 1}`}
              aria-label={`Answer ${i + 1}`}
              className={field}
            />
            {options.length > 2 && (
              <button type="button" aria-label={`Remove answer ${i + 1}`} onClick={() => setOptions((all) => all.filter((_, j) => j !== i))} className="rounded-full px-2 text-text-mid">
                <X size={14} aria-hidden="true" />
              </button>
            )}
          </div>
        ))}
        {options.length < 10 && (
          <button type="button" onClick={() => setOptions((all) => [...all, ''])} className="text-xs font-semibold text-gold-soft">
            + Add an answer
          </button>
        )}
        <label className="flex items-center gap-2 text-sm text-text-mid">
          <input type="checkbox" className="accent-[#E5B556]" checked={multiple} onChange={(e) => setMultiple(e.target.checked)} />
          Several answers allowed
        </label>
        <label className="block text-xs text-text-mid">
          Results
          <select value={results} onChange={(e) => setResults(e.target.value as GroupPoll['results'])} className={cn(field, 'mt-1')}>
            <option value="always">Always visible</option>
            <option value="after_vote">After voting</option>
            <option value="after_close">When the poll closes</option>
          </select>
        </label>
        <label className="block text-xs text-text-mid">
          Closes (optional)
          <input type="datetime-local" value={closes} onChange={(e) => setCloses(e.target.value)} className={cn(field, 'mt-1')} />
        </label>
        <p className="text-xs text-text-low">Results are counts only: nobody sees who voted for what.</p>
        {error && <p role="alert" className="text-xs text-red-200">{error}</p>}
        <Submit busy={busy} disabled={!question.trim() || filled.length < 2} label="Post the poll" />
      </form>
    </Sheet>
  )
}

function localZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function EventForm({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [starts, setStarts] = useState('')
  const [ends, setEnds] = useState('')
  const [zone, setZone] = useState(localZone())
  const [location, setLocation] = useState('')
  const [url, setUrl] = useState('')
  const { busy, error, submit } = useSubmit(onClose)
  const zones = (() => {
    try {
      return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? [zone]
    } catch {
      return [zone]
    }
  })()
  return (
    <Sheet title="New event" onClose={onClose}>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          void submit(() =>
            kaluta.messages.createEvent(conversationId, {
              title: title.trim(),
              description: description.trim() || undefined,
              // The times are typed as wall-clock time in your own zone.
              starts_at: new Date(starts).toISOString(),
              ends_at: ends ? new Date(ends).toISOString() : null,
              timezone: zone,
              location: location.trim() || undefined,
              online_url: url.trim() || undefined,
            }),
          )
        }}
      >
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="What" aria-label="Title" autoFocus className={field} />
        <label className="block text-xs text-text-mid">
          Starts (your time)
          <input type="datetime-local" required value={starts} onChange={(e) => setStarts(e.target.value)} className={cn(field, 'mt-1')} />
        </label>
        <label className="block text-xs text-text-mid">
          Ends (optional)
          <input type="datetime-local" value={ends} onChange={(e) => setEnds(e.target.value)} className={cn(field, 'mt-1')} />
        </label>
        <label className="block text-xs text-text-mid">
          Where it happens (time zone)
          <select value={zone} onChange={(e) => setZone(e.target.value)} className={cn(field, 'mt-1')}>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
        <input value={location} onChange={(e) => setLocation(e.target.value)} maxLength={300} placeholder="Place (optional)" aria-label="Place" className={field} />
        <input value={url} onChange={(e) => setUrl(e.target.value)} maxLength={500} placeholder="Online meeting link (optional)" aria-label="Online meeting link" className={field} />
        <textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={4000} rows={3} placeholder="Details (optional)" aria-label="Details" className={cn(field, 'resize-none')} />
        <p className="text-xs text-text-low">Members who answer “Going” or “Maybe” are reminded the day before and an hour before, as their notification settings allow.</p>
        {error && <p role="alert" className="text-xs text-red-200">{error}</p>}
        <Submit busy={busy} disabled={!title.trim() || !starts} label="Post the event" />
      </form>
    </Sheet>
  )
}

export function TaskForm({
  conversationId,
  members,
  onClose,
}: {
  conversationId: string
  members: Array<{ user_id: string; display_name: string }>
  onClose: () => void
}) {
  const [title, setTitle] = useState('')
  const [assignee, setAssignee] = useState('')
  const [due, setDue] = useState('')
  const { busy, error, submit } = useSubmit(onClose)
  return (
    <Sheet title="New task" onClose={onClose}>
      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault()
          void submit(() =>
            kaluta.messages.createTask(conversationId, {
              title: title.trim(),
              assignee_id: assignee || null,
              due_at: due ? new Date(due).toISOString() : null,
            }),
          )
        }}
      >
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} placeholder="What needs doing" aria-label="Task" autoFocus className={field} />
        <label className="block text-xs text-text-mid">
          For
          <select value={assignee} onChange={(e) => setAssignee(e.target.value)} className={cn(field, 'mt-1')}>
            <option value="">Anybody in the group</option>
            {members.map((m) => (
              <option key={m.user_id} value={m.user_id}>
                {m.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-text-mid">
          Due (optional)
          <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} className={cn(field, 'mt-1')} />
        </label>
        <p className="text-xs text-text-low">The person it is for is told, and reminded when it is due.</p>
        {error && <p role="alert" className="text-xs text-red-200">{error}</p>}
        <Submit busy={busy} disabled={!title.trim()} label="Post the task" />
      </form>
    </Sheet>
  )
}
