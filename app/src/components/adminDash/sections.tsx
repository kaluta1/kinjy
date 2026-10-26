import { useState } from 'react'
import { Link } from 'react-router'
import { ExternalLink, RefreshCw, Search } from 'lucide-react'
import { Badge, Panel, PanelState, Stat } from '@/components/dashboard/primitives'
import { useApi } from '@/hooks/useApi'
import {
  ApiError,
  kaluta,
  type AdminJournal,
  type AdminMemberBrief,
  type AdminPaymentBrief,
  type Order,
  type SupportCase,
} from '@/lib/api'
import { FEATURES } from '@/lib/features'
import { cn } from '@/lib/utils'
import PayoutWallets from '@/pages/app/PayoutWallets'
import { BTN, BTN_GOLD, INPUT, usd, when, type Msg } from './format'
import { Chips, ConfirmButton, Kpi, Message, Row } from './ui'
import { useMembers, usePayments } from './lookups'
import { Member, ProviderRef } from './people'

const errorText = (err: unknown, fallback = 'That did not work') => (err instanceof ApiError ? err.message : fallback)
const thisMonth = () => new Date().toISOString().slice(0, 7)
const lastMonths = (n: number) =>
  Array.from({ length: n }, (_, i) => {
    const d = new Date()
    d.setUTCDate(1)
    d.setUTCMonth(d.getUTCMonth() - i)
    return d.toISOString().slice(0, 7)
  })

/* ------------------------------------------------------------------ overview */

export function Overview({ go }: { go: (section: string) => void }) {
  const users = useApi(() => kaluta.adminConsole.users('', 0), [])
  const reconcile = useApi(() => kaluta.adminConsole.reconcile(), [])
  const kyc = useApi(() => kaluta.adminConsole.kyc(), [])
  const fraud = useApi(() => kaluta.adminConsole.fraud(30), [])
  const moderation = useApi(() => kaluta.trustSafety.overview(), [])
  const pool = useApi(() => kaluta.poolAdmin.overview(), [])
  const wallets = useApi(() => kaluta.payoutAdmin.wallets('pending_admin'), [])
  const withdrawals = useApi(() => kaluta.payoutAdmin.withdrawals('submitted'), [])
  const leaders = useApi(() => kaluta.leaders.standings(), [])
  const services = useApi(() => kaluta.adminConsole.services(), [])

  const v = <T,>(s: { data: T | null; error: string | null }, pick: (d: T) => string | number) =>
    s.error ? 'n/a' : s.data == null ? '…' : pick(s.data)
  const fraudTotal = fraud.data
    ? fraud.data.counts.sponsor_shared_ip + fraud.data.counts.signup_bursts + fraud.data.counts.age_probing
    : 0

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi label="Members" value={v(users, (d) => d.total.toLocaleString())} onClick={() => go('members')} />
        <Kpi
          label="Ledger"
          value={v(reconcile, (d) => (d.drift_count === 0 ? 'Reconciled' : `${d.drift_count} drift`))}
          tone={reconcile.data && reconcile.data.drift_count > 0 ? 'bad' : 'gold'}
          hint={reconcile.data ? `${reconcile.data.checked} wallets checked` : undefined}
          onClick={() => go('ledger')}
        />
        <Kpi
          label="Fraud signals (30 days)"
          value={v(fraud, () => fraudTotal)}
          tone={fraudTotal > 0 ? 'warn' : 'default'}
          onClick={() => go('fraud')}
        />
        <Kpi
          label="Waiting for a moderator"
          value={v(moderation, (d) => d.pending_review.toLocaleString())}
          hint={moderation.data ? `${moderation.data.appeals.overdue} overdue appeal(s)` : undefined}
          tone={moderation.data && moderation.data.appeals.overdue > 0 ? 'warn' : 'default'}
          onClick={() => go('moderation')}
        />
        <Kpi
          label="Verified this year"
          value={v(kyc, (d) => d.counts.verified ?? 0)}
          hint={kyc.data ? `${kyc.data.counts.fee_paid ?? 0} fees paid` : undefined}
          onClick={() => go('kyc')}
        />
        <Kpi
          label="Payout wallets to approve"
          value={v(wallets, (d) => d.items.length)}
          hint={withdrawals.data ? `${withdrawals.data.items.length} withdrawal(s) in flight` : undefined}
          tone={wallets.data && wallets.data.items.length > 0 ? 'warn' : 'default'}
          onClick={() => go('payouts')}
        />
        <Kpi
          label="Referral pool"
          value={v(pool, (d) => `${d.members} / ${d.cap.toLocaleString()}`)}
          hint={pool.data ? `${pool.data.active_units} active units` : undefined}
          onClick={() => go('referral-pool')}
        />
        <Kpi
          label="Kinjy Leaders pool"
          value={v(leaders, (d) => usd(d.pool_amount))}
          hint={leaders.data ? `${leaders.data.qualifying} qualifying · ${leaders.data.period}` : undefined}
          tone="gold"
          onClick={() => go('leaders')}
        />
      </div>
      <Panel title="Services" subtitle="Asked of each service right now, through the gateway.">
        <PanelState loading={services.loading} error={services.error}>
          {services.data && (
            <>
              <p className="mb-3 text-sm text-text-mid">
                {services.data.healthy} of {services.data.total} services answering.
              </p>
              <ServiceList services={services.data.services} />
            </>
          )}
        </PanelState>
      </Panel>
    </div>
  )
}

function ServiceList({ services }: { services: Record<string, { status: string; error?: string }> }) {
  return (
    <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {Object.entries(services)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([name, s]) => (
          <li key={name} className="flex items-center justify-between rounded-xl bg-text-hi/[0.04] px-3 py-2 text-sm">
            <span className="text-text-hi">{name}</span>
            <Badge tone={s.status === 'ok' ? 'good' : 'bad'}>{s.status === 'ok' ? 'ok' : s.error ?? s.status}</Badge>
          </li>
        ))}
    </ul>
  )
}

/* ------------------------------------------------------------------ members */

