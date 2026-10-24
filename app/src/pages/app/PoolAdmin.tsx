import { useState, type ReactNode } from 'react'
import { RotateCcw } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { Badge, Panel, PanelState, Stat } from '@/components/dashboard/primitives'
import { useApi } from '@/hooks/useApi'
import { ApiError, kaluta, type PoolPurchase, type PoolRound } from '@/lib/api'
import { cn } from '@/lib/utils'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'members', label: 'Members' },
  { id: 'purchases', label: 'Purchases' },
  { id: 'rounds', label: 'Rounds' },
  { id: 'assignments', label: 'Assignments' },
  { id: 'ledger', label: 'Unit ledger' },
  { id: 'unassigned', label: 'Unassigned' },
] as const
type TabId = (typeof TABS)[number]['id']

const usd = (value: string | number) =>
  `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : '—')
const BTN =
  'inline-flex items-center gap-1.5 rounded-full border border-[var(--cloud-border)] px-3 py-1.5 text-xs font-semibold text-text-mid hover:border-gold/50 hover:text-text-hi disabled:opacity-40'
const INPUT =
  'rounded-full border border-[var(--cloud-border)] bg-transparent px-3 py-1.5 text-sm text-text-hi focus:border-gold/50 focus:outline-none'

/**
 * Admin: the referral pool.
 *
 * Purchases and the units they carry, every member's weight and share of the
 * round being dealt, the rounds themselves, who was sent to whom, the unit
 * ledger (refunds and reversals included), and the sign-ups the pool could
 * not place. Refunds of units are recorded here; the money itself is
 * refunded on the payment rail and the revenue reversed in the ledger.
 */
export default function PoolAdmin() {
  const [tab, setTab] = useState<TabId>('overview')
  const overview = useApi(() => kaluta.poolAdmin.overview(), [])
  const forbidden = overview.error && /403|access required|forbidden/i.test(overview.error)

  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-[clamp(32px,4.5vw,48px)] font-bold leading-[1.05] tracking-[-0.04em] text-text-hi">
          Referral pool
        </h1>
        <p className="mt-2 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          Units, rounds and assignments. Sign-ups without a valid invitation are dealt in rounds: each member receives
          as many as they held units when the round began, in a random order.
        </p>
      </header>

      {forbidden ? (
        <p role="alert" className="rounded-2xl bg-warning/10 px-5 py-4 text-sm text-text-hi">
          This page is for Kinjy admins. Your account does not have access.
        </p>
      ) : (
        <>
          <nav
            className="mb-5 flex gap-1 overflow-x-auto rounded-full bg-text-hi/[0.06] p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            aria-label="Pool sections"
          >
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => setTab(t.id)}
                aria-current={tab === t.id ? 'page' : undefined}
                className={cn(
                  'shrink-0 rounded-full px-4 py-2 text-sm font-semibold',
                  tab === t.id ? 'bg-gold text-ink' : 'text-text-mid hover:text-text-hi',
                )}
              >
                {t.label}
                {t.id === 'purchases' && (overview.data?.purchases_under_review ?? 0) > 0 && (
                  <span className="ms-1.5 rounded-full bg-ink/20 px-1.5 text-xs">{overview.data?.purchases_under_review}</span>
                )}
                {t.id === 'unassigned' && (overview.data?.unassigned_open ?? 0) > 0 && (
                  <span className="ms-1.5 rounded-full bg-ink/20 px-1.5 text-xs">{overview.data?.unassigned_open}</span>
                )}
              </button>
            ))}
          </nav>

          {tab === 'overview' && <Overview state={overview} />}
          {tab === 'members' && <Members />}
          {tab === 'purchases' && <Purchases onChanged={overview.reload} />}
          {tab === 'rounds' && <Rounds />}
          {tab === 'assignments' && <Assignments />}
          {tab === 'ledger' && <Ledger />}
          {tab === 'unassigned' && <Unassigned onChanged={overview.reload} />}
        </>
      )}
    </AppShell>
  )
}

function Message({ msg }: { msg: { ok: boolean; text: string } | null }) {
  if (!msg) return null
  return (
    <p role={msg.ok ? 'status' : 'alert'} className={cn('mb-3 text-sm', msg.ok ? 'text-emerald-300' : 'text-red-300')}>
      {msg.text}
    </p>
  )
}

function Row({ children }: { children: ReactNode }) {
  return <li className="rounded-2xl border border-[var(--cloud-border)] bg-ink-2/40 p-3.5">{children}</li>
}

function Overview({ state }: { state: ReturnType<typeof useApi<Awaited<ReturnType<typeof kaluta.poolAdmin.overview>>>> }) {
  const d = state.data
  return (
    <PanelState loading={state.loading} error={state.error}>
      {d && (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
          <Panel title="The pool">
            <div className="grid grid-cols-2 gap-6">
              <Stat label="Unit price" value={usd(d.unit_price)} tone="gold" />
              <Stat label="Members" value={d.members.toLocaleString()} />
              <Stat label="Active units" value={d.active_units.toLocaleString()} />
              <Stat label="Units sold / cap" value={`${d.units_sold.toLocaleString()} / ${d.cap.toLocaleString()}`} tone="muted" />
            </div>
          </Panel>
          <Panel title="Round in progress" subtitle={d.rotation_note}>
            {d.current_round ? (
              <div className="grid grid-cols-2 gap-6">
                <Stat label="Round" value={`#${d.current_round.number}`} hint={`since ${when(d.current_round.started_at)}`} />
                <Stat label="Units in the snapshot" value={String(d.current_round.units_snapshot)} />
                <Stat label="Assigned" value={String(d.current_round.assigned)} tone="gold" />
                <Stat label="Quota left" value={String(d.current_round.remaining)} tone="muted" />
              </div>
            ) : (
              <p className="text-sm text-text-low">
                No round open. The next eligible sign-up opens one with every eligible member’s active units.
              </p>
            )}
          </Panel>
          {(d.purchases_under_review > 0 || d.unassigned_open > 0) && (
            <Panel title="Needs a decision" className="lg:col-span-2">
              <ul className="space-y-1 text-sm text-text-mid">
                {d.purchases_under_review > 0 && (
                  <li>{d.purchases_under_review} migrated purchase(s) to review (Purchases → Under review).</li>
                )}
                {d.unassigned_open > 0 && <li>{d.unassigned_open} sign-up(s) the pool could not place (Unassigned).</li>}
              </ul>
            </Panel>
          )}
        </div>
      )}
    </PanelState>
  )
}

