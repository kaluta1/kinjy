import { Link } from 'react-router'
import { Badge, Panel, PanelState } from '@/components/dashboard/primitives'
import { useApi } from '@/hooks/useApi'
import { kaluta } from '@/lib/api'

const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' }) : '')
const LABEL = { trial: 'Trial', active: 'Active', grace: 'Payment due', expired: 'Ended', cancelled: 'Cancelled', refunded: 'Refunded' } as const

/** The member's paid community memberships: status, renewal date, a way back to each. */
export default function MyMemberships() {
  const rows = useApi(() => kaluta.communityPaid.mine(), [])
  return (
    <Panel title="Community memberships" subtitle="Paid access to communities. Renewals are paid by hand from each community's page.">
      <PanelState loading={rows.loading} error={rows.error} empty={rows.data?.items.length === 0}
        emptyLabel="You have no paid community membership.">
        <ul className="space-y-3">
          {rows.data?.items.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-[var(--cloud-border)] bg-ink-2/40 p-3.5">
              <div className="min-w-0">
                <p className="font-semibold text-text-hi">
                  {m.community ? <Link to={`/communities/${m.community.slug}`} className="hover:underline">{m.community.name}</Link> : 'A community'}
                </p>
                <p className="caption mt-1">
                  {m.tier?.name}
                  {m.status === 'grace' && m.grace_until ? ` · access until ${day(m.grace_until)}` : ''}
                  {m.has_access && m.period_end && m.status !== 'grace' ? ` · ${m.cancel_at_period_end ? 'ends' : 'renew by'} ${day(m.period_end)}` : ''}
                </p>
              </div>
              <Badge tone={m.status === 'active' || m.status === 'trial' ? 'good' : m.status === 'grace' ? 'warn' : 'neutral'}>
                {LABEL[m.status]}
              </Badge>
            </li>
          ))}
        </ul>
      </PanelState>
    </Panel>
  )
}