export function Members() {
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const users = useApi(() => kaluta.adminConsole.users(search, offset), [search, offset])
  return (
    <Panel title="Members" subtitle={users.data ? `${users.data.total.toLocaleString()} accounts` : undefined}>
      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          setOffset(0)
          setSearch(query.trim())
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Name, @handle or e-mail"
          aria-label="Search members"
          className={cn(INPUT, 'min-w-0 flex-1')}
        />
        <button type="submit" className={BTN_GOLD}>
          <Search size={12} aria-hidden="true" /> Search
        </button>
      </form>
      <PanelState loading={users.loading} error={users.error} empty={users.data?.items.length === 0}>
        <ul className="space-y-2">
          {users.data?.items.map((u) => (
            <Row key={u.id}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="font-semibold text-text-hi">
                    {u.display_name}{' '}
                    <Link to={`/u/${u.handle}`} className="caption hover:underline">
                      @{u.handle}
                    </Link>
                  </p>
                  <p className="caption mt-1 break-all">
                    {u.email} · {u.country ?? '—'} · joined {when(u.created_at)}
                    {u.invited_by ? ` · sponsor ${u.invited_by}` : ' · no sponsor'}
                  </p>
                </div>
                <div className="flex shrink-0 flex-wrap gap-1.5">
                  {u.role !== 'member' && <Badge tone="warn">{u.role}</Badge>}
                  <Badge tone={u.kyc_verified ? 'good' : 'neutral'}>{u.kyc_verified ? 'Verified' : 'Unverified'}</Badge>
                  <Badge tone={u.status === 'active' ? 'good' : 'bad'}>{u.status}</Badge>
                </div>
              </div>
            </Row>
          ))}
        </ul>
        <Pager offset={offset} size={50} total={users.data?.total ?? 0} onChange={setOffset} />
      </PanelState>
    </Panel>
  )
}

function Pager({ offset, size, total, onChange }: { offset: number; size: number; total: number; onChange: (n: number) => void }) {
  if (total <= size) return null
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-text-mid">
      <button type="button" className={BTN} disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - size))}>
        Previous
      </button>
      <span className="caption">
        {offset + 1}–{Math.min(offset + size, total)} of {total.toLocaleString()}
      </span>
      <button type="button" className={BTN} disabled={offset + size >= total} onClick={() => onChange(offset + size)}>
        Next
      </button>
    </div>
  )
}

/* ------------------------------------------------------------------ ledger */

const JOURNAL_KINDS = [
  { id: '', label: 'All' },
  { id: 'referral_pool_entry', label: 'Pool' },
  { id: 'kyc_fee', label: 'KYC fees' },
  { id: 'ad_purchase', label: 'Ads' },
  { id: 'marketplace_order', label: 'Marketplace' },
  { id: 'payout', label: 'Payouts' },
  { id: 'refund', label: 'Refunds' },
  { id: 'reversal', label: 'Reversals' },
] as const

