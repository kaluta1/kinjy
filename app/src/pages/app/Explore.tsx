import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { BadgeCheck, ChevronRight, Compass, Package, Search, TreeDeciduous, UserRound, UsersRound } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import AppShell from '@/components/app/AppShell'
import { People, Places } from '@/components/social/Suggestions'
import MemberAvatar from '@/components/social/MemberAvatar'
import { kaluta, type Community, type Person, type PersonBrief, type Product } from '@/lib/api'
import { FEATURES } from '@/lib/features'
import { cn } from '@/lib/utils'

type Member = PersonBrief & { user_id: string }

/** user-service answers nothing below two characters, so the page does not ask. */
const MIN_QUERY = 2
/** The most user-service returns in one answer. */
const PEOPLE_LIMIT = 25

/**
 * What the page searches. A feature switched off in lib/features.ts is neither
 * queried nor given a section, and the subtitle does not promise it.
 */
const SCOPE = [
  'people',
  'communities',
  ...(FEATURES.marketplace ? ['the marketplace'] : []),
  ...(FEATURES.familyTreeApp ? ['the family graph'] : []),
]
const SCOPE_TEXT = `${SCOPE.slice(0, -1).join(', ')} and ${SCOPE.at(-1)}`

/** Sections under People, one grid column each. Literal classes, for Tailwind. */
const COLUMNS = SCOPE.length - 1
const GRID_COLS = ['', '', 'lg:grid-cols-2', 'lg:grid-cols-3'][COLUMNS]
const FULL_ROW = ['', '', 'lg:col-span-2', 'lg:col-span-3'][COLUMNS]

function Section({
  title,
  icon: Icon,
  count,
  empty,
  emptyText = 'Nothing found.',
  className,
  children,
}: {
  title: string
  icon: typeof Compass
  count: number
  empty: boolean
  emptyText?: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <section className={cn('cloud-card p-5 md:p-6', className)}>
      <h2 className="mb-4 flex items-center gap-2.5 text-[1.05rem] font-bold tracking-[-0.02em] text-text-hi">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[10px] bg-gold/15 text-gold-soft">
          <Icon size={16} aria-hidden="true" />
        </span>
        {title}
        <span className="mono-data ms-auto rounded-full bg-text-hi/[0.07] px-2.5 py-0.5 text-xs font-semibold text-text-mid">
          {count}
        </span>
      </h2>
      {empty ? <p className="text-sm leading-relaxed text-text-low">{emptyText}</p> : children}
    </section>
  )
}

/** One result row: a linked title over a quiet detail line, with a hover state. */
function ResultRow({ to, title, detail }: { to: string; title: string; detail: string }) {
  return (
    <li>
      <Link
        to={to}
        className="group -mx-2 flex items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-text-hi/[0.05]"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[0.95rem] font-semibold text-text-hi group-hover:text-gold-soft">{title}</span>
          <span className="block truncate text-xs text-text-low">{detail}</span>
        </span>
        <ChevronRight size={15} className="shrink-0 text-text-low" aria-hidden="true" />
      </Link>
    </li>
  )
}

/**
 * Explore — one query across the services that can answer it.
 *
 * Each service is searched independently and a failing one degrades its own
 * section instead of emptying the page: a marketplace outage should not make it
 * look like there are no communities either.
 */

/**
 * Results on their way.
 *
 * A search here fans out to four services, so on a slow connection the page
 * used to answer a query by showing nothing at all - the sections only exist
 * once something has come back. These are those sections, empty: the member
 * sees where the answers will be while they are being fetched.
 */