function Members() {
  const members = useApi(() => kaluta.poolAdmin.members(), [])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const setStatus = async (seatId: string, status: 'active' | 'suspended') => {
    if (status === 'suspended' && !window.confirm('Suspend this member? Their unused quota in this round goes at once.')) return
    setBusy(seatId)
    try {
      await kaluta.poolAdmin.setSeatStatus(seatId, status)
      setMsg({ ok: true, text: status === 'suspended' ? 'Suspended.' : 'Restored. They join the next round.' })
      members.reload()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'That did not work' })
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel title="Members" subtitle={`${members.data?.total ?? 0} members with a place in the pool.`}>
      <Message msg={msg} />
      <PanelState loading={members.loading} error={members.error} empty={members.data?.items.length === 0}>
        <ul className="space-y-3">
          {members.data?.items.map((m) => (
            <Row key={m.seat_id}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="font-semibold text-text-hi">
                    {m.display_name ?? 'Member'} <span className="caption">@{m.handle ?? m.member_id}</span>
                  </p>
                  <p className="caption mt-1">
                    {m.total_active_units} active unit(s) · this round {m.round_assigned}/{m.round_quota_total}
                    {m.units_awaiting_next_round > 0 && ` · ${m.units_awaiting_next_round} next round`}
                    {m.units_under_review > 0 && ` · ${m.units_under_review} under review`} · {m.referrals_received}{' '}
                    received
                    {m.round_excluded_reason && ` · excluded: ${m.round_excluded_reason}`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone={m.status === 'active' ? 'good' : 'warn'}>{m.status}</Badge>
                  {m.status === 'active' ? (
                    <button type="button" className={BTN} disabled={busy === m.seat_id} onClick={() => setStatus(m.seat_id, 'suspended')}>
                      Suspend
                    </button>
                  ) : (
                    <button type="button" className={BTN} disabled={busy === m.seat_id} onClick={() => setStatus(m.seat_id, 'active')}>
                      Restore
                    </button>
                  )}
                </div>
              </div>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

const PURCHASE_FILTERS = [
  { id: '', label: 'All' },
  { id: 'review', label: 'Under review' },
  { id: 'credited', label: 'Credited' },
  { id: 'partially_reversed', label: 'Partly reversed' },
  { id: 'reversed', label: 'Reversed' },
]

function Purchases({ onChanged }: { onChanged: () => void }) {
  const [filter, setFilter] = useState('')
  const purchases = useApi(() => kaluta.poolAdmin.purchases(filter || undefined), [filter])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const done = (text: string) => {
    setMsg({ ok: true, text })
    purchases.reload()
    onChanged()
  }

  return (
    <Panel title="Purchases" subtitle="Each confirmed payment and the units it carries. Units bought during a round join the next one.">
      <div className="mb-4 flex flex-wrap gap-2">
        {PURCHASE_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setFilter(f.id)}
            className={cn(
              'rounded-full px-3.5 py-1.5 text-xs font-semibold',
              filter === f.id ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid hover:bg-text-hi/[0.1]',
            )}
          >
            {f.label}
          </button>
        ))}
      </div>
      <Message msg={msg} />
      <PanelState loading={purchases.loading} error={purchases.error} empty={purchases.data?.items.length === 0}>
        <ul className="space-y-3">
          {purchases.data?.items.map((p) => (
            <PurchaseRow key={p.id} p={p} onDone={done} onError={(text) => setMsg({ ok: false, text })} />
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

function PurchaseRow({ p, onDone, onError }: { p: PoolPurchase; onDone: (t: string) => void; onError: (t: string) => void }) {
  const [open, setOpen] = useState<'reverse' | 'review' | null>(null)
  const [units, setUnits] = useState(1)
  const [kind, setKind] = useState<'refund' | 'reversal'>('refund')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<unknown>, text: string) => {
    setBusy(true)
    try {
      await fn()
      setOpen(null)
      setReason('')
      onDone(text)
    } catch (err) {
      onError(err instanceof ApiError ? err.message : 'That did not work')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Row>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="font-semibold text-text-hi">
            {p.units} unit(s) · {usd(p.amount)}{' '}
            <span className="caption">
              {p.active_units} active{p.units_reversed > 0 && `, ${p.units_reversed} reversed`}
            </span>
          </p>
          <p className="caption mt-1 break-all">
            {p.member_id} · payment {p.payment_ref} · from round {p.active_from_round} · {when(p.created_at)}
            {p.source === 'migration' && ' · migrated'}
          </p>
          {p.review_note && <p className="mt-1 text-xs text-amber-300">{p.review_note}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Badge tone={p.status === 'credited' ? 'good' : p.status === 'review' ? 'warn' : 'neutral'}>{p.status}</Badge>
          {p.active_units > 0 && (
            <button type="button" className={BTN} onClick={() => setOpen(open === 'reverse' ? null : 'reverse')}>
              Refund / reverse
            </button>
          )}
          {p.status === 'review' && (
            <button type="button" className={BTN} onClick={() => setOpen(open === 'review' ? null : 'review')}>
              Review
            </button>
          )}
        </div>
      </div>
      {open === 'reverse' && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            type="number"
            min={1}
            max={p.active_units}
            value={units}
            onChange={(e) => setUnits(Math.max(1, Math.min(p.active_units, Math.floor(e.currentTarget.valueAsNumber || 1))))}
            aria-label="Units"
            className={cn(INPUT, 'w-20 text-center')}
          />
          <select value={kind} onChange={(e) => setKind(e.target.value as 'refund' | 'reversal')} className={INPUT} aria-label="Kind">
            <option value="refund">Refund</option>
            <option value="reversal">Payment reversal</option>
          </select>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (kept in the ledger)"
            maxLength={300} className={cn(INPUT, 'min-w-0 flex-1')} />
          <button
            type="button"
            disabled={busy || reason.trim().length < 3}
            className="rounded-full bg-red-500/80 px-3.5 py-1.5 text-xs font-bold text-white disabled:opacity-40"
            onClick={() =>
              run(() => kaluta.poolAdmin.reverse(p.id, units, kind, reason.trim()), `${units} unit(s) taken off. Unused quota this round goes with them.`)
            }
          >
            Take off {units} unit(s)
          </button>
        </div>
      )}
      {open === 'review' && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What you checked"
            maxLength={300} className={cn(INPUT, 'min-w-0 flex-1')} />
          <button type="button" disabled={busy || reason.trim().length < 3}
            className="rounded-full bg-gold px-3.5 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
            onClick={() => run(() => kaluta.poolAdmin.review(p.id, 'approve', reason.trim()), 'Approved: the units join the next round.')}>
            Approve
          </button>
          <button type="button" disabled={busy || reason.trim().length < 3} className={BTN}
            onClick={() => run(() => kaluta.poolAdmin.review(p.id, 'reject', reason.trim()), 'Rejected: the units do not count.')}>
            Reject
          </button>
        </div>
      )}
    </Row>
  )
}

function Rounds() {
  const rounds = useApi(() => kaluta.poolAdmin.rounds(), [])
  const [openId, setOpenId] = useState<string | null>(null)
  return (
    <Panel title="Rounds" subtitle="Newest first. A round closes when every quota in its snapshot is used.">
      <PanelState loading={rounds.loading} error={rounds.error} empty={rounds.data?.items.length === 0}
        emptyLabel="No round yet: the first eligible sign-up opens one.">
        <ul className="space-y-3">
          {rounds.data?.items.map((r) => (
            <Row key={r.id}>
              <RoundHead r={r} open={openId === r.id} onToggle={() => setOpenId(openId === r.id ? null : r.id)} />
              {openId === r.id && <Quotas roundId={r.id} />}
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

function RoundHead({ r, open, onToggle }: { r: PoolRound; open: boolean; onToggle: () => void }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="font-semibold text-text-hi">
          Round #{r.number} <span className="caption">· {r.members} members · {r.units_snapshot} units</span>
        </p>
        <p className="caption mt-1">
          {r.assigned} assigned · {r.remaining} left · opened {when(r.started_at)}
          {r.closed_at && ` · closed ${when(r.closed_at)} (${r.close_reason})`}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge tone={r.status === 'open' ? 'good' : 'neutral'}>{r.status}</Badge>
        <button type="button" className={BTN} onClick={onToggle} aria-expanded={open}>
          {open ? 'Hide quotas' : 'Quotas'}
        </button>
      </div>
    </div>
  )
}

function Quotas({ roundId }: { roundId: string }) {
  const quotas = useApi(() => kaluta.poolAdmin.quotas(roundId), [roundId])
  return (
    <PanelState loading={quotas.loading} error={quotas.error}>
      <ul className="mt-3 divide-y divide-[var(--cloud-border)] text-sm">
        {quotas.data?.items.map((q) => (
          <li key={q.member_id} className="flex flex-wrap justify-between gap-2 py-2">
            <span className="text-text-hi">@{q.handle ?? q.member_id}</span>
            <span className="mono-data text-text-mid">
              {q.assigned}/{q.quota_total} of {q.units_snapshot} units{q.excluded_reason && ` · ${q.excluded_reason}`}
            </span>
          </li>
        ))}
      </ul>
    </PanelState>
  )
}

function Assignments() {
  const assignments = useApi(() => kaluta.poolAdmin.assignments(), [])
  return (
    <Panel title="Assignments" subtitle="Who was sent to whom, with the receiving member's quota at the moment of the draw.">
      <PanelState loading={assignments.loading} error={assignments.error} empty={assignments.data?.items.length === 0}>
        <ul className="space-y-2">
          {assignments.data?.items.map((a) => (
            <Row key={a.new_member_id}>
              <p className="text-sm text-text-hi">
                @{a.new_member_handle ?? a.new_member_id} → <span className="font-semibold">@{a.sponsor_handle ?? a.sponsor_id}</span>
              </p>
              <p className="caption mt-1">
                {a.round_number ? `round #${a.round_number} · quota ${a.quota_remaining_before}/${a.quota_total} left before · ` : 'before rounds · '}
                {a.eligible_quota_in_draw} in the draw · {when(a.assigned_at)}
              </p>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

const LEDGER_FILTERS = [
  { id: '', label: 'All' },
  { id: 'purchase', label: 'Purchases' },
  { id: 'refund', label: 'Refunds' },
  { id: 'reversal', label: 'Reversals' },
  { id: 'migration', label: 'Migration' },
  { id: 'review', label: 'Reviews' },
]

function Ledger() {
  const [kind, setKind] = useState('')
  const ledger = useApi(() => kaluta.poolAdmin.ledger(kind || undefined), [kind])
  return (
    <Panel title="Unit ledger" subtitle="Every change to anybody's units. Never edited.">
      <div className="mb-4 flex flex-wrap gap-2">
        {LEDGER_FILTERS.map((f) => (
          <button key={f.id} type="button" onClick={() => setKind(f.id)}
            className={cn('rounded-full px-3.5 py-1.5 text-xs font-semibold',
              kind === f.id ? 'bg-gold text-ink' : 'bg-text-hi/[0.06] text-text-mid hover:bg-text-hi/[0.1]')}>
            {f.label}
          </button>
        ))}
      </div>
      <PanelState loading={ledger.loading} error={ledger.error} empty={ledger.data?.items.length === 0}>
        <ul className="divide-y divide-[var(--cloud-border)] text-sm">
          {ledger.data?.items.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline justify-between gap-2 py-2.5">
              <span className={cn('mono-data w-12 font-bold', e.delta > 0 ? 'text-emerald-300' : e.delta < 0 ? 'text-red-300' : 'text-text-low')}>
                {e.delta > 0 ? `+${e.delta}` : e.delta}
              </span>
              <span className="min-w-0 flex-1 text-text-mid">
                {e.kind} · {e.reason ?? ''} <span className="caption break-all">({e.member_id})</span>
              </span>
              <span className="caption">{e.actor} · {when(e.created_at)}</span>
            </li>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

function Unassigned({ onChanged }: { onChanged: () => void }) {
  const rows = useApi(() => kaluta.poolAdmin.unassigned(), [])
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const retry = async (id: string) => {
    setBusy(id)
    try {
      const r = await kaluta.poolAdmin.retry(id)
      setMsg({ ok: r.assigned, text: r.assigned ? 'Placed.' : 'Still nobody eligible in the pool.' })
      rows.reload()
      onChanged()
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : 'That did not work' })
    } finally {
      setBusy(null)
    }
  }
  return (
    <Panel title="Unassigned sign-ups"
      subtitle="Sign-ups with no invitation that the pool could not place, and why. Until placed they have no sponsor, as before.">
      <Message msg={msg} />
      <PanelState loading={rows.loading} error={rows.error} empty={rows.data?.items.length === 0} emptyLabel="Nothing waiting.">
        <ul className="space-y-2">
          {rows.data?.items.map((r) => (
            <Row key={r.new_member_id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-all text-sm text-text-hi">{r.new_member_id}</p>
                  <p className="caption mt-1">
                    {r.reason} · {r.attempts} attempt(s) · {when(r.created_at)}
                  </p>
                </div>
                <button type="button" className={BTN} disabled={busy === r.new_member_id} onClick={() => retry(r.new_member_id)}>
                  <RotateCcw size={12} aria-hidden="true" /> Retry
                </button>
              </div>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}