export function Ledger() {
  const [tab, setTab] = useState<'journals' | 'balance' | 'reconcile'>('journals')
  return (
    <div className="space-y-4">
      <Chips
        options={[
          { id: 'journals', label: 'Journals' },
          { id: 'balance', label: 'Trial balance' },
          { id: 'reconcile', label: 'Reconciliation' },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'journals' && <Journals />}
      {tab === 'balance' && <TrialBalance />}
      {tab === 'reconcile' && <Reconciliation />}
    </div>
  )
}

function Journals() {
  const [kind, setKind] = useState<string>('')
  const [offset, setOffset] = useState(0)
  const journals = useApi(() => kaluta.adminConsole.journals(kind, offset), [kind, offset])
  const [msg, setMsg] = useState<Msg>(null)
  const items = journals.data?.items ?? []
  const payments = usePayments(items.map((j) => j.reference))
  const members = useMembers([
    ...Object.values(payments).map((p) => p.user_id),
    ...items.flatMap((j) => j.entries.map((e) => e.beneficiary_id)),
  ])
  return (
    <Panel title="Journals" subtitle="Every posting, newest first. A journal is never edited: a mistake is reversed by its mirror image.">
      <Chips options={JOURNAL_KINDS} value={kind as (typeof JOURNAL_KINDS)[number]['id']} onChange={(k) => { setKind(k); setOffset(0) }} />
      <Message msg={msg} />
      <PanelState loading={journals.loading} error={journals.error} empty={journals.data?.items.length === 0}>
        <ul className="space-y-2">
          {journals.data?.items.map((j) => (
            <JournalRow key={j.id} j={j} payment={j.reference ? payments[j.reference] : undefined} members={members} onReversed={(t) => { setMsg({ ok: true, text: t }); journals.reload() }}
              onError={(t) => setMsg({ ok: false, text: t })} />
          ))}
        </ul>
        <Pager offset={offset} size={50} total={journals.data?.total ?? 0} onChange={setOffset} />
      </PanelState>
    </Panel>
  )
}

function JournalRow({
  j,
  payment,
  members,
  onReversed,
  onError,
}: {
  j: AdminJournal
  payment: AdminPaymentBrief | undefined
  members: Record<string, AdminMemberBrief> | null
  onReversed: (t: string) => void
  onError: (t: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  return (
    <Row>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="font-semibold text-text-hi">
            {usd(j.amount)} <span className="caption">· {j.kind}</span>
          </p>
          {payment && (
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
              <span className="text-text-low">Paid by</span>
              <Member id={payment.user_id} members={members} inline />
              <ProviderRef payment={payment} />
            </div>
          )}
          <p className="caption mt-1 break-all">
            {j.id} · ref {j.reference ?? '—'} · {when(j.created_at)}
          </p>
        </div>
        <button type="button" className={BTN} onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? 'Hide' : 'Entries'}
        </button>
      </div>
      {open && (
        <>
          <ul className="mt-3 divide-y divide-[var(--cloud-border)] text-xs">
            {j.entries.map((e, i) => (
              <li key={i} className="flex flex-wrap justify-between gap-2 py-1.5">
                <span className="text-text-mid">
                  {e.account}
                  {e.beneficiary_id && (
                    <>
                      {' · '}
                      <Member id={e.beneficiary_id} members={members} inline />
                    </>
                  )}
                </span>
                <span className="mono-data text-text-hi">
                  {Number(e.debit) ? `Dr ${e.debit}` : `Cr ${e.credit}`}
                </span>
              </li>
            ))}
          </ul>
          {j.kind !== 'reversal' && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason for the reversal"
                className={cn(INPUT, 'min-w-0 flex-1')} />
              <ConfirmButton
                label="Reverse"
                danger
                disabled={reason.trim().length < 3}
                question="Post the mirror image? Commissions in it are clawed back."
                onConfirm={async () => {
                  try {
                    await kaluta.adminConsole.reverseJournal(j.id, reason.trim())
                    onReversed('Reversed.')
                  } catch (err) {
                    onError(errorText(err))
                  }
                }}
              />
            </div>
          )}
        </>
      )}
    </Row>
  )
}

function TrialBalance() {
  const tb = useApi(() => kaluta.adminConsole.trialBalance(), [])
  const total = (key: 'debit' | 'credit') => (tb.data?.accounts ?? []).reduce((sum, a) => sum + Number(a[key]), 0)
  return (
    <Panel title="Trial balance" subtitle="Debits and credits per account. The two totals must be equal.">
      <PanelState loading={tb.loading} error={tb.error}>
        <ul className="divide-y divide-[var(--cloud-border)] text-sm">
          {tb.data?.accounts.map((a) => (
            <li key={a.account} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 py-2 sm:grid-cols-[minmax(0,1fr)_7rem_7rem_7rem]">
              <span className="truncate text-text-hi">{a.account}</span>
              <span className="mono-data hidden text-end text-text-mid sm:block">{a.debit}</span>
              <span className="mono-data hidden text-end text-text-mid sm:block">{a.credit}</span>
              <span className="mono-data text-end font-semibold text-text-hi">{a.balance}</span>
            </li>
          ))}
        </ul>
        {tb.data && (
          <p className={cn('mt-3 text-sm font-semibold', total('debit').toFixed(2) === total('credit').toFixed(2) ? 'text-emerald-300' : 'text-red-300')}>
            Debits {usd(total('debit'))} · Credits {usd(total('credit'))}
          </p>
        )}
      </PanelState>
    </Panel>
  )
}

function Reconciliation() {
  const rec = useApi(() => kaluta.adminConsole.reconcile(), [])
  return (
    <Panel title="Reconciliation" subtitle="Each wallet's balance recomputed from its entries."
      action={<button type="button" className={BTN} onClick={rec.reload}><RefreshCw size={12} aria-hidden="true" /> Run again</button>}>
      <PanelState loading={rec.loading} error={rec.error}>
        {rec.data && (
          rec.data.drift_count === 0 ? (
            <p className="text-sm text-emerald-300">{rec.data.checked} wallets checked: every balance matches its entries.</p>
          ) : (
            <>
              <p className="mb-3 text-sm text-red-300">{rec.data.drift_count} wallet(s) drift from their entries.</p>
              <pre className="mono-data overflow-x-auto rounded-xl bg-text-hi/[0.05] p-3 text-xs text-text-mid">
                {JSON.stringify(rec.data.drift, null, 2)}
              </pre>
            </>
          )
        )}
      </PanelState>
    </Panel>
  )
}

/* ------------------------------------------------------------------ kyc */

export function Kyc() {
  const [status, setStatus] = useState<'' | 'verified' | 'pending' | 'rejected' | 'unverified'>('')
  const kyc = useApi(() => kaluta.adminConsole.kyc(status), [status])
  const kycMembers = useMembers((kyc.data?.items ?? []).map((r) => r.user_id))
  return (
    <div className="space-y-5">
      {kyc.data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          <Kpi label={`Fees paid ${kyc.data.year}`} value={kyc.data.counts.fee_paid ?? 0} tone="gold" />
          <Kpi label="Verified" value={kyc.data.counts.verified ?? 0} />
          <Kpi label="Pending" value={kyc.data.counts.pending ?? 0} />
          <Kpi label="Rejected" value={kyc.data.counts.rejected ?? 0} tone={(kyc.data.counts.rejected ?? 0) > 0 ? 'warn' : 'default'} />
          <Kpi label="Not started" value={kyc.data.counts.unverified ?? 0} />
        </div>
      )}
      <Panel title="KinjyKYC" subtitle="Only the result is kept on Kinjy; the documents stay with Kaluta KYC.">
        <Chips
          options={[
            { id: '', label: 'All' },
            { id: 'pending', label: 'Pending' },
            { id: 'verified', label: 'Verified' },
            { id: 'rejected', label: 'Rejected' },
            { id: 'unverified', label: 'Not started' },
          ]}
          value={status}
          onChange={setStatus}
        />
        <PanelState loading={kyc.loading} error={kyc.error} empty={kyc.data?.items.length === 0}>
          <ul className="space-y-2">
            {kyc.data?.items.map((r) => (
              <Row key={`${r.user_id}-${r.year}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <Member id={r.user_id} members={kycMembers} />
                    <p className="caption mt-1">
                      {r.year} · fee {r.fee_paid ? 'paid' : 'not paid'} · attempts {r.attempts}/{r.max_attempts}
                    </p>
                    {r.fee_payment && (
                      <div className="mt-1">
                        <ProviderRef payment={r.fee_payment} />
                      </div>
                    )}
                  </div>
                  <Badge tone={r.status === 'verified' ? 'good' : r.status === 'rejected' ? 'bad' : 'neutral'}>{r.status}</Badge>
                </div>
              </Row>
            ))}
          </ul>
        </PanelState>
      </Panel>
    </div>
  )
}

/* ------------------------------------------------------------------ fraud */

export function Fraud() {
  const [days, setDays] = useState<'7' | '30' | '90'>('30')
  const fraud = useApi(() => kaluta.adminConsole.fraud(Number(days)), [days])
  const risk = useApi(() => kaluta.adminConsole.contactRisk(), [])
  const f = fraud.data
  return (
    <div className="space-y-5">
      <Chips options={[{ id: '7', label: '7 days' }, { id: '30', label: '30 days' }, { id: '90', label: '90 days' }]} value={days} onChange={setDays} />
      <p className="text-sm text-text-low">Signals for review, measured from what Kinjy records. Not findings about anybody.</p>
      <PanelState loading={fraud.loading} error={fraud.error}>
        {f && (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
            <Panel title="Member and sponsor on one address" subtitle={`${f.counts.sponsor_shared_ip} pair(s): the shape of a self-referral.`}>
              <ul className="space-y-2">
                {f.sponsor_shared_ip.length === 0 && <p className="text-sm text-text-low">None.</p>}
                {f.sponsor_shared_ip.slice(0, 50).map((r) => (
                  <Row key={`${r.member_id}-${r.ip}`}>
                    <p className="text-sm text-text-hi">
                      @{r.member_handle} ← @{r.sponsor_handle} {r.via_pool && <Badge tone="warn">via pool</Badge>}
                    </p>
                    <p className="caption mt-1">{r.ip} · joined {when(r.created_at)}</p>
                  </Row>
                ))}
              </ul>
            </Panel>
            <Panel title="Sign-up bursts" subtitle={`${f.counts.signup_bursts} address-day(s) with 5+ new accounts.`}>
              <ul className="space-y-2">
                {f.signup_bursts.length === 0 && <p className="text-sm text-text-low">None.</p>}
                {f.signup_bursts.map((r) => (
                  <Row key={`${r.ip}-${r.day}`}>
                    <p className="text-sm text-text-hi">{r.accounts} accounts from {r.ip}</p>
                    <p className="caption mt-1">{when(r.first_at)} → {when(r.last_at)}</p>
                  </Row>
                ))}
              </ul>
            </Panel>
            <Panel title="Age-gate probing" subtitle={`${f.counts.age_probing} identit(ies) refused several times.`}>
              <ul className="space-y-2">
                {f.age_probing.length === 0 && <p className="text-sm text-text-low">None.</p>}
                {f.age_probing.map((r) => (
                  <Row key={r.subject}>
                    <p className="text-sm text-text-hi">
                      {r.refusals} refusals {r.later_allowed && <Badge tone="bad">later let in</Badge>}
                    </p>
                    <p className="caption mt-1">{r.jurisdiction ?? '—'} · last {when(r.last_at)} · {r.subject}…</p>
                  </Row>
                ))}
              </ul>
            </Panel>
            <Panel title="Contact risk" subtitle="Members who repeatedly tried to message minors and were refused.">
              <PanelState loading={risk.loading} error={risk.error} empty={risk.data?.items.length === 0} emptyLabel="None.">
                <ul className="space-y-2">
                  {risk.data?.items.slice(0, 50).map((r) => (
                    <Row key={r.sender_id}>
                      <p className="break-all text-sm text-text-hi">{r.sender_id}</p>
                      <p className="caption mt-1">
                        {r.distinct_minors_refused} minor(s) · {r.total_attempts} attempt(s) · score {r.risk_score}
                      </p>
                    </Row>
                  ))}
                </ul>
              </PanelState>
            </Panel>
          </div>
        )}
      </PanelState>
    </div>
  )
}

/* ------------------------------------------------------------------ leaders */

export function Leaders() {
  const months = lastMonths(6)
  const [period, setPeriod] = useState(months[1])
  const live = useApi(() => kaluta.leaders.standings(), [])
  const frozen = useApi(() => kaluta.leaders.frozen(period), [period])
  const leaderMembers = useMembers((live.data?.items ?? []).slice(0, 20).map((l) => l.member_id))
  const [msg, setMsg] = useState<Msg>(null)
  const act = async (fn: () => Promise<unknown>, text: string) => {
    try {
      await fn()
      setMsg({ ok: true, text })
      frozen.reload()
    } catch (err) {
      setMsg({ ok: false, text: errorText(err) })
    }
  }
  const items = frozen.data?.items ?? []
  const reviewed = items.length > 0 && items.every((i) => i.paid || (i as { fraud_reviewed?: boolean }).fraud_reviewed)
  const paid = items.length > 0 && items.every((i) => i.paid)
  return (
    <div className="space-y-5">
      <Panel title={`This month (${thisMonth()})`} subtitle="Live ranking: share = a leader's direct commission ÷ the total of the qualifying members.">
        <PanelState loading={live.loading} error={live.error}>
          {live.data && (
            <>
              <div className="mb-4 grid grid-cols-3 gap-6">
                <Stat label="Pool" value={usd(live.data.pool_amount)} tone="gold" />
                <Stat label="Qualifying" value={String(live.data.qualifying)} />
                <Stat label="Of" value={live.data.pool_size.toLocaleString()} tone="muted" />
              </div>
              <ol className="divide-y divide-[var(--cloud-border)] text-sm">
                {live.data.items.slice(0, 20).map((l) => (
                  <li key={l.member_id} className="flex justify-between gap-2 py-2">
                    <span className="min-w-0 truncate">
                      <span className="text-text-low">#{l.rank} </span>
                      <Member id={l.member_id} members={leaderMembers} inline />
                    </span>
                    <span className="mono-data text-text-mid">{usd(l.direct_commissions)} → {usd(l.projected_payout)}</span>
                  </li>
                ))}
              </ol>
            </>
          )}
        </PanelState>
      </Panel>
      <Panel title="Closing a month" subtitle="Snapshot, then fraud review, then payment - in that order.">
        <Chips options={months.slice(1).map((m) => ({ id: m, label: m }))} value={period} onChange={setPeriod} />
        <Message msg={msg} />
        <div className="mb-4 flex flex-wrap gap-2">
          <ConfirmButton label="1. Snapshot" question={`Freeze the ranking of ${period}? A new snapshot replaces an unpaid one.`}
            disabled={paid} onConfirm={() => act(() => kaluta.adminConsole.leadersSnapshot(period), 'Snapshot taken.')} />
          <ConfirmButton label="2. Mark fraud-reviewed" question={`Confirm ${period} was reviewed for fraud?`}
            disabled={items.length === 0 || paid} onConfirm={() => act(() => kaluta.adminConsole.leadersReviewed(period), 'Marked as reviewed.')} />
          <ConfirmButton label="3. Pay" danger question={`Credit ${period}'s payouts to the leaders' wallets?`}
            disabled={!reviewed || paid} onConfirm={() => act(() => kaluta.adminConsole.leadersPay(period), 'Paid to wallets.')} />
        </div>
        <PanelState loading={frozen.loading} error={frozen.error && !/404|No snapshot/i.test(frozen.error) ? frozen.error : null}
          empty={items.length === 0} emptyLabel={`No snapshot for ${period} yet.`}>
          <ol className="divide-y divide-[var(--cloud-border)] text-sm">
            {items.slice(0, 50).map((l) => (
              <li key={l.leader_id} className="flex flex-wrap justify-between gap-2 py-2">
                <span className="truncate text-text-hi">#{l.rank} {l.leader_id}</span>
                <span className="mono-data text-text-mid">
                  {usd(l.payout_amount)} {l.paid ? '· paid' : (l as { fraud_reviewed?: boolean }).fraud_reviewed ? '· reviewed' : ''}
                </span>
              </li>
            ))}
          </ol>
        </PanelState>
      </Panel>
    </div>
  )
}

/* ------------------------------------------------------------------ payouts */

export function Payouts() {
  const [tab, setTab] = useState<'wallets' | 'withdrawals' | 'refunds'>('wallets')
  return (
    <div className="space-y-4">
      <Chips options={[{ id: 'wallets', label: 'Wallets to approve' }, { id: 'withdrawals', label: 'Withdrawals' }, { id: 'refunds', label: 'Refunds' }]}
        value={tab} onChange={setTab} />
      {tab === 'wallets' && <PayoutWallets />}
      {tab === 'withdrawals' && <Withdrawals />}
      {tab === 'refunds' && <Refunds />}
    </div>
  )
}

function Withdrawals() {
  const rows = useApi(() => kaluta.payoutAdmin.withdrawals(), [])
  const withdrawalMembers = useMembers((rows.data?.items ?? []).map((w) => w.user_id))
  const [msg, setMsg] = useState<Msg>(null)
  return (
    <Panel title="Withdrawals" subtitle="Members' commissions paid out in USDT on BSC."
      action={<ConfirmButton label="Re-read pending" question="Ask the payout provider for every unfinished withdrawal?"
        onConfirm={async () => {
          try { await kaluta.adminConsole.reconcileWithdrawals(); setMsg({ ok: true, text: 'Re-read.' }); rows.reload() }
          catch (err) { setMsg({ ok: false, text: errorText(err) }) }
        }} />}>
      <Message msg={msg} />
      <PanelState loading={rows.loading} error={rows.error} empty={rows.data?.items.length === 0}>
        <ul className="space-y-2">
          {rows.data?.items.map((w) => (
            <Row key={w.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold text-text-hi">{usd(w.amount)} <span className="caption">net {usd(w.net)}</span></p>
                  <div className="mt-1 text-sm"><Member id={w.user_id} members={withdrawalMembers} inline /></div>
                  <p className="caption mt-1 break-all">→ {w.address} · {when(w.created_at)}</p>
                  {w.failure_reason && <p className="mt-1 text-xs text-red-300">{w.failure_reason}</p>}
                </div>
                <Badge tone={w.status === 'completed' ? 'good' : w.status === 'failed' ? 'bad' : 'neutral'}>{w.status}</Badge>
              </div>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

function Refunds() {
  const rows = useApi(() => kaluta.adminConsole.refunds(), [])
  const refundMembers = useMembers((rows.data?.items ?? []).map((r) => r.user_id))
  return (
    <Panel title="Refunds" subtitle="Marketplace refunds of held payments, and pool refunds credited to members' balances.">
      <PanelState loading={rows.loading} error={rows.error} empty={rows.data?.items.length === 0}>
        <ul className="space-y-2">
          {rows.data?.items.map((r) => (
            <Row key={r.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold text-text-hi">{usd(r.amount)} <span className="caption">· {r.rail}</span></p>
                  <div className="mt-1 text-sm"><Member id={r.user_id} members={refundMembers} inline /></div>
                  <p className="caption mt-1 break-all">for {r.order_id} · {when(r.created_at)}</p>
                  {r.failure_reason && <p className="mt-1 text-xs text-red-300">{r.failure_reason}</p>}
                </div>
                <Badge tone={r.status === 'failed' ? 'bad' : r.status === 'pending' ? 'neutral' : 'good'}>{r.status}</Badge>
              </div>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

/* ------------------------------------------------------------------ marketplace */

/**
 * Marketplace operations. Kinjy does not arbitrate ordinary disputes: a
 * purchase is final when the buyer confirms receipt (or, for an ebook, at the
 * first successful download). Admins only fix stuck money and handle
 * operational support.
 */
export function Marketplace() {
  const [tab, setTab] = useState<'attention' | 'support' | 'funds' | 'disputes'>('attention')
  return (
    <div className="space-y-4">
      <Chips
        options={[
          { id: 'attention', label: 'Orders needing attention' },
          { id: 'support', label: 'Support requests' },
          { id: 'funds', label: 'Order funds' },
          { id: 'disputes', label: 'Dispute history' },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === 'attention' && <OrdersNeedingAttention />}
      {tab === 'support' && <SupportRequests />}
      {tab === 'funds' && <OrderFunds />}
      {tab === 'disputes' && <Disputes />}
    </div>
  )
}

const moneyTone = (status: string): 'good' | 'bad' | 'neutral' =>
  status === 'confirmed' ? 'good' : status === 'action_required' ? 'bad' : 'neutral'

function OrdersNeedingAttention() {
  const rows = useApi(() => kaluta.adminConsole.marketOrders(true), [])
  const [msg, setMsg] = useState<Msg>(null)
  const items = rows.data?.items ?? []
  return (
    <Panel
      title="Marketplace orders needing attention"
      subtitle="A seller payment or a refund that is pending or waiting for a person. Retry asks the payment rail again; it never pays twice."
      action={<button type="button" className={BTN} onClick={rows.reload}><RefreshCw size={13} aria-hidden="true" /> Refresh</button>}
    >
      <Message msg={msg} />
      <PanelState loading={rows.loading} error={rows.error} empty={items.length === 0} emptyLabel="Nothing is stuck.">
        <ul className="space-y-2">
          {items.map((o) => (
            <MarketOrderRow key={o.id} o={o} onDone={(t) => { setMsg({ ok: true, text: t }); rows.reload() }}
              onError={(t) => setMsg({ ok: false, text: t })} />
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

function MarketOrderRow({ o, onDone, onError }: { o: Order; onDone: (t: string) => void; onError: (t: string) => void }) {
  const [cancelling, setCancelling] = useState(false)
  const [note, setNote] = useState('')
  const settlement = o.settlement?.status ?? 'none'
  const refund = o.cancellation?.refund_status ?? 'none'
  const retryable = settlement === 'action_required' || refund === 'action_required'
  const final = Boolean(o.acceptance?.accepted)
  const cancellable = !final && !o.cancellation
  return (
    <Row>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-text-hi">
            {usd(o.customer_price)} <span className="caption">· {o.status}{o.payment_rail ? ` · ${o.payment_rail}` : ''}</span>
          </p>
          <p className="caption mt-1 break-all">order {o.id} · {when(o.created_at)}</p>
          <p className="mt-1 text-xs text-text-mid">
            {final
              ? `Final (${o.acceptance.status === 'first_download' ? 'first download' : 'buyer confirmed'}) ${when(o.acceptance.accepted_at)}`
              : 'Not final yet'}
          </p>
          {o.settlement?.error && <p className="mt-1 text-xs text-red-300">Seller payment: {o.settlement.error}</p>}
          {o.cancellation && (
            <p className="mt-1 text-xs text-text-mid">
              Cancelled ({o.cancellation.reason}) {when(o.cancellation.cancelled_at)}
              {o.cancellation.refund_error ? ` · refund: ${o.cancellation.refund_error}` : ''}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {settlement !== 'none' && <Badge tone={moneyTone(settlement)}>seller payment {settlement}</Badge>}
          {refund !== 'none' && <Badge tone={moneyTone(refund)}>refund {refund}</Badge>}
          {retryable && (
            <ConfirmButton label="Retry" question="Ask the payment rail again for this order?"
              onConfirm={async () => {
                try { await kaluta.adminConsole.retryMarketOrder(o.id); onDone('Retried. Check the status again shortly.') }
                catch (err) { onError(errorText(err)) }
              }} />
          )}
          {cancellable && (
            <button type="button" className={BTN} onClick={() => setCancelling(!cancelling)}>Operational cancel</button>
          )}
        </div>
      </div>
      {cancelling && cancellable && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why (kept on the order, shown to both parties)"
            aria-label="Reason for the cancellation" className={cn(INPUT, 'min-w-0 flex-1')} />
          <ConfirmButton label="Cancel order" danger disabled={note.trim().length < 3}
            question="Cancel this order and refund the buyer if it was paid?"
            onConfirm={async () => {
              try { await kaluta.adminConsole.cancelMarketOrder(o.id, note.trim()); onDone('Cancelled.') }
              catch (err) { onError(errorText(err)) }
            }} />
        </div>
      )}
    </Row>
  )
}

const SUPPORT_LABELS: Record<SupportCase['kind'], string> = {
  payment_problem: 'Payment problem',
  duplicate_charge: 'Charged twice',
  not_handed_over: 'Not handed over',
  file_problem: 'Missing or damaged file',
  fraud: 'Suspected fraud',
  legal: 'Legal request',
}

function SupportRequests() {
  const [status, setStatus] = useState<'open' | 'closed'>('open')
  const rows = useApi(() => kaluta.adminConsole.supportCases(status), [status])
  const [msg, setMsg] = useState<Msg>(null)
  const items = rows.data?.items ?? []
  return (
    <Panel title="Support requests" subtitle="Operational help on an order: payments, files, fraud. Not arbitration of the deal itself."
      action={<Chips options={[{ id: 'open', label: 'Open' }, { id: 'closed', label: 'Closed' }]} value={status} onChange={setStatus} />}>
      <Message msg={msg} />
      <PanelState loading={rows.loading} error={rows.error} empty={items.length === 0}
        emptyLabel={status === 'open' ? 'No open request.' : 'No closed request.'}>
        <ul className="space-y-2">
          {items.map((c) => (
            <SupportRow key={c.id} c={c} onDone={(t) => { setMsg({ ok: true, text: t }); rows.reload() }}
              onError={(t) => setMsg({ ok: false, text: t })} />
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

function SupportRow({ c, onDone, onError }: { c: SupportCase; onDone: (t: string) => void; onError: (t: string) => void }) {
  const [note, setNote] = useState('')
  return (
    <Row>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-text-hi">{SUPPORT_LABELS[c.kind] ?? c.kind} <span className="caption">· from the {c.role}</span></p>
          <p className="mt-1 whitespace-pre-line text-sm text-text-mid">{c.message}</p>
          <p className="caption mt-1 break-all">order {c.order_id} · {when(c.created_at)}</p>
          {c.resolution_note && <p className="mt-1 text-xs text-text-mid">Closed {when(c.closed_at)}: {c.resolution_note}</p>}
        </div>
        <Badge tone={c.status === 'open' ? 'warn' : 'neutral'}>{c.status}</Badge>
      </div>
      {c.status === 'open' && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What was done (shown to the member)"
            aria-label="Closing note" className={cn(INPUT, 'min-w-0 flex-1')} />
          <ConfirmButton label="Close" disabled={note.trim().length < 3} question="Close this request with this note?"
            onConfirm={async () => {
              try { await kaluta.adminConsole.closeSupportCase(c.id, note.trim()); onDone('Closed.') }
              catch (err) { onError(errorText(err)) }
            }} />
        </div>
      )}
    </Row>
  )
}

function OrderFunds() {
  const [status, setStatus] = useState<'' | 'pending' | 'action_required' | 'confirmed'>('')
  const rows = useApi(() => kaluta.adminConsole.orderFunds(status || undefined), [status])
  const items = rows.data?.items ?? []
  return (
    <Panel title="Order funds" subtitle="Every payment to a seller and every refund of marketplace order money, with what the provider answered."
      action={<Chips options={[{ id: '', label: 'All' }, { id: 'pending', label: 'Pending' }, { id: 'action_required', label: 'Needs a person' }, { id: 'confirmed', label: 'Confirmed' }]}
        value={status} onChange={setStatus} />}>
      <PanelState loading={rows.loading} error={rows.error} empty={items.length === 0} emptyLabel="Nothing here.">
        <ul className="space-y-2">
          {items.map((f) => (
            <Row key={f.reference}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold text-text-hi">
                    {usd(f.amount)} <span className="caption">· {f.purpose === 'refund' ? 'refund to the buyer' : 'payment to the seller'}{f.rail ? ` · ${f.rail}` : ''}</span>
                  </p>
                  <p className="caption mt-1 break-all">order {f.order_id} · ref {f.reference} · {when(f.created_at)}</p>
                  <p className="caption mt-1 break-all">
                    {f.attempts} attempt{f.attempts === 1 ? '' : 's'}{f.backing ? ` · backed by ${f.backing}` : ''}
                    {f.provider_refs ? ` · provider ${f.provider_refs}` : ''}{f.confirmed_at ? ` · confirmed ${when(f.confirmed_at)}` : ''}
                  </p>
                  {f.error && <p className="mt-1 text-xs text-red-300">{f.error}</p>}
                </div>
                <Badge tone={moneyTone(f.status)}>{f.status}</Badge>
              </div>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

type Dispute = {
  id: string
  order_id: string
  category: string
  reason: string
  amount_claimed: string
  status: string
  outcome: string | null
  created_at: string
}

/** Disputes opened before 27/10. Kept for the record; nothing can be decided any more. */
export function Disputes() {
  const rows = useApi(() => kaluta.adminConsole.disputes(), [])
  const items = (Array.isArray(rows.data) ? rows.data : rows.data?.items ?? []) as unknown as Dispute[]
  return (
    <Panel title="Dispute history" subtitle="Read only. Marketplace disputes are no longer arbitrated by Kinjy since 27/10.">
      <PanelState loading={rows.loading} error={rows.error} empty={items.length === 0} emptyLabel="No dispute on record.">
        <ul className="space-y-2">
          {items.map((d) => (
            <Row key={d.id}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold text-text-hi">{usd(d.amount_claimed)} <span className="caption">· {d.category}</span></p>
                  <p className="mt-1 text-sm text-text-mid">{d.reason}</p>
                  <p className="caption mt-1 break-all">order {d.order_id} · opened {when(d.created_at)}</p>
                </div>
                <Badge tone="neutral">{d.outcome ?? d.status}</Badge>
              </div>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

/* ------------------------------------------------------------------ moderation */

export function Moderation() {
  const o = useApi(() => kaluta.trustSafety.overview(), [])
  return (
    <div className="space-y-5">
      <PanelState loading={o.loading} error={o.error}>
        {o.data && (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Kpi label="Classified automatically" value={o.data.classified_total.toLocaleString()} />
            <Kpi label="Waiting for a person" value={o.data.pending_review.toLocaleString()} tone={o.data.pending_review > 0 ? 'warn' : 'default'} />
            <Kpi label="Child-safety escalations" value={o.data.child_safety_escalations.toLocaleString()} tone={o.data.child_safety_escalations > 0 ? 'bad' : 'default'} />
            <Kpi label="Reports (24 h)" value={o.data.reports_24h} hint={`${o.data.reports_total} in total`} />
            <Kpi label="Appeals open" value={o.data.appeals.open} hint={`${o.data.appeals.overdue} overdue`} tone={o.data.appeals.overdue > 0 ? 'warn' : 'default'} />
            <Kpi label="Appeals answered" value={o.data.appeals.answered} />
            <Kpi label="Decisions overturned" value={o.data.appeals.overturned}
              hint={o.data.appeals.overturn_rate != null ? `${Math.round(o.data.appeals.overturn_rate * 100)}% of answered` : undefined} />
          </div>
        )}
      </PanelState>
      <Panel title="Where the work is done">
        <ul className="flex flex-wrap gap-2">
          <li><Link to="/moderation" className={BTN}><ExternalLink size={12} aria-hidden="true" /> Moderation queue</Link></li>
          <li><Link to="/trust-safety" className={BTN}><ExternalLink size={12} aria-hidden="true" /> Review console</Link></li>
        </ul>
      </Panel>
    </div>
  )
}

/* ------------------------------------------------------------------ team */

export function Team() {
  const team = useApi(() => kaluta.team.list(), [])
  return (
    <Panel title="Team" subtitle="Who has a staff role."
      action={<Link to="/team" className={BTN}><ExternalLink size={12} aria-hidden="true" /> Manage</Link>}>
      <PanelState loading={team.loading} error={team.error} empty={team.data?.items.length === 0}>
        <ul className="space-y-2">
          {team.data?.items.map((m) => (
            <Row key={m.user_id}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm text-text-hi">{m.display_name} <span className="caption">@{m.handle} · {m.email}</span></p>
                <Badge tone={m.role === 'superadmin' ? 'warn' : 'neutral'}>{m.role}</Badge>
              </div>
            </Row>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}

/* ------------------------------------------------------------------ communities */

/** Platform rules for paid communities: the markup, the grace days, the switch. */
export function CommunitiesSettings() {
  const settings = useApi(() => kaluta.communityPaid.adminSettings(), [])
  const [markup, setMarkup] = useState('')
  const [grace, setGrace] = useState('')
  const [msg, setMsg] = useState<Msg>(null)
  const d = settings.data
  const save = async (patch: Parameters<typeof kaluta.communityPaid.setAdminSettings>[0], text: string) => {
    setMsg(null)
    try {
      await kaluta.communityPaid.setAdminSettings(patch)
      setMsg({ ok: true, text })
      settings.reload()
    } catch (err) {
      setMsg({ ok: false, text: errorText(err) })
    }
  }
  return (
    <Panel title="Paid communities" subtitle="Rules for community memberships and event tickets, applied to new prices.">
      <Message msg={msg} />
      <PanelState loading={settings.loading} error={settings.error}>
        {d && (
          <div className="space-y-5">
            <div className="flex flex-wrap items-center gap-3">
              <Badge tone={d.community_paid_enabled.value === 'true' ? 'good' : 'neutral'}>
                {d.community_paid_enabled.value === 'true' ? 'Paid features on' : 'Paid features off'}
              </Badge>
              <ConfirmButton
                label={d.community_paid_enabled.value === 'true' ? 'Switch off' : 'Switch on'}
                question={d.community_paid_enabled.value === 'true'
                  ? 'Stop new paid tiers, prices and tickets? Existing access is kept.'
                  : 'Let community owners sell memberships and tickets?'}
                onConfirm={() => save({ community_paid_enabled: d.community_paid_enabled.value !== 'true' }, 'Saved.')}
              />
            </div>
            <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => {
              e.preventDefault()
              void save({ community_markup_pct: Number(markup || d.community_markup_pct.value),
                          community_grace_days: Number(grace || d.community_grace_days.value) }, 'Saved: new prices use these rules.')
            }}>
              <label className="text-sm text-text-mid">
                Kinjy markup on top of the owner's price (%)
                <input type="number" min={0} max={100} step="0.01" placeholder={d.community_markup_pct.value}
                  value={markup} onChange={(e) => setMarkup(e.target.value)} className={cn(INPUT, 'mt-1 block w-32')} />
              </label>
              <label className="text-sm text-text-mid">
                Grace days after a missed renewal
                <input type="number" min={0} max={60} placeholder={d.community_grace_days.value}
                  value={grace} onChange={(e) => setGrace(e.target.value)} className={cn(INPUT, 'mt-1 block w-32')} />
              </label>
              <button type="submit" className={BTN_GOLD}>Save</button>
            </form>
            <p className="caption">
              Markup {d.community_markup_pct.value}% (default {d.community_markup_pct.default}) · grace {d.community_grace_days.value} days
              (default {d.community_grace_days.default}). The sponsor's commission and the Leaders share come out of the markup, as
              for every Kinjy revenue. A quote already given keeps the markup it was given with.
            </p>
          </div>
        )}
      </PanelState>
    </Panel>
  )
}

/* ------------------------------------------------------------------ system */

export function System() {
  const services = useApi(() => kaluta.adminConsole.services(), [])
  const rec = useApi(() => kaluta.adminConsole.reconcile(), [])
  const ai = useApi(() => kaluta.adminConsole.aiObservability(), [])
  const [msg, setMsg] = useState<Msg>(null)
  const flags = Object.entries(FEATURES as Record<string, boolean>)
  return (
    <div className="space-y-5">
      <Panel title="Services" action={<button type="button" className={BTN} onClick={services.reload}><RefreshCw size={12} aria-hidden="true" /> Refresh</button>}>
        <PanelState loading={services.loading} error={services.error}>
          {services.data && <ServiceList services={services.data.services} />}
        </PanelState>
      </Panel>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-2">
        <Panel title="Money checks">
          <Message msg={msg} />
          <PanelState loading={rec.loading} error={rec.error}>
            {rec.data && (
              <p className={cn('text-sm', rec.data.drift_count ? 'text-red-300' : 'text-emerald-300')}>
                Ledger: {rec.data.checked} wallets, {rec.data.drift_count} drift.
              </p>
            )}
          </PanelState>
          <div className="mt-4 flex flex-wrap gap-2">
            <ConfirmButton label="Retry payment splits" question="Retry the Kaluta Pay splits that did not land?"
              onConfirm={async () => {
                try { await kaluta.adminConsole.reconcileSplits(); setMsg({ ok: true, text: 'Splits retried.' }) }
                catch (err) { setMsg({ ok: false, text: errorText(err) }) }
              }} />
            <ConfirmButton label="Re-read withdrawals" question="Ask the payout provider for every unfinished withdrawal?"
              onConfirm={async () => {
                try { await kaluta.adminConsole.reconcileWithdrawals(); setMsg({ ok: true, text: 'Withdrawals re-read.' }) }
                catch (err) { setMsg({ ok: false, text: errorText(err) }) }
              }} />
          </div>
        </Panel>
        <Panel title="Features" subtitle="What this build has switched on (lib/features.ts).">
          <ul className="grid grid-cols-2 gap-2 text-sm">
            {flags.map(([name, on]) => (
              <li key={name} className="flex items-center justify-between rounded-xl bg-text-hi/[0.04] px-3 py-1.5">
                <span className="text-text-hi">{name}</span>
                <Badge tone={on ? 'good' : 'neutral'}>{on ? 'on' : 'off'}</Badge>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      <Panel title="AI observability" subtitle="The classifier and assistant calls, as ai-service measures them.">
        <PanelState loading={ai.loading} error={ai.error}>
          <pre className="mono-data max-h-80 overflow-auto rounded-xl bg-text-hi/[0.05] p-3 text-xs text-text-mid">
            {JSON.stringify(ai.data, null, 2)}
          </pre>
        </PanelState>
      </Panel>
    </div>
  )
}
