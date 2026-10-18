import { useEffect, useRef, useState } from 'react'
import { Check, Crosshair, Image as ImageIcon, Music, Search, Trash2, UserMinus, UserPlus, X } from 'lucide-react'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import {
  ApiError,
  kaluta,
  type Memorial,
  type MemorialAdmin,
  type MemorialEvent,
  type PersonBrief,
  type Tribute,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { FAITH_STYLES, formatDate, momentDate } from './format'
import GalleryManage from './GalleryManage'
import { TICKET_REFRESH_MS, useEvery } from './useEvery'

/** The most a device may be off and still count as being at the grave (the backend holds the same number). */
const VERIFIED_ACCURACY_M = 50

const card = 'rounded-card-md border border-text-low/25 bg-text-low/5 p-5'
const field =
  'w-full rounded-card-sm border border-text-low/40 bg-text-low/5 px-3 py-2 text-sm text-text-hi placeholder:text-text-low focus:border-gold/40 focus:outline-none'
const ghost =
  'inline-flex items-center gap-1.5 rounded-full border border-text-low/40 px-3.5 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/40 hover:text-text-hi disabled:opacity-40'
const primary =
  'inline-flex items-center justify-center gap-1.5 rounded-full bg-gold-soft px-4 py-2 text-xs font-bold text-ink disabled:opacity-40'
const chip = (on: boolean) =>
  cn(
    'rounded-full border px-3 py-1.5 text-xs font-semibold',
    on ? 'border-gold/50 bg-gold/10 text-text-hi' : 'border-text-low/40 text-text-mid hover:text-text-hi',
  )

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className={card} aria-label={title}>
      <h3 className="text-sm font-semibold text-text-hi">{title}</h3>
      {hint && <p className="mt-1 text-xs text-text-mid">{hint}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function Problem({ text }: { text: string | null }) {
  return text ? (
    <p role="alert" className="mt-2 text-sm text-danger">
      {text}
    </p>
  ) : null
}

const message = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback)

/** Tributes waiting for the family, oldest first. */
function PendingTributes({ memorial, onChanged }: { memorial: Memorial; onChanged: () => void }) {
  const queue = useApi<{ items: Tribute[] }>(() => kaluta.memorials.pendingTributes(memorial.id), [memorial.id])
  useEvery(queue.reload, TICKET_REFRESH_MS)
  const [error, setError] = useState<string | null>(null)

  const decide = async (id: string, decision: 'approved' | 'rejected') => {
    setError(null)
    try {
      await kaluta.memorials.moderate(memorial.id, id, decision)
      queue.reload()
      onChanged()
    } catch (err) {
      setError(message(err, 'Could not save the decision'))
    }
  }

  const items = queue.data?.items ?? []
  return (
    <Section
      title={`Waiting for approval${items.length ? ` (${items.length})` : ''}`}
      hint="Nothing here is visible to visitors until you approve it."
    >
      {queue.loading && !queue.data && <p className="text-sm text-text-mid">Loading…</p>}
      {queue.data && items.length === 0 && <p className="text-sm text-text-mid">Nothing is waiting.</p>}
      <ul className="space-y-3">
        {items.map((t) => (
          <li key={t.id} className="rounded-card-sm border border-text-low/20 p-3">
            <p className="text-sm text-text-hi">
              <span className="font-semibold">{t.author_name}</span>
              <span className="text-text-mid"> · {t.kind} · {formatDate(t.created_at)}</span>
            </p>
            {t.body && <p className="mt-1 whitespace-pre-line text-sm text-text-mid">{t.body}</p>}
            {t.media_url && <img src={t.media_url} alt={`Photo from ${t.author_name}`} className="mt-2 max-h-48 rounded-card-sm object-cover" />}
            <div className="mt-2 flex gap-2">
              <button type="button" onClick={() => void decide(t.id, 'approved')} className={primary}>
                <Check size={12} aria-hidden="true" /> Approve
              </button>
              <button type="button" onClick={() => void decide(t.id, 'rejected')} className={ghost}>
                <X size={12} aria-hidden="true" /> Decline
              </button>
            </div>
          </li>
        ))}
      </ul>
      <Problem text={error ?? queue.error} />
    </Section>
  )
}

const KIND_LABEL: Record<string, string> = {
  candle: 'lit a candle',
  flower: 'left a flower',
  message: 'wrote in the guest book',
  photo: 'shared a photo',
}

/** What visitors can see now — the place to take something down again. */
function PublishedTributes({
  memorial,
  onChanged,
  onPromoted,
}: {
  memorial: Memorial
  onChanged: () => void
  onPromoted: () => void
}) {
  const [shown, setShown] = useState(20)
  const list = useApi(() => kaluta.memorials.tributes(memorial.id, { limit: shown }), [memorial.id, shown])
  useEvery(list.reload, TICKET_REFRESH_MS)
  const [error, setError] = useState<string | null>(null)

  const [added, setAdded] = useState<Set<string>>(new Set())
  const [note, setNote] = useState<string | null>(null)

  const promote = async (id: string) => {
    setError(null)
    setNote(null)
    try {
      await kaluta.memorials.promoteTribute(memorial.id, id)
      setAdded((prev) => new Set(prev).add(id))
      setNote('Added to the gallery.')
      onPromoted()
    } catch (err) {
      setError(message(err, 'Could not add it to the gallery'))
    }
  }

  const takeDown = async (id: string) => {
    setError(null)
    try {
      await kaluta.memorials.moderate(memorial.id, id, 'rejected')
      list.reload()
      onChanged()
    } catch (err) {
      setError(message(err, 'Could not take it down'))
    }
  }

  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0
  return (
    <Section
      title={`On the page${total ? ` (${total})` : ''}`}
      hint="Everything visitors can see now, newest first. Take down whatever should not be there."
    >
      {list.loading && !list.data && <p className="text-sm text-text-mid">Loading…</p>}
      {list.data && items.length === 0 && <p className="text-sm text-text-mid">No tributes yet.</p>}
      <ul className="space-y-2">
        {items.map((t) => (
          <li key={t.id} className="flex items-start gap-3 rounded-card-sm border border-text-low/20 p-2.5">
            <div className="min-w-0 flex-1 text-sm">
              <p className="text-text-hi">
                <span className="font-semibold">{t.author_name}</span>
                <span className="text-text-mid"> {KIND_LABEL[t.kind] ?? t.kind} · {formatDate(t.created_at)}</span>
              </p>
              {t.body && <p className="mt-0.5 line-clamp-3 whitespace-pre-line text-text-mid">{t.body}</p>}
              {t.media_url && <img src={t.media_url} alt={`Photo from ${t.author_name}`} className="mt-1.5 max-h-28 rounded-card-sm object-cover" loading="lazy" />}
            </div>
            <div className="flex shrink-0 flex-col gap-1.5">
              {t.kind === 'photo' && (
                <button
                  type="button"
                  onClick={() => void promote(t.id)}
                  disabled={added.has(t.id)}
                  aria-label={`Add ${t.author_name}'s photo to the gallery`}
                  className={ghost}
                >
                  {added.has(t.id) ? 'In the gallery' : 'Add to gallery'}
                </button>
              )}
              <button
                type="button"
                onClick={() => void takeDown(t.id)}
                aria-label={`Take down: ${t.author_name} ${KIND_LABEL[t.kind] ?? t.kind}`}
                className={ghost}
              >
                <Trash2 size={12} aria-hidden="true" /> Take down
              </button>
            </div>
          </li>
        ))}
      </ul>
      {items.length < total && (
        <button type="button" onClick={() => setShown((n) => n + 20)} disabled={list.loading} className={cn(ghost, 'mt-3')}>
          Show more ({total - items.length} more)
        </button>
      )}
      {note && <p className="mt-2 text-sm text-success">{note}</p>}
      <Problem text={error ?? list.error} />
    </Section>
  )
}

function Details({ memorial, onChanged }: { memorial: Memorial; onChanged: () => void }) {
  const [name, setName] = useState(memorial.full_name)
  const [birth, setBirth] = useState(memorial.birth_date ?? '')
  const [death, setDeath] = useState(memorial.death_date ?? '')
  const [bio, setBio] = useState(memorial.biography ?? '')
  const [visibility, setVisibility] = useState(memorial.visibility)
  const [moderation, setModeration] = useState(memorial.moderation)
  const [faith, setFaith] = useState(memorial.faith_style || 'none')
  const [source, setSource] = useState<'documented_wish' | 'admin_choice' | ''>(
    (memorial.faith_style_source as 'documented_wish' | 'admin_choice' | null) ?? '',
  )
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const today = new Date().toISOString().slice(0, 10)
  const datesWrong = Boolean(birth && death && death < birth)
  const needsSource = faith !== 'none' && !source

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setSaved(false)
    setError(null)
    try {
      await kaluta.memorials.update(memorial.id, {
        full_name: name.trim(),
        birth_date: birth || null,
        death_date: death || null,
        biography: bio.trim() || null,
        visibility,
        moderation,
        faith_style: faith,
        faith_style_source: faith === 'none' ? null : source || null,
      })
      setSaved(true)
      onChanged()
    } catch (err) {
      setError(message(err, 'Could not save'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="The page" hint="Everything a visitor reads. Changes appear at once.">
      <form onSubmit={save} className="space-y-3">
        <label className="block text-xs text-text-mid">
          Full name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} className={cn(field, 'mt-1')} />
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-xs text-text-mid">
            Date of birth
            <input type="date" value={birth} max={today} onChange={(e) => setBirth(e.target.value)} className={cn(field, 'mt-1')} />
          </label>
          <label className="block text-xs text-text-mid">
            Date of death
            <input type="date" value={death} max={today} onChange={(e) => setDeath(e.target.value)} className={cn(field, 'mt-1')} />
          </label>
        </div>
        {datesWrong && <p className="text-xs text-warning">The date of death is before the date of birth.</p>}
        <label className="block text-xs text-text-mid">
          Life story
          <textarea
            value={bio}
            onChange={(e) => setBio(e.target.value)}
            rows={5}
            maxLength={20000}
            className={cn(field, 'mt-1 resize-y')}
            placeholder="Who they were, what they loved, what they leave behind…"
          />
        </label>

        <fieldset>
          <legend className="text-xs text-text-mid">Who can see it</legend>
          <div className="mt-1 flex flex-wrap gap-2">
            <button type="button" aria-pressed={visibility === 'public'} onClick={() => setVisibility('public')} className={chip(visibility === 'public')}>
              Anyone — the QR code works for everyone
            </button>
            <button type="button" aria-pressed={visibility === 'private'} onClick={() => setVisibility('private')} className={chip(visibility === 'private')}>
              Only its administrators
            </button>
          </div>
        </fieldset>

        <fieldset>
          <legend className="text-xs text-text-mid">Guest book</legend>
          <div className="mt-1 flex flex-wrap gap-2">
            <button type="button" aria-pressed={moderation === 'pending_approval'} onClick={() => setModeration('pending_approval')} className={chip(moderation === 'pending_approval')}>
              Hold every message for approval
            </button>
            <button type="button" aria-pressed={moderation === 'open'} onClick={() => setModeration('open')} className={chip(moderation === 'open')}>
              Members post at once
            </button>
          </div>
          <p className="mt-1 text-xs text-text-mid">Messages from visitors without an account are always held for you.</p>
        </fieldset>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-xs text-text-mid">
            Faith style
            <select value={faith} onChange={(e) => setFaith(e.target.value)} className={cn(field, 'mt-1')}>
              {FAITH_STYLES.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.label}
                </option>
              ))}
            </select>
          </label>
          {faith !== 'none' && (
            <label className="block text-xs text-text-mid">
              Chosen because
              <select value={source} onChange={(e) => setSource(e.target.value as typeof source)} className={cn(field, 'mt-1')}>
                <option value="">Choose…</option>
                <option value="documented_wish">It was their documented wish</option>
                <option value="admin_choice">The family chose it</option>
              </select>
            </label>
          )}
        </div>
        {needsSource && <p className="text-xs text-warning">Say why: a faith style is never guessed.</p>}

        <div className="flex items-center gap-3">
          <button type="submit" disabled={busy || name.trim().length < 2 || datesWrong || needsSource} className={primary}>
            Save
          </button>
          {saved && <span className="text-xs text-success">Saved.</span>}
        </div>
        <Problem text={error} />
      </form>
    </Section>
  )
}