function ResultsSkeleton() {
  // The theme's own subtle surface. A fixed grey is invisible on one of the
  // three themes, and an opacity modifier on a variable colour has already
  // cost this codebase one skeleton that was in the DOM and not on screen.
  const { tok } = useAppTheme()
  const bar = cn('block animate-pulse rounded', tok.subtleBg)

  const card = (rows: number, className?: string) => (
    <section className={cn('cloud-card p-5 md:p-6', className)}>
      <div className="mb-4 flex items-center gap-2.5">
        <span className={cn('h-8 w-8 shrink-0 rounded-[10px]', bar)} />
        <span className={cn('h-4 w-28', bar)} />
        <span className={cn('ms-auto h-5 w-8 rounded-full', bar)} />
      </div>
      <ul className="space-y-3">
        {Array.from({ length: rows }, (_, row) => (
          <li key={row} className="flex items-center gap-3">
            <span className={cn('h-10 w-10 shrink-0 rounded-full', bar)} />
            <span className="min-w-0 flex-1">
              <span className={cn('h-3.5', bar)} style={{ width: `${70 - row * 9}%` }} />
              <span className={cn('mt-1.5 h-2.5', bar)} style={{ width: `${48 - row * 6}%` }} />
            </span>
          </li>
        ))}
      </ul>
    </section>
  )

  return (
    <div className={cn('mt-6 grid gap-5', GRID_COLS)} role="status" aria-label="Searching">
      {card(3, FULL_ROW)}
      {Array.from({ length: COLUMNS }, (_, column) => <div key={column}>{card(2)}</div>)}
    </div>
  )
}

