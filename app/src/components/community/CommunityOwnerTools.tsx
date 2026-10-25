import { useState, type FormEvent } from 'react'
import { Sheet } from '@/components/community/CommunityDialogs'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type CommunitySpace } from '@/lib/api'
import { cn } from '@/lib/utils'

const FIELD = 'w-full rounded-xl border border-[var(--cloud-border)] bg-transparent px-3 py-2 text-sm text-text-hi focus:border-gold/50 focus:outline-none'
const BTN = 'rounded-full border border-[var(--cloud-border)] px-3.5 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'
const GOLD = 'rounded-full bg-gradient-to-br from-gold-soft to-gold px-4 py-2 text-sm font-bold text-ink disabled:opacity-40'
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '')

type Msg = { ok: boolean; text: string } | null
function Note({ msg }: { msg: Msg }) {
  if (!msg) return null
  return <p role={msg.ok ? 'status' : 'alert'} className={cn('text-sm', msg.ok ? 'text-emerald-300' : 'text-red-300')}>{msg.text}</p>
}

/**
 * The owner's paid access: free, paid or mixed, paid spaces, tiers, codes,
 * members with paid access, earnings and refunds. Prices are the owner's;
 * members pay them plus Kinjy's fee, and the owner's share goes to the owner's
 * Kinjy balance.
 */
export function PaidAccessDialog({
  communityId,
  spaces,
  paidSpaces,
  onClose,
  onChanged,
}: {
  communityId: string
  spaces: CommunitySpace[]
  /** The spaces already for paying members, so the boxes start right. */
  paidSpaces: string[]
  onClose: () => void
  onChanged: () => void
}) {
  const [tab, setTab] = useState<'access' | 'members' | 'earnings'>('access')
  return (
    <Sheet title="Paid access" onClose={onClose}>
      <div className="mb-4 flex gap-1.5" role="tablist">
        {(['access', 'members', 'earnings'] as const).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={cn('rounded-full px-3.5 py-1.5 text-xs font-semibold', tab === t ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid')}>
            {t === 'access' ? 'Access & tiers' : t === 'members' ? 'Paying members' : 'Earnings'}
          </button>
        ))}
      </div>
      {tab === 'access' && <AccessTab communityId={communityId} spaces={spaces} paidSpaces={paidSpaces} onChanged={onChanged} />}
      {tab === 'members' && <MembersTab communityId={communityId} />}
      {tab === 'earnings' && <EarningsTab communityId={communityId} />}
    </Sheet>
  )
}

