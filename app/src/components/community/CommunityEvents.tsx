import { useState, type FormEvent } from 'react'
import { CalendarDays, MapPin, Users, Video } from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type CommunityEvent, type CommunityQuote } from '@/lib/api'
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

/**
 * A community's events: who can come, how many places are left, the waitlist,
 * free registration or a paid ticket. The joining link shows once registered.
 */
export default function CommunityEvents({
  communityId,
  slug,
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
  const [quote, setQuote] = useState<{ eventId: string; quote: CommunityQuote } | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const act = async (id: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(id)
    setNote(null)
    try {
      await fn()
      if (ok) setNote({ ok: true, text: ok })
      events.reload()
    } catch (err) {
      setNote({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    } finally {
      setBusy(null)
    }
  }

  return (
    <section aria-label="Events" className="space-y-3">
      {steward && (
        <button type="button" className={BTN} onClick={() => setCreating(!creating)} aria-expanded={creating}>
          {creating ? 'Close' : 'New event'}
        </button>
      )}
      {creating && <NewEvent communityId={communityId} paidEnabled={paidEnabled} onCreated={() => { setCreating(false); events.reload() }} />}
      {note && <p role={note.ok ? 'status' : 'alert'} className={note.ok ? 'text-sm text-emerald-300' : 'text-sm text-red-300'}>{note.text}</p>}
      {events.loading && <p className="caption">Loading events…</p>}
      {events.error && <p className="text-sm text-text-mid">{events.error}</p>}
      {events.data?.items.length === 0 && <p className="cloud-card p-4 text-sm text-text-mid">No event planned yet.</p>}
      <ul className="space-y-3">
        {events.data?.items.map((e) => (
          <li key={e.id} className={cn('cloud-card p-4', e.status === 'cancelled' && 'opacity-60')}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="font-semibold text-text-hi">
                  {e.title} {e.status === 'cancelled' && <span className="text-sm text-red-300">· cancelled</span>}
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
            {quote?.eventId === e.id ? (
              <div className="mt-3">
                <QuotePayment quote={quote.quote} returnPath={`/communities/${slug}`} onCancel={() => setQuote(null)}
                  onPaid={() => { setQuote(null); setNote({ ok: true, text: 'Paid: your place is being confirmed.' }); events.reload() }} />
              </div>
            ) : e.status === 'scheduled' && member ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {e.my_status === 'registered' ? (
                  <>
                    <span className="rounded-full bg-emerald-400/15 px-3 py-1.5 text-xs font-bold text-emerald-300">You are registered</span>
                    {!e.ticket_price && (
                      <button type="button" className={BTN} disabled={busy === e.id}
                        onClick={() => void act(e.id, () => kaluta.communityPaid.unregister(e.id), 'Your place is given back.')}>
                        I can no longer come
                      </button>
                    )}
                  </>
                ) : e.my_status === 'waitlisted' ? (
                  <>
                    <span className="rounded-full bg-amber-400/15 px-3 py-1.5 text-xs font-bold text-amber-300">On the waitlist</span>
                    <button type="button" className={BTN} disabled={busy === e.id}
                      onClick={() => void act(e.id, () => kaluta.communityPaid.unregister(e.id))}>Leave the waitlist</button>
                  </>
                ) : e.ticket_price ? (
                  <button type="button" disabled={busy === e.id || e.places_left === 0}
                    className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
                    onClick={() => void act(e.id, async () => setQuote({ eventId: e.id, quote: await kaluta.communityPaid.quote(communityId, { event_id: e.id }) }))}>
                    {e.places_left === 0 ? 'Sold out' : 'Get a ticket'}
                  </button>
                ) : (
                  <button type="button" disabled={busy === e.id}
                    className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
                    onClick={() => void act(e.id, () => kaluta.communityPaid.register(e.id))}>
                    {e.capacity && e.places_left === 0 ? 'Join the waitlist' : 'Register'}
                  </button>
                )}
                {steward && (
                  <button type="button" className={BTN} disabled={busy === e.id}
                    onClick={() => {
                      if (window.confirm('Cancel this event? Everybody registered is told; paid tickets are refunded.'))
                        void act(e.id, () => kaluta.communityPaid.cancelEvent(e.id), 'Event cancelled.')
                    }}>
                    Cancel event
                  </button>
                )}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}

function NewEvent({ communityId, paidEnabled, onCreated }: { communityId: string; paidEnabled: boolean; onCreated: () => void }) {
  const here = Intl.DateTimeFormat().resolvedOptions().timeZone
  const [form, setForm] = useState({
    title: '', description: '', start: '', end: '', mode: 'online', location: '', url: '', capacity: '',
    waitlist: true, price: '', speakers: '', recording: false,
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value })

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    if (!form.title.trim() || !form.start) {
      setError('A title and a start time are needed.')
      return
    }
    setBusy(true)
    try {
      await kaluta.communityPaid.createEvent(communityId, {
        title: form.title.trim(),
        description: form.description.trim(),
        // The picker gives local time; sent as UTC with the zone it is in.
        starts_at: new Date(form.start).toISOString(),
        ends_at: form.end ? new Date(form.end).toISOString() : undefined,
        timezone: here,
        mode: form.mode,
        location: form.location.trim() || undefined,
        online_url: form.url.trim() || undefined,
        capacity: form.capacity ? Number(form.capacity) : undefined,
        waitlist: form.waitlist,
        ticket_price: form.price ? Number(form.price) : undefined,
        speakers: form.speakers.split(',').map((s) => s.trim()).filter(Boolean),
        recording_consent: form.recording,
      })
      onCreated()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the event')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="cloud-card grid gap-3 p-4 sm:grid-cols-2">
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
      <button type="submit" disabled={busy} className="sm:col-span-2 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-40">
        {busy ? 'Creating…' : 'Create event'}
      </button>
    </form>
  )
}