/** Portrait, cover and a voice recording, each the family's own upload. */
function Media({ memorial, onChanged }: { memorial: Memorial; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inputs = { photo: useRef<HTMLInputElement>(null), cover: useRef<HTMLInputElement>(null), audio: useRef<HTMLInputElement>(null) }

  const put = async (slot: 'photo' | 'cover' | 'audio', file: File | null) => {
    setBusy(slot)
    setError(null)
    try {
      const uploaded = file ? await kaluta.media.upload(file) : null
      const id = uploaded ? uploaded.id : null
      await kaluta.memorials.update(
        memorial.id,
        slot === 'photo' ? { photo_media_id: id } : slot === 'cover' ? { cover_media_id: id } : { audio_media_id: id },
      )
      onChanged()
    } catch (err) {
      setError(message(err, 'Could not save the file'))
    } finally {
      setBusy(null)
      const input = inputs[slot].current
      if (input) input.value = ''
    }
  }

  const autoplay = async (value: boolean) => {
    setError(null)
    try {
      await kaluta.memorials.update(memorial.id, { audio_autoplay: value })
      onChanged()
    } catch (err) {
      setError(message(err, 'Could not save'))
    }
  }

  const row = (slot: 'photo' | 'cover' | 'audio', label: string, present: boolean, accept: string, Icon: typeof ImageIcon) => (
    <div className="flex flex-wrap items-center gap-2">
      <Icon size={14} className="text-gold" aria-hidden="true" />
      <span className="min-w-[7rem] text-sm text-text-hi">{label}</span>
      <span className="text-xs text-text-mid">{present ? 'Added' : 'None yet'}</span>
      <button type="button" onClick={() => inputs[slot].current?.click()} disabled={busy !== null} className={cn(ghost, 'ms-auto')}>
        {busy === slot ? 'Uploading…' : present ? 'Replace' : 'Add'}
      </button>
      {present && (
        <button type="button" onClick={() => void put(slot, null)} disabled={busy !== null} className={ghost}>
          Remove
        </button>
      )}
      <input
        ref={inputs[slot]}
        type="file"
        accept={accept}
        className="hidden"
        aria-label={`${label} file`}
        onChange={(e) => void put(slot, e.target.files?.[0] ?? null)}
      />
    </div>
  )

  return (
    <Section title="Portrait, cover and voice" hint="Only files you upload yourself can be used.">
      <div className="space-y-3">
        {row('photo', 'Portrait', Boolean(memorial.photo_url), 'image/jpeg,image/png,image/webp', ImageIcon)}
        {row('cover', 'Cover image', Boolean(memorial.cover_url), 'image/jpeg,image/png,image/webp', ImageIcon)}
        {row('audio', 'Voice or music', Boolean(memorial.audio.url), 'audio/mpeg,audio/ogg,audio/wav', Music)}
        {memorial.audio.url && (
          <label className="flex items-center gap-2 text-xs text-text-mid">
            <input type="checkbox" checked={memorial.audio.autoplay} onChange={(e) => void autoplay(e.target.checked)} />
            Start playing when the page opens (browsers may still wait for a tap)
          </label>
        )}
      </div>
      <Problem text={error} />
    </Section>
  )
}

function Timeline({ memorial }: { memorial: Memorial }) {
  const events = useApi<{ items: MemorialEvent[] }>(() => kaluta.memorials.events(memorial.id), [memorial.id])
  const [year, setYear] = useState('')
  const [month, setMonth] = useState('')
  const [day, setDay] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [error, setError] = useState<string | null>(null)

  const add = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      await kaluta.memorials.addEvent(memorial.id, {
        year: Number(year),
        ...(month ? { month: Number(month) } : {}),
        ...(month && day ? { day: Number(day) } : {}),
        title: title.trim(),
        ...(body.trim() ? { body: body.trim() } : {}),
      })
      setYear('')
      setMonth('')
      setDay('')
      setTitle('')
      setBody('')
      events.reload()
    } catch (err) {
      setError(message(err, 'Could not add the moment'))
    }
  }

  const remove = async (id: string) => {
    setError(null)
    try {
      await kaluta.memorials.deleteEvent(memorial.id, id)
      events.reload()
    } catch (err) {
      setError(message(err, 'Could not remove it'))
    }
  }

  return (
    <Section title="Timeline" hint="The moments of a life. A year is enough when that is all anyone remembers.">
      <ul className="mb-3 space-y-1.5">
        {(events.data?.items ?? []).map((m) => (
          <li key={m.id} className="flex items-center gap-2 text-sm">
            <span className="mono-data w-36 shrink-0 text-xs text-text-mid">{momentDate(m.year, m.month, m.day)}</span>
            <span className="min-w-0 flex-1 truncate text-text-hi">{m.title}</span>
            <button type="button" onClick={() => void remove(m.id)} aria-label={`Remove ${m.title}`} className="rounded-full p-1.5 text-text-mid hover:text-danger">
              <Trash2 size={13} />
            </button>
          </li>
        ))}
      </ul>
      <form onSubmit={add} className="space-y-2">
        <div className="grid grid-cols-3 gap-2">
          <input value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, '').slice(0, 4))} placeholder="Year" aria-label="Year" inputMode="numeric" className={field} />
          <select value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Month, optional" className={field}>
            <option value="">Month</option>
            {Array.from({ length: 12 }, (_, i) => (
              <option key={i + 1} value={i + 1}>
                {new Date(Date.UTC(2000, i, 1)).toLocaleDateString('en-GB', { month: 'long', timeZone: 'UTC' })}
              </option>
            ))}
          </select>
          <input value={day} onChange={(e) => setDay(e.target.value.replace(/\D/g, '').slice(0, 2))} placeholder="Day" aria-label="Day, optional" inputMode="numeric" disabled={!month} className={field} />
        </div>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="What happened — Married Juma" aria-label="Moment title" className={field} />
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={2} maxLength={4000} placeholder="A few words — optional" aria-label="Moment details" className={cn(field, 'resize-none')} />
        <button type="submit" disabled={year.length !== 4 || !title.trim()} className={primary}>
          Add to the timeline
        </button>
      </form>
      <Problem text={error} />
    </Section>
  )
}