function AccessTab({
  communityId,
  spaces,
  paidSpaces,
  onChanged,
}: {
  communityId: string
  spaces: CommunitySpace[]
  paidSpaces: string[]
  onChanged: () => void
}) {
  const tiers = useApi(() => kaluta.communityPaid.tiers(communityId), [communityId])
  const coupons = useApi(() => kaluta.communityPaid.coupons(communityId), [communityId])
  const [msg, setMsg] = useState<Msg>(null)
  const [tier, setTier] = useState({ name: '', price: '', period: 'monthly', trial: '', intro: '', benefits: '', description: '' })
  const [coupon, setCoupon] = useState({ code: '', pct: '', max: '' })
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setMsg(null)
    try {
      await fn()
      setMsg({ ok: true, text: ok })
      tiers.reload()
      coupons.reload()
      onChanged()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'That did not go through' })
    }
  }
  const data = tiers.data
  if (data && !data.paid_enabled) {
    return <p className="text-sm text-text-mid">Paid communities are not open on Kinjy yet.</p>
  }
  const pct = Number(data?.markup_pct ?? 0)
  const addTier = (e: FormEvent) => {
    e.preventDefault()
    void run(() => kaluta.communityPaid.createTier(communityId, {
      name: tier.name.trim(), price: Number(tier.price), period: tier.period, description: tier.description.trim(),
      trial_days: tier.trial ? Number(tier.trial) : 0, intro_price: tier.intro ? Number(tier.intro) : undefined,
      benefits: tier.benefits.split('\n').map((b) => b.trim()).filter(Boolean),
    }).then(() => setTier({ name: '', price: '', period: 'monthly', trial: '', intro: '', benefits: '', description: '' })), 'Tier created.')
  }
  const postSpaces = spaces.filter((s) => s.enabled && s.posters !== null)
  return (
    <div className="space-y-6">
      <Note msg={msg} />
      <fieldset>
        <legend className="text-sm font-semibold text-text-hi">Who can come in</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {(['free', 'paid', 'mixed'] as const).map((m) => (
            <button key={m} type="button" onClick={() => void run(() => kaluta.communityPaid.setAccess(communityId, { access_model: m }), 'Saved.')}
              className={cn(BTN, data?.access_model === m && '!border-gold/60 !bg-gold/15 !text-gold-soft')}>
              {m === 'free' ? 'Free' : m === 'paid' ? 'Paid membership' : 'Free + paid spaces'}
            </button>
          ))}
        </div>
        <p className="caption mt-2">Members already in keep their place when a community becomes paid.</p>
        {data?.access_model === 'mixed' && (
          <ul className="mt-3 space-y-1.5">
            {postSpaces.map((s) => (
              <li key={s.id}>
                <label className="flex items-center gap-2 text-sm text-text-mid">
                  <input type="checkbox" defaultChecked={paidSpaces.includes(s.id)}
                    onChange={(e) => void run(() => kaluta.communityPaid.setAccess(communityId, { paid_spaces: { [s.id]: e.target.checked } }),
                      `${s.label} ${e.target.checked ? 'for paying members' : 'free'}.`)} />
                  {s.label} for paying members only
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      <div>
        <p className="text-sm font-semibold text-text-hi">Tiers</p>
        <ul className="mt-2 space-y-2">
          {data?.tiers.map((t) => (
            <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-text-hi/[0.04] px-3 py-2 text-sm">
              <span className={cn('text-text-hi', !t.active && 'line-through opacity-60')}>
                {t.name} · your ${t.owner_price} → members pay ${t.price} {t.period === 'one_time' ? 'once' : t.period === 'annual' ? '/ year' : '/ month'}
                {t.trial_days ? ` · ${t.trial_days}-day trial` : ''}
              </span>
              <button type="button" className={BTN}
                onClick={() => void run(() => kaluta.communityPaid.editTier(communityId, t.id, { active: !t.active }), t.active ? 'Tier hidden.' : 'Tier shown.')}>
                {t.active ? 'Hide' : 'Show'}
              </button>
            </li>
          ))}
        </ul>
        <form onSubmit={addTier} className="mt-3 grid gap-2 sm:grid-cols-2">
          <input className={FIELD} placeholder="Name (e.g. Monthly)" value={tier.name} onChange={(e) => setTier({ ...tier, name: e.target.value })} />
          <input className={FIELD} type="number" min={0.01} step="0.01" placeholder="Your price, $" value={tier.price} onChange={(e) => setTier({ ...tier, price: e.target.value })} />
          <select className={FIELD} value={tier.period} onChange={(e) => setTier({ ...tier, period: e.target.value })}>
            <option value="monthly">Monthly</option><option value="annual">Annual</option><option value="one_time">One time</option>
          </select>
          <input className={FIELD} type="number" min={0} max={60} placeholder="Free trial days (optional)" value={tier.trial} onChange={(e) => setTier({ ...tier, trial: e.target.value })} />
          <input className={FIELD} type="number" min={0.01} step="0.01" placeholder="First period price, $ (optional)" value={tier.intro} onChange={(e) => setTier({ ...tier, intro: e.target.value })} />
          <input className={FIELD} placeholder="Description" value={tier.description} onChange={(e) => setTier({ ...tier, description: e.target.value })} />
          <textarea className={cn(FIELD, 'sm:col-span-2')} rows={2} placeholder="Benefits, one per line" value={tier.benefits} onChange={(e) => setTier({ ...tier, benefits: e.target.value })} />
          <p className="caption sm:col-span-2">
            {tier.price ? `Members will pay $${(Number(tier.price) * (1 + pct / 100)).toFixed(2)} (your $${Number(tier.price).toFixed(2)} + Kinjy ${pct}%).` : `Kinjy adds ${pct}% on top of your price.`}
            {' '}A tier's price never changes once created: a new price is a new tier.
          </p>
          <button type="submit" className={cn(GOLD, 'sm:col-span-2')} disabled={!tier.name.trim() || !tier.price}>Add tier</button>
        </form>
      </div>

      <div>
        <p className="text-sm font-semibold text-text-hi">Codes (on a first payment)</p>
        <ul className="mt-2 space-y-1.5 text-sm">
          {coupons.data?.items.map((cp) => (
            <li key={cp.id} className="flex flex-wrap justify-between gap-2">
              <span className={cn('text-text-hi', !cp.active && 'line-through opacity-60')}>
                {cp.code} · {cp.percent_off}% · used {cp.redemptions}{cp.max_redemptions ? `/${cp.max_redemptions}` : ''}
              </span>
              {cp.active && <button type="button" className={BTN} onClick={() => void run(() => kaluta.communityPaid.disableCoupon(communityId, cp.id), 'Code disabled.')}>Disable</button>}
            </li>
          ))}
        </ul>
        <form className="mt-2 flex flex-wrap gap-2" onSubmit={(e) => {
          e.preventDefault()
          void run(() => kaluta.communityPaid.createCoupon(communityId, { code: coupon.code.trim(), percent_off: Number(coupon.pct),
            max_redemptions: coupon.max ? Number(coupon.max) : undefined }).then(() => setCoupon({ code: '', pct: '', max: '' })), 'Code created.')
        }}>
          <input className={cn(FIELD, 'w-32')} placeholder="CODE" value={coupon.code} onChange={(e) => setCoupon({ ...coupon, code: e.target.value })} />
          <input className={cn(FIELD, 'w-24')} type="number" min={1} max={100} placeholder="% off" value={coupon.pct} onChange={(e) => setCoupon({ ...coupon, pct: e.target.value })} />
          <input className={cn(FIELD, 'w-28')} type="number" min={1} placeholder="Max uses" value={coupon.max} onChange={(e) => setCoupon({ ...coupon, max: e.target.value })} />
          <button type="submit" className={BTN} disabled={!coupon.code.trim() || !coupon.pct}>Add code</button>
        </form>
      </div>
    </div>
  )
}

function MembersTab({ communityId }: { communityId: string }) {
  const rows = useApi(() => kaluta.communityPaid.subscribers(communityId), [communityId])
  const counts = (rows.data?.items ?? []).reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.status]: (acc[r.status] ?? 0) + 1 }), {})
  return (
    <div className="space-y-3">
      <p className="text-sm text-text-mid">
        {Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(' · ') || 'Nobody has paid access yet.'}
      </p>
      <ul className="divide-y divide-[var(--cloud-border)] text-sm">
        {rows.data?.items.map((r) => (
          <li key={r.id} className="flex flex-wrap justify-between gap-2 py-2">
            <span className="break-all text-text-hi">{r.user_id}</span>
            <span className="text-text-mid">{r.status}{r.period_end ? ` · until ${day(r.period_end)}` : ''}{r.cancel_at_period_end ? ' · cancelled' : ''}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function EarningsTab({ communityId }: { communityId: string }) {
  const data = useApi(() => kaluta.communityPaid.earnings(communityId), [communityId])
  const [msg, setMsg] = useState<Msg>(null)
  const refund = async (paymentId: string, max: string) => {
    const amount = window.prompt(`Amount to refund (up to $${max}):`, max)
    if (!amount) return
    const reason = window.prompt('Reason (shown to the member):')
    if (!reason || reason.trim().length < 3) return
    try {
      await kaluta.communityPaid.refund(communityId, paymentId, Number(amount), reason.trim(), crypto.randomUUID())
      setMsg({ ok: true, text: `$${Number(amount).toFixed(2)} refunded to the member's Kinjy balance.` })
      data.reload()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'The refund did not go through' })
    }
  }
  const e = data.data
  return (
    <div className="space-y-4">
      <Note msg={msg} />
      {e && (
        <>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            {([
              ['Members paid', e.members_paid], ['Kinjy fees', e.kinjy_fees], ['Your share (gross)', e.owner_gross],
              ['Refunded to members', e.refunded_to_members], ['Your share refunded', e.owner_refunded], ['Your net', e.owner_net],
            ] as const).map(([label, value]) => (
              <div key={label} className="rounded-xl bg-text-hi/[0.04] p-3">
                <dt className="caption">{label}</dt>
                <dd className="text-lg font-bold text-text-hi">${value}</dd>
              </div>
            ))}
          </dl>
          <p className="caption">{e.note}</p>
          <ul className="divide-y divide-[var(--cloud-border)] text-sm">
            {e.payments.map((p) => {
              const left = (Number(p.total) - Number(p.refunded)).toFixed(2)
              return (
                <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span className="text-text-mid">{day(p.created_at)} · {p.kind} · ${p.total} (your ${p.owner_price}){Number(p.refunded) > 0 ? ` · refunded $${p.refunded}` : ''}</span>
                  {Number(left) > 0 && <button type="button" className={BTN} onClick={() => void refund(p.id, left)}>Refund</button>}
                </li>
              )
            })}
          </ul>
        </>
      )}
    </div>
  )
}

/** Counts over a window, each with its definition. Money for the owner only. */
export function AnalyticsDialog({ communityId, onClose }: { communityId: string; onClose: () => void }) {
  const [days, setDays] = useState(30)
  const data = useApi(() => kaluta.communityPaid.analytics(communityId, days), [communityId, days])
  const a = data.data
  const rows: Array<[string, string, number | string | null | undefined]> = a
    ? [
        ['new_members', 'New members', a.new_members], ['via_invitations', 'Through invitations', a.via_invitations],
        ['activated', 'New members who took part', a.activated], ['active_contributors', 'Active contributors', a.active_contributors],
        ['posts', 'Posts', a.posts], ['comments', 'Comments', a.comments], ['unanswered', 'Unanswered discussions', a.unanswered],
        ['pending_posts', 'Posts waiting for approval', a.pending_posts], ['moderation_actions', 'Moderation actions', a.moderation_actions],
        ['event_registrations', 'Event registrations', a.event_registrations], ['upcoming_events', 'Upcoming events', a.upcoming_events],
      ]
    : []
  return (
    <Sheet title="Analytics" onClose={onClose}>
      <div className="mb-4 flex gap-1.5">
        {[7, 30, 90].map((d) => (
          <button key={d} type="button" onClick={() => setDays(d)}
            className={cn('rounded-full px-3.5 py-1.5 text-xs font-semibold', days === d ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid')}>
            {d} days
          </button>
        ))}
      </div>
      {data.error && <p className="text-sm text-red-300">{data.error}</p>}
      {a && (
        <dl className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl bg-text-hi/[0.04] p-3 sm:col-span-2">
            <dt className="caption">Members now</dt><dd className="text-lg font-bold text-text-hi">{a.members_total}</dd>
          </div>
          {rows.map(([key, label, value]) => (
            <div key={key} className="rounded-xl bg-text-hi/[0.04] p-3" title={a.definitions[key]}>
              <dt className="caption">{label}</dt>
              <dd className="text-lg font-bold text-text-hi">{value ?? '—'}</dd>
              <p className="caption mt-0.5">{a.definitions[key]}</p>
            </div>
          ))}
          {a.money && (
            <div className="rounded-xl bg-gold/10 p-3 sm:col-span-2">
              <dt className="caption">Money (you only)</dt>
              <dd className="mt-1 text-sm text-text-hi">
                {a.money.sales} sales · your share ${a.money.owner_gross} · {a.money.renewals} renewals · {a.money.cancellations} cancellations · refunds ${a.money.refunds}
              </dd>
            </div>
          )}
        </dl>
      )}
      <p className="caption mt-4">Counts only: nothing here names a member or shows their payments.</p>
    </Sheet>
  )
}
