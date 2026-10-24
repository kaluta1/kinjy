import { Link } from 'react-router'
import { ArrowLeft, BadgeCheck, Repeat, Scale, Users } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { Panel } from '@/components/dashboard/primitives'
import ReferralPoolBuy from '@/components/pool/ReferralPoolBuy'

const POINTS = [
  {
    icon: Users,
    title: 'Who you receive',
    body: 'Members who join Kinjy without a personal invitation. You become their sponsor and earn the same commission as for somebody you invited yourself.',
  },
  {
    icon: Scale,
    title: 'In proportion to your units',
    body: 'Each unit is a share. Someone with 6 units receives six times as many members as someone with 1. Units are never used up.',
  },
  {
    icon: Repeat,
    title: 'In rounds, in a random order',
    body: 'Each round gives every member as many referrals as they held units when it began. Units bought during a round join the next one.',
  },
]

/**
 * The referral pool, as a service on the market.
 *
 * Kinjy sells it itself: the price is the unit price, with no agency margin
 * and no delivery escrow. Buying happens here with the same component as on
 * the Earn page, so the rules and the payment are identical.
 */
export default function MarketPoolService() {
  return (
    <AppShell>
      <Link to="/market" className="inline-flex items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-text-hi">
        <ArrowLeft size={14} aria-hidden="true" />
        Market
      </Link>
      <header className="mb-6 mt-4">
        <span className="inline-flex items-center gap-1 rounded-full bg-gold/15 px-3 py-1 text-xs font-bold text-gold-soft">
          <BadgeCheck size={12} aria-hidden="true" />
          Official Kinjy service
        </span>
        <h1 className="mt-3 text-[clamp(32px,4.5vw,48px)] font-bold leading-[1.05] tracking-[-0.04em] text-text-hi">
          Referral pool units
        </h1>
        <p className="mt-2 max-w-2xl text-[0.95rem] leading-relaxed text-text-low">
          Grow the number of members you sponsor without having to invite each one yourself.
        </p>
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)] gap-5 lg:grid-cols-[minmax(0,6fr)_minmax(0,5fr)]">
        <Panel title="Buy units">
          <ReferralPoolBuy returnPath="/market/services/referral-pool" />
        </Panel>
        <Panel title="How it works">
          <ul className="space-y-5">
            {POINTS.map((point) => (
              <li key={point.title} className="flex gap-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gold/15 text-gold-soft">
                  <point.icon size={16} aria-hidden="true" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-text-hi">{point.title}</p>
                  <p className="mt-1 text-sm leading-relaxed text-text-low">{point.body}</p>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-6 text-sm text-text-low">
            The members the pool sends you are listed on{' '}
            <Link to="/earn" className="font-semibold text-gold-soft hover:underline">
              your Earn page
            </Link>
            .
          </p>
        </Panel>
      </div>
    </AppShell>
  )
}