function Location({ memorial, onChanged }: { memorial: Memorial; onChanged: () => void }) {
  const [lat, setLat] = useState(memorial.grave.lat?.toString() ?? '')
  const [lng, setLng] = useState(memorial.grave.lng?.toString() ?? '')
  const [label, setLabel] = useState(memorial.grave.label ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async (latitude: number, longitude: number, onSite: boolean, accuracy?: number) => {
    setBusy(true)
    setError(null)
    try {
      await kaluta.memorials.setLocation(memorial.id, {
        lat: latitude,
        lng: longitude,
        label: label.trim() || undefined,
        captured_on_site: onSite,
        ...(onSite && accuracy !== undefined ? { accuracy_m: accuracy } : {}),
      })
      setLat(latitude.toString())
      setLng(longitude.toString())
      onChanged()
    } catch (err) {
      setError(message(err, 'Could not save the location'))
    } finally {
      setBusy(false)
    }
  }

  const here = () => {
    if (!navigator.geolocation) {
      setError('This browser cannot read its location.')
      return
    }
    setBusy(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        // A desktop browser answers from its network address, kilometres off, and
        // would still be "at the grave". Only a fix this close is called one.
        const accuracy = pos.coords.accuracy
        if (!Number.isFinite(accuracy) || accuracy > VERIFIED_ACCURACY_M) {
          setBusy(false)
          setError(
            `Your device could only place you within about ${Number.isFinite(accuracy) ? Math.round(accuracy) : 'an unknown number of'} m — too rough to confirm a grave. ` +
              'Stand at the grave with a clear sky, wait a few seconds and try again. Or type the coordinates: they are shown as not yet confirmed.',
          )
          return
        }
        void save(pos.coords.latitude, pos.coords.longitude, true, accuracy)
      },
      () => {
        setBusy(false)
        setError('The location could not be read. Allow location access and try again at the grave.')
      },
      { enableHighAccuracy: true, timeout: 20000 },
    )
  }

  const clear = async () => {
    setError(null)
    try {
      await kaluta.memorials.clearLocation(memorial.id)
      setLat('')
      setLng('')
      setLabel('')
      onChanged()
    } catch (err) {
      setError(message(err, 'Could not clear it'))
    }
  }

  const manualValid =
    lat.trim() !== '' && lng.trim() !== '' && Math.abs(Number(lat)) <= 90 && Math.abs(Number(lng)) <= 180 &&
    !Number.isNaN(Number(lat)) && !Number.isNaN(Number(lng))

  return (
    <Section
      title="Resting place"
      hint={`Captured at the grave by a device sure of its position to within ${VERIFIED_ACCURACY_M} m, it is shown as confirmed. Typed in, or captured too roughly, it is shown as not yet confirmed — never presented as more than it is.`}
    >
      <input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={255} placeholder="Cemetery, row, plot — optional" aria-label="Place description" className={field} />
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" onClick={here} disabled={busy} className={primary}>
          <Crosshair size={12} aria-hidden="true" /> I am at the grave — use my location
        </button>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <input value={lat} onChange={(e) => setLat(e.target.value)} placeholder="Latitude" aria-label="Latitude" inputMode="decimal" className={field} />
        <input value={lng} onChange={(e) => setLng(e.target.value)} placeholder="Longitude" aria-label="Longitude" inputMode="decimal" className={field} />
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <button type="button" onClick={() => void save(Number(lat), Number(lng), false)} disabled={busy || !manualValid} className={ghost}>
          Save typed coordinates
        </button>
        {memorial.grave.lat !== null && (
          <button type="button" onClick={() => void clear()} disabled={busy} className={ghost}>
            Clear
          </button>
        )}
      </div>
      <Problem text={error} />
    </Section>
  )
}

