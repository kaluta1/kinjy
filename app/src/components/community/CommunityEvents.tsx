import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { CalendarDays, Check, Link2, MapPin, Pencil, Users, Video } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type CommunityEvent, type CommunityQuote, type EventInput } from '@/lib/api'
import { copyLink, siteOrigin } from '@/lib/share'
import { cn } from '@/lib/utils'
import QuotePayment from './QuotePayment'

const MODE = { online: 'Online', in_person: 'In person', hybrid: 'Hybrid' } as const
const BTN = 'rounded-full border border-[var(--cloud-border)] px-3.5 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'
const FIELD = 'w-full rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm text-text-hi focus:border-gold/50 focus:outline-none'

/** The reader's local time, and the event's own zone when it differs. */
function when(e: CommunityEvent) {
  const start = new Date(e.starts_at)
  const local = start.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' })
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone
  if (e.timezone === here) return local
  const there = start.toLocaleString(undefined, { timeStyle: 'short', timeZone: e.timezone })
  return `${local} (${there} in ${e.timezone.replace(/_/g, ' ')})`
}

/** The event's own page: the address to share. */
function eventUrl(id: string): string {
  return `${siteOrigin()}/events/${id}`
}

/** "Copy link", saying when it worked - and showing the address when it could not copy. */
function CopyEventLink({ id }: { id: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  return (
    <>
      <button
        type="button"
        className={BTN}
        onClick={() => void copyLink(eventUrl(id)).then(setState)}
      >
        {state === 'copied' ? <Check size={12} className="me-1 inline" aria-hidden="true" /> : <Link2 size={12} className="me-1 inline" aria-hidden="true" />}
        {state === 'copied' ? 'Link copied' : 'Copy link'}
      </button>
      {state === 'failed' && (
        <input readOnly value={eventUrl(id)} onFocus={(e) => e.target.select()} aria-label="Event link" className={cn(FIELD, 'mt-2 w-full text-xs')} />
      )}
    </>
  )
}

/**
 * One event: when, where, places, and what the reader can do - register, get a
 * ticket, copy its link, and (host or moderators) edit or cancel it. Used in
 * the community's Events tab and on the event's own page.
 */
export function EventCard({
  event: e,
  member,
  steward,
  linkTitle = true,
  onChanged,
}: {
  event: CommunityEvent
  member: boolean
  steward: boolean
  /** In a list the title opens the event's page; on that page it does not. */
  linkTitle?: boolean
  onChanged: () => void
}) {
  const [quote, setQuote] = useState<CommunityQuote | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true)
    setNote(null)
    try {
      await fn()
      if (ok) setNote({ ok: true, text: ok })
      onChanged()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <EventForm
        communityId={e.community_id}
        paidEnabled={Boolean(e.ticket_price)}
        event={e}
        onDone={() => {
          setEditing(false)
          setNote({ ok: true, text: 'Event updated.' })
          onChanged()
        }}
        onCancel={() => setEditing(false)}
      />
    )
  }

  return (
    <div className={cn('cloud-card p-4', e.status === 'cancelled' && 'opacity-60')}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-text-hi">
            {linkTitle ? (
              <Link to={`/events/${e.id}`} className="hover:underline">{e.title}</Link>
            ) : (
              e.title
            )}{' '}
            {e.status === 'cancelled' && <span className="text-sm text-red-300">· cancelled</span>}
          </p>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-text-mid">
            <CalendarDays size={13} aria-hidden="true" /> {when(e)}
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-sm text-text-low">
            {e.mode === 'in_person' ? <MapPin size={13} aria-hidden="true" /> : <Video size={13} aria-hidden="true" />}
            {MODE[e.mode]}{e.location ? ` · ${e.location}` : ''}
          </p>
          {e.capacity && (
            <p className="mt-0.5 flex items-center gap-1.5 text-sm text-text-low">
              <Users size={13} aria-hidden="true" /> {e.places_left} of {e.capacity} places left
            </p>
          )}
        </div>
        {e.ticket_price && <p className="font-bold text-gold-soft">${e.ticket_price}</p>}
      </div>
      {e.description && <p className="mt-2 whitespace-pre-wrap text-sm text-text-mid">{e.description}</p>}
      {e.speakers.length > 0 && <p className="mt-2 text-sm text-text-mid">With {e.speakers.join(', ')}</p>}
      {e.online_url && (
        <a href={e.online_url} target="_blank" rel="noopener noreferrer" className="mt-2 inline-block text-sm font-semibold text-gold-soft hover:underline">
          Join link
        </a>
      )}
      {e.recording_consent && <p className="caption mt-1">This event may be recorded.</p>}
      {note && <p role={note.ok ? 'status' : 'alert'} className={cn('mt-2 text-sm', note.ok ? 'text-emerald-300' : 'text-red-300')}>{note.text}</p>}
      {quote ? (
        <div className="mt-3">
          <QuotePayment quote={quote} returnPath={`/events/${e.id}`} onCancel={() => setQuote(null)}
            onPaid={() => { setQuote(null); setNote({ ok: true, text: 'Paid: your place is being confirmed.' }); onChanged() }} />
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          {e.status === 'scheduled' && member && (
            e.my_status === 'registered' ? (
              <>
                <span className="rounded-full bg-emerald-400/15 px-3 py-1.5 text-xs font-bold text-emerald-300">You are registered</span>
                {!e.ticket_price && (
                  <button type="button" className={BTN} disabled={busy}
                    onClick={() => void act(() => kaluta.communityPaid.unregister(e.id), 'Your place is given back.')}>
                    I can no longer come
                  </button>
                )}
              </>
            ) : e.my_status === 'waitlisted' ? (
              <>
                <span className="rounded-full bg-amber-400/15 px-3 py-1.5 text-xs font-bold text-amber-300">On the waitlist</span>
                <button type="button" className={BTN} disabled={busy}
                  onClick={() => void act(() => kaluta.communityPaid.unregister(e.id))}>Leave the waitlist</button>
              </>
            ) : e.ticket_price ? (
              <button type="button" disabled={busy || e.places_left === 0}
                className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
                onClick={() => void act(async () => setQuote(await kaluta.communityPaid.quote(e.community_id, { event_id: e.id })))}>
                {e.places_left === 0 ? 'Sold out' : 'Get a ticket'}
              </button>
            ) : (
              <button type="button" disabled={busy}
                className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
                onClick={() => void act(() => kaluta.communityPaid.register(e.id))}>
                {e.capacity && e.places_left === 0 ? 'Join the waitlist' : 'Register'}
              </button>
            )
          )}
          <CopyEventLink id={e.id} />
          {e.status === 'scheduled' && e.can_edit && (
            <button type="button" className={BTN} onClick={() => setEditing(true)}>
              <Pencil size={12} className="me-1 inline" aria-hidden="true" />
              Edit event
            </button>
          )}
          {e.status === 'scheduled' && steward && (
            <button type="button" className={BTN} disabled={busy}
              onClick={() => {
                if (window.confirm('Cancel this event? Everybody registered is told; paid tickets are refunded.'))
                  void act(() => kaluta.communityPaid.cancelEvent(e.id), 'Event cancelled.')
              }}>
              Cancel event
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/** The next few events, at the top of the community's main page. */
export function UpcomingEvents({ communityId, onSeeAll }: { communityId: string; onSeeAll: () => void }) {
  const events = useApi(() => kaluta.communityPaid.events(communityId), [communityId])
  const next = (events.data?.items ?? []).filter((e) => e.status === 'scheduled').slice(0, 3)
  if (next.length === 0) return null
  return (
    <section aria-label="Upcoming events" className="cloud-card mb-4 p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-sm font-bold text-text-hi">Upcoming events</h3>
        <button type="button" onClick={onSeeAll} className="text-xs font-semibold text-gold-soft hover:underline">
          See all
        </button>
      </div>
      <ul className="space-y-2">
        {next.map((e) => (
          <li key={e.id}>
            <Link to={`/events/${e.id}`} className="block rounded-xl px-2 py-1.5 hover:bg-text-hi/[0.05]">
              <span className="block truncate text-sm font-semibold text-text-hi">{e.title}</span>
              <span className="flex items-center gap-1.5 text-xs text-text-low">
                <CalendarDays size={12} aria-hidden="true" />
                {new Date(e.starts_at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}
                {' · '}
                {MODE[e.mode]}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * A community's events: who can come, how many places are left, the waitlist,
 * free registration or a paid ticket. The joining link shows once registered.
 */
export default function CommunityEvents({
  communityId,
  steward,
  member,
}: {
  communityId: string
  slug: string
  steward: boolean
  member: boolean
}) {
  const events = useApi(() => kaluta.communityPaid.events(communityId), [communityId])
  // Whether Kinjy has paid features on (an admin switch): tickets need it.
  const paidState = useApi(() => (steward ? kaluta.communityPaid.tiers(communityId) : Promise.resolve(null)), [communityId, steward])
  const paidEnabled = Boolean(paidState.data?.paid_enabled)
  const [creating, setCreating] = useState(false)
  const [created, setCreated] = useState<CommunityEvent | null>(null)

  return (
    <section aria-label="Events" className="space-y-3">
      {steward && (
        <button type="button" className={BTN} onClick={() => setCreating(!creating)} aria-expanded={creating}>
          {creating ? 'Close' : 'New event'}
        </button>
      )}
      {creating && (
        <EventForm
          communityId={communityId}
          paidEnabled={paidEnabled}
          onDone={(event) => {
            setCreating(false)
            setCreated(event)
            events.reload()
          }}
          onCancel={() => setCreating(false)}
        />
      )}
      {created && (
        <div role="status" className="cloud-card flex flex-wrap items-center gap-2 p-3 text-sm text-emerald-300">
          Event created.{' '}
          <Link to={`/events/${created.id}`} className="font-semibold text-gold-soft hover:underline">Open its page</Link>
          <CopyEventLink id={created.id} />
        </div>
      )}
      {events.loading && <p className="caption">Loading events…</p>}
      {events.error && <p className="text-sm text-text-mid">{events.error}</p>}
      {events.data?.items.length === 0 && <p className="cloud-card p-4 text-sm text-text-mid">No event planned yet.</p>}
      <ul className="space-y-3">
        {events.data?.items.map((e) => (
          <li key={e.id}>
            <EventCard event={e} member={member} steward={steward} onChanged={events.reload} />
          </li>
        ))}
      </ul>
    </section>
  )
}

/** A UTC instant as the value a datetime-local picker shows (the reader's local time). */
function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** Create an event, or edit one (`event` given): the same fields either way. */
export function EventForm({
  communityId,
  paidEnabled,
  event,
  onDone,
  onCancel,
}: {
  communityId: string
  paidEnabled: boolean
  event?: CommunityEvent
  onDone: (event: CommunityEvent) => void
  onCancel?: () => void
}) {
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone
  const [form, setForm] = useState(() => ({
    title: event?.title ?? '',
    description: event?.description ?? '',
    start: toLocalInput(event?.starts_at),
    end: toLocalInput(event?.ends_at),
    mode: event?.mode ?? 'online',
    location: event?.location ?? '',
    // The joining link comes back only to hosts, which an editor is.
    url: event?.online_url ?? '',
    capacity: event?.capacity ? String(event.capacity) : '',
    waitlist: event?.waitlist ?? true,
    price: event?.ticket_owner_price ?? '',
    speakers: (event?.speakers ?? []).join(', '),
    recording: event?.recording_consent ?? false,
  }))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value })

  const submit = async (submitted: FormEvent) => {
    submitted.preventDefault()
    setError(null)
    if (form.title.trim().length < 3 || !form.start) {
      setError('A title of at least 3 characters and a start time are needed.')
      return
    }
    if (form.end && new Date(form.end) <= new Date(form.start)) {
      setError('The event must end after it starts.')
      return
    }
    if (form.url.trim() && !form.url.trim().startsWith('https://')) {
      setError('The joining link must start with https://')
      return
    }
    const input: EventInput = {
      title: form.title.trim(),
      description: form.description.trim(),
      // The picker gives local time; sent as UTC with the zone it is in.
      starts_at: new Date(form.start).toISOString(),
      ends_at: form.end ? new Date(form.end).toISOString() : event ? null : undefined,
      timezone: event && form.start === toLocalInput(event.starts_at) ? event.timezone : here,
      mode: form.mode,
      location: form.location.trim() || (event ? null : undefined),
      online_url: form.url.trim() || (event ? null : undefined),
      capacity: form.capacity ? Number(form.capacity) : event ? null : undefined,
      waitlist: form.waitlist,
      ticket_price: form.price ? Number(form.price) : event ? null : undefined,
      speakers: form.speakers.split(',').map((s) => s.trim()).filter(Boolean),
      recording_consent: form.recording,
    }
    setBusy(true)
    try {
      if (event) {
        // An unchanged start is not sent: an event already under way can still be edited.
        const patch: Partial<EventInput> = { ...input }
        if (form.start === toLocalInput(event.starts_at)) delete patch.starts_at
        onDone(await kaluta.communityPaid.updateEvent(event.id, patch))
      } else {
        onDone(await kaluta.communityPaid.createEvent(communityId, input as Parameters<typeof kaluta.communityPaid.createEvent>[1]))
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : event ? 'Could not save the event' : 'Could not create the event')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="cloud-card grid gap-3 p-4 sm:grid-cols-2">
      {event && <p className="sm:col-span-2 text-sm font-semibold text-text-hi">Edit event</p>}
      <label className="sm:col-span-2 text-sm text-text-mid">Title<input className={cn(FIELD, 'mt-1')} value={form.title} onChange={set('title')} maxLength={200} /></label>
      <label className="text-sm text-text-mid">Starts ({here})<input type="datetime-local" className={cn(FIELD, 'mt-1')} value={form.start} onChange={set('start')} /></label>
      <label className="text-sm text-text-mid">Ends<input type="datetime-local" className={cn(FIELD, 'mt-1')} value={form.end} onChange={set('end')} /></label>
      <label className="text-sm text-text-mid">Where
        <select className={cn(FIELD, 'mt-1')} value={form.mode} onChange={set('mode')}>
          <option value="online">Online</option><option value="in_person">In person</option><option value="hybrid">Hybrid</option>
        </select>
      </label>
      <label className="text-sm text-text-mid">Place<input className={cn(FIELD, 'mt-1')} value={form.location} onChange={set('location')} maxLength={300} /></label>
      <label className="sm:col-span-2 text-sm text-text-mid">Joining link (https://, shown to registered people)<input className={cn(FIELD, 'mt-1')} value={form.url} onChange={set('url')} maxLength={500} /></label>
      <label className="text-sm text-text-mid">Places (empty = unlimited)<input type="number" min={1} className={cn(FIELD, 'mt-1')} value={form.capacity} onChange={set('capacity')} /></label>
      {paidEnabled && <label className="text-sm text-text-mid">Ticket price, $ (empty = free)<input type="number" min={0} step="0.01" className={cn(FIELD, 'mt-1')} value={form.price} onChange={set('price')} /></label>}
      <label className="sm:col-span-2 text-sm text-text-mid">Speakers (comma separated)<input className={cn(FIELD, 'mt-1')} value={form.speakers} onChange={set('speakers')} /></label>
      <label className="sm:col-span-2 text-sm text-text-mid">Description<textarea rows={3} className={cn(FIELD, 'mt-1')} value={form.description} onChange={set('description')} maxLength={8000} /></label>
      <label className="flex items-center gap-2 text-sm text-text-mid"><input type="checkbox" checked={form.waitlist} onChange={(e) => setForm({ ...form, waitlist: e.target.checked })} /> Waitlist when full (free events)</label>
      <label className="flex items-center gap-2 text-sm text-text-mid"><input type="checkbox" checked={form.recording} onChange={(e) => setForm({ ...form, recording: e.target.checked })} /> It may be recorded</label>
      {error && <p role="alert" className="sm:col-span-2 text-sm text-red-300">{error}</p>}
      <div className="sm:col-span-2 flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-40">
          {busy ? (event ? 'Saving…' : 'Creating…') : event ? 'Save changes' : 'Create event'}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className={BTN}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
