import { useState } from 'react'
import { Link } from 'react-router'
import { ArrowLeft, Plus } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import SalesToShip from '@/components/commerce/SalesToShip'
import SellerFinances from '@/components/commerce/SellerFinances'
import SellerListings from '@/components/commerce/SellerListings'
import { cn } from '@/lib/utils'

const TABS = [
  { id: 'listings', label: 'My listings' },
  { id: 'ship', label: 'Orders' },
  { id: 'money', label: 'Earnings' },
] as const

type Tab = (typeof TABS)[number]['id']

export default function MarketSeller() {
  const [tab, setTab] = useState<Tab>('listings')

  return (
    <AppShell
      title="My shop"
      subtitle="Manage your listings, hand over your orders and follow your earnings."
      action={
        <Link
          to="/market/sell"
          className="inline-flex min-h-[44px] items-center gap-1.5 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 text-sm font-bold text-ink"
        >
          <Plus size={14} aria-hidden="true" />
          Sell
        </Link>
      }
    >
      <div className="mx-auto max-w-2xl">
        <Link
          to="/market"
          className="mb-3 inline-flex min-h-[44px] items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-gold-soft"
        >
          <ArrowLeft size={14} aria-hidden="true" />
          Marketplace
        </Link>

        <div role="tablist" aria-label="Shop sections" className="-mx-4 mb-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:px-0">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              role="tab"
              id={`seller-tab-${id}`}
              aria-selected={tab === id}
              aria-controls="seller-panel"
              onClick={() => setTab(id)}
              className={cn(
                'min-h-[44px] shrink-0 whitespace-nowrap rounded-full border px-4 text-sm font-semibold transition-colors',
                tab === id
                  ? 'border-gold/60 bg-gold/[0.12] text-gold-soft'
                  : 'border-white/12 text-text-mid hover:border-gold/40',
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div role="tabpanel" id="seller-panel" aria-labelledby={`seller-tab-${tab}`}>
          {tab === 'listings' && <SellerListings />}
          {tab === 'ship' && <SalesToShip />}
          {tab === 'money' && <SellerFinances />}
        </div>
      </div>
    </AppShell>
  )
}