function Admins({ memorial }: { memorial: Memorial }) {
  const { user } = useAuth()
  const admins = useApi<{ max: number; items: MemorialAdmin[] }>(() => kaluta.memorials.admins(memorial.id), [memorial.id])
  const [query, setQuery] = useState('')
  // Results carry the query they answer, so the list under "ab" is never shown,
  // or clicked, while "abe" is still being looked up.
  const [found, setFound] = useState<{ q: string; items: Array<PersonBrief & { user_id: string }> } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef(0)
  const q = query.trim()
  const results = found && found.q === q ? found.items : null

  useEffect(() => {
    if (q.length < 2) return
    const ticket = ++latest.current
    const timer = window.setTimeout(() => {
      kaluta.people
        .search(q, 6)
        .then((r) => ticket === latest.current && setFound({ q, items: r.items }))
        .catch(() => ticket === latest.current && setFound({ q, items: [] }))
    }, 250)
    return () => window.clearTimeout(timer)
  }, [q])

  const items = admins.data?.items ?? []
  const max = admins.data?.max ?? 3
  const ids = new Set(items.map((a) => a.user_id))
  // Anyone may step down; only those ahead of someone in the succession may remove them.
  const myRank = memorial.admin_rank ?? (items.find((a) => a.user_id === user?.id)?.succession_order ?? Infinity)

  const add = async (userId: string) => {
    setError(null)
    try {
      await kaluta.memorials.addAdmin(memorial.id, userId)
      setQuery('')
      admins.reload()
    } catch (err) {
      setError(message(err, 'Could not add them'))
    }
  }

  const remove = async (userId: string) => {
    setError(null)
    try {
      await kaluta.memorials.removeAdmin(memorial.id, userId)
      admins.reload()
    } catch (err) {
      setError(message(err, 'Could not remove them'))
    }
  }

  return (
    <Section title={`Administrators (${items.length}/${max})`} hint="They approve tributes, edit the page and receive the anniversary reminders. If the first can no longer look after it, the next one in line takes over. You can step down, or remove those who come after you.">
      <ol className="space-y-2">
        {items.map((a) => (
          <li key={a.user_id} className="flex items-center gap-3">
            <span className="mono-data w-5 text-xs text-text-mid">{a.succession_order}</span>
            <MemberAvatar handle={a.handle} displayName={a.display_name} avatarUrl={a.avatar_url} size={30} />
            <span className="min-w-0 flex-1 truncate text-sm text-text-hi">
              {a.display_name ?? 'A member'}
              {a.user_id === user?.id && <span className="text-text-mid"> (you)</span>}
            </span>
            {items.length > 1 && (a.user_id === user?.id || a.succession_order > myRank) && (
              <button
                type="button"
                onClick={() => void remove(a.user_id)}
                className={ghost}
                aria-label={a.user_id === user?.id ? 'Step down as administrator' : `Remove ${a.display_name ?? 'administrator'}`}
              >
                <UserMinus size={12} aria-hidden="true" /> {a.user_id === user?.id ? 'Step down' : 'Remove'}
              </button>
            )}
          </li>
        ))}
      </ol>
      {items.length < max && (
        <div className="mt-3">
          <label className="flex items-center gap-2 rounded-full border border-text-low/40 bg-text-low/5 px-3 py-2">
            <Search size={14} className="shrink-0 text-text-low" aria-hidden="true" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Add an administrator — type a name"
              aria-label="Search people to add as administrator"
              className="w-full bg-transparent text-sm text-text-hi placeholder:text-text-low focus:outline-none"
            />
          </label>
          {q.length >= 2 && (
            <ul className="mt-2 space-y-1">
              {results === null && <li className="px-2 text-xs text-text-mid">Looking…</li>}
              {results?.length === 0 && <li className="px-2 text-xs text-text-mid">No member whose name starts with “{q}”.</li>}
              {(results ?? []).map((p) => (
                <li key={p.user_id} className="flex items-center gap-3 rounded-card-sm px-2 py-1.5 hover:bg-text-low/10">
                  <MemberAvatar handle={p.handle} displayName={p.display_name} avatarUrl={p.avatar_url} size={28} />
                  <span className="min-w-0 flex-1 truncate text-sm text-text-hi">{p.display_name}</span>
                  {ids.has(p.user_id) ? (
                    <span className="text-xs text-text-mid">Already</span>
                  ) : (
                    <button type="button" onClick={() => void add(p.user_id)} className={ghost}>
                      <UserPlus size={12} aria-hidden="true" /> Add
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <Problem text={error ?? admins.error} />
    </Section>
  )
}

function Reminders({ memorial }: { memorial: Memorial }) {
  const reminders = useApi(() => kaluta.memorials.reminders(memorial.id), [memorial.id, memorial.death_date])
  const items = reminders.data?.items ?? []
  return (
    <Section title="Anniversary reminders" hint="Sent to each administrator 10 days, 3 days and 6 hours before.">
      {!memorial.death_date ? (
        <p className="text-sm text-text-mid">Add the date of death to receive them.</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-text-mid">None scheduled.</p>
      ) : (
        <ul className="space-y-1 text-sm text-text-hi">
          {items.map((r) => (
            <li key={r.due_at}>
              {new Date(r.due_at).toLocaleString('en-GB', { dateStyle: 'long', timeStyle: 'short' })}
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function DeleteMemorial({ memorial, onDeleted }: { memorial: Memorial; onDeleted: () => void }) {
  const [confirm, setConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const remove = async () => {
    setError(null)
    try {
      await kaluta.memorials.remove(memorial.id)
      onDeleted()
    } catch (err) {
      setError(message(err, 'Could not delete it'))
    }
  }
  return (
    <Section title="Delete the memorial" hint="Only the first administrator can. Its tributes, timeline and reminders go with it.">
      {confirm ? (
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => void remove()} className="rounded-full bg-red-600 px-4 py-2 text-xs font-bold text-white hover:bg-red-700">
            Delete it permanently
          </button>
          <button type="button" onClick={() => setConfirm(false)} className={ghost}>
            Keep it
          </button>
        </div>
      ) : (
        <button type="button" onClick={() => setConfirm(true)} className={ghost}>
          <Trash2 size={12} aria-hidden="true" /> Delete…
        </button>
      )}
      <Problem text={error} />
    </Section>
  )
}

/** Everything only the memorial's administrators can do. */
export default function MemorialManage({
  memorial,
  onChanged,
  onDeleted,
}: {
  memorial: Memorial
  onChanged: () => void
  onDeleted: () => void
}) {
  // A photo promoted from the tributes must show up in the gallery section below it.
  const [galleryVersion, setGalleryVersion] = useState(0)
  return (
    <div className="space-y-4">
      <PendingTributes memorial={memorial} onChanged={onChanged} />
      <PublishedTributes memorial={memorial} onChanged={onChanged} onPromoted={() => setGalleryVersion((n) => n + 1)} />
      <Details key={`${memorial.id}-details`} memorial={memorial} onChanged={onChanged} />
      <Media memorial={memorial} onChanged={onChanged} />
      <GalleryManage memorial={memorial} refresh={galleryVersion} />
      <Timeline memorial={memorial} />
      <Location key={`${memorial.id}-place`} memorial={memorial} onChanged={onChanged} />
      <Admins memorial={memorial} />
      <Reminders memorial={memorial} />
      {memorial.admin_rank === 1 && <DeleteMemorial memorial={memorial} onDeleted={onDeleted} />}
    </div>
  )
}