export default function Explore() {
  const [params] = useSearchParams()
  const urlQuery = params.get('q') ?? ''
  const [query, setQuery] = useState(urlQuery)
  // The top bar's search lands here as ?q=. Used while this page is already
  // open, the route stays mounted, so the new value is adopted during render.
  const [adopted, setAdopted] = useState(urlQuery)
  if (urlQuery !== adopted) {
    setAdopted(urlQuery)
    setQuery(urlQuery)
  }

  const [busy, setBusy] = useState(false)
  const [members, setMembers] = useState<Member[] | null>(null)
  const [communities, setCommunities] = useState<Community[] | null>(null)
  const [products, setProducts] = useState<Product[] | null>(null)
  const [people, setPeople] = useState<Person[] | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const latest = useRef(0)
  const pending = useRef<number | undefined>(undefined)

  const search = useCallback(async (q: string) => {
    const ticket = ++latest.current
    setBusy(true)

    const results = await Promise.allSettled([
      kaluta.people.search(q, PEOPLE_LIMIT),
      kaluta.communities.list({ q }),
      FEATURES.marketplace ? kaluta.market.products({ q }) : null,
      FEATURES.familyTreeApp ? kaluta.family.search(q) : null,
    ])

    // Answers arrive out of order: the one for "e" can land after the one for
    // "ez", and must not replace what the member has typed since.
    if (ticket !== latest.current) return

    const failures: string[] = []
    setMembers(results[0].status === 'fulfilled' ? results[0].value.items : (failures.push('people'), null))
    setCommunities(results[1].status === 'fulfilled' ? results[1].value.items : (failures.push('communities'), null))
    setProducts(results[2].status === 'fulfilled' ? (results[2].value?.items ?? null) : (failures.push('marketplace'), null))
    setPeople(results[3].status === 'fulfilled' ? (results[3].value?.items ?? null) : (failures.push('family tree'), null))
    setErrors(failures)
    setBusy(false)
  }, [])

  const q = query.trim()
  const active = q.length >= MIN_QUERY

  // Results follow the typing, so "ez" already lists Ezekiel. Debounced: each
  // search fans out to several services, and a burst of keystrokes should cost one.
  useEffect(() => {
    if (!active) {
      // Whatever is still in flight answers text that is no longer there.
      latest.current++
      return
    }
    pending.current = window.setTimeout(() => void search(q), 250)
    return () => window.clearTimeout(pending.current)
  }, [q, active, search])

  // Enter and the button still search, straight away rather than after the pause.
  const run = (event: React.FormEvent) => {
    event.preventDefault()
    if (!active) return
    window.clearTimeout(pending.current)
    void search(q)
  }

  const searching = busy && active

  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-[clamp(38px,5vw,56px)] font-bold leading-[1.02] tracking-[-0.045em] text-text-hi">
          Find anything on Kinjy
        </h1>
        <p className="mt-3 max-w-xl text-[0.95rem] leading-relaxed text-text-low">Search {SCOPE_TEXT} at once.</p>
      </header>

      <form onSubmit={run}>
        <label className="flex items-center gap-3 rounded-full border border-[var(--cloud-border)] bg-[var(--cloud)] py-2.5 pe-2.5 ps-5 shadow-[0_15px_35px_-22px_rgba(76,62,43,0.35)] transition-colors focus-within:border-gold/60">
          <Search size={19} className="shrink-0 text-text-low" aria-hidden="true" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search Kinjy — a name, Kigoma, cassava…"
            aria-label="Search Kinjy"
            className="w-full bg-transparent text-base text-text-hi placeholder:text-text-low focus:outline-none"
          />
          <button
            type="submit"
            disabled={!active || searching}
            className="shrink-0 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2.5 text-sm font-bold text-ink disabled:opacity-40"
          >
            {searching ? 'Searching…' : 'Search'}
          </button>
        </label>
      </form>

      {/* Nothing typed yet. A search box on an empty page tells somebody who
          has just arrived to go away and think of a word; the point of a
          discovery page is that it answers before it is asked. These are the
          same two lists the feed's rail carries, at a size that suits a page. */}
      {!active && (
        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <People limit={12} title="People to connect with" />
          <Places limit={8} title="Community spaces to join" />
        </div>
      )}

      {active && errors.length > 0 && (
        <p role="status" className="mt-4 rounded-xl bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
          Could not reach: {errors.join(', ')}. The other results are complete.
        </p>
      )}

      {searching && !members && !communities && !products && !people && <ResultsSkeleton />}

      {active && (members || communities || products || people) && (
        <div className={cn('mt-6 grid gap-5', GRID_COLS)}>
          <Section
            title="People"
            icon={UserRound}
            count={(members ?? []).length}
            empty={(members ?? []).length === 0}
            emptyText={`No member whose name starts with “${q}”.`}
            className={FULL_ROW}
          >
            <ul className="grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-3" aria-live="polite">
              {(members ?? []).map((m) => (
                <li key={m.user_id}>
                  {/* The name and the handle line are one link: whichever the
                      member clicks, they land on that person's profile. */}
                  <Link
                    to={`/u/${m.handle}`}
                    className="group -mx-2 flex min-w-0 items-center gap-3 rounded-xl px-2 py-2.5 transition-colors hover:bg-text-hi/[0.05]"
                  >
                    <MemberAvatar handle={m.handle} displayName={m.display_name} avatarUrl={m.avatar_url} size={48} />
                    <span className="min-w-0 flex-1">
                      <span
                        className="flex items-center gap-1 text-[0.95rem] font-semibold text-text-hi group-hover:text-gold-soft"
                        title={`@${m.handle}`}
                      >
                        <span className="truncate">{m.display_name}</span>
                        {m.verified && <BadgeCheck size={14} className="shrink-0 text-gold" aria-label="Verified" />}
                      </span>
                      {m.city && <span className="block truncate text-xs text-text-low">{m.city}</span>}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </Section>

          <Section
            title="Communities"
            icon={UsersRound}
            count={(communities ?? []).length}
            empty={(communities ?? []).length === 0}
          >
            <ul>
              {(communities ?? []).map((c) => (
                <ResultRow key={c.id} to="/communities" title={c.name} detail={`${c.members_count} members · ${c.kind}`} />
              ))}
            </ul>
          </Section>

          {FEATURES.marketplace && (
            <Section
              title="Marketplace"
              icon={Package}
              count={(products ?? []).length}
              empty={(products ?? []).length === 0}
            >
              <ul>
                {(products ?? []).map((p) => (
                  <ResultRow key={p.id} to="/market" title={p.title} detail={`$${p.customer_price}`} />
                ))}
              </ul>
            </Section>
          )}

          {FEATURES.familyTreeApp && (
            <Section
              title="Family graph"
              icon={TreeDeciduous}
              count={(people ?? []).length}
              empty={(people ?? []).length === 0}
            >
              <ul>
                {(people ?? []).map((p) => (
                  <ResultRow
                    key={p.id}
                    to="/tree"
                    title={`${p.given_name} ${p.family_name ?? ''}`.trim()}
                    detail={p.status ?? 'pending'}
                  />
                ))}
              </ul>
            </Section>
          )}
        </div>
      )}
    </AppShell>
  )
}
