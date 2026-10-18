import { FEATURES, isRouteAvailable, type Feature } from '@/lib/features'

export interface ModuleInfo {
  letter: string
  glyph: string
  name: string
  tagline: string
  description: string
  bullets: string[]
  /** ids of letters this module connects to in the constellation */
  connections: string[]
  cta?: { label: string; to: string }
  /** Left off the public site while this feature is switched off. */
  feature?: Feature
  /** The letter the module has in the full blueprint, which keys its illustration. */
  visual?: string
}

const ALL_MODULES: ModuleInfo[] = [
  {
    letter: 'A',
    glyph: 'mod-home',
    name: 'Social Hub',
    tagline: 'Your identity, alive.',
    description:
      'Your profile, friends, followers, status and life events — the warm center of your Kinjy presence, aware of every circle and language you speak.',
    bullets: ['Rich profiles & life events', 'Friends & followers graph', 'Status with audience controls'],
    connections: ['B', 'D', 'F', 'H'],
  },
  {
    letter: 'B',
    glyph: 'mod-globe-grid',
    name: 'Public Content',
    tagline: 'The whole world, drilled down.',
    description:
      'Geographic discovery that zooms from the planet to your street: Global → Continent → Region → Country → State → District → City → Neighborhood.',
    bullets: ['8-level geographic drill-down', 'Local voices surface first', 'Every post carries provenance'],
    connections: ['A', 'C', 'E', 'K', 'L'],
  },
  {
    letter: 'C',
    glyph: 'mod-forums',
    name: 'Forums',
    tagline: 'Structured discussion that remembers.',
    description:
      'Geographic and topic hierarchies with threaded discussion — and AI summaries that digest hundred-post threads into two honest lines.',
    bullets: ['Geographic + topic hierarchies', 'Deep threading, clean reading', 'AI thread summaries'],
    connections: ['B', 'E', 'N'],
  },
  {
    letter: 'D',
    glyph: 'mod-circles',
    name: 'Circles',
    tagline: 'Private networks, filtered your way.',
    description:
      'Family, Close Friends, Business, Customers — plus Smart Circles maintained by AI according to rules you set, not rules we sell.',
    bullets: ['Family · Close Friends · Business', 'Smart Circles by your rules', 'Post to exactly who matters'],
    connections: ['A', 'F', 'H'],
  },
  {
    letter: 'E',
    glyph: 'mod-community',
    name: 'Communities & Groups',
    tagline: 'Gather around anything.',
    description:
      'Public, private, secret or paid — communities with real governance, real privacy tiers and AI managers that keep them healthy.',
    bullets: ['Public / private / secret / paid', 'Member governance tools', 'AI community managers'],
    connections: ['B', 'C', 'J', 'K', 'N'],
  },
  {
    letter: 'F',
    glyph: 'mod-message',
    name: 'Private Messenger',
    tagline: 'Sealed by default.',
    description:
      FEATURES.calls
        ? 'End-to-end encrypted messaging with voice & video calls and disappearing messages. Your words belong to the people in the room.'
        : 'End-to-end encrypted messaging with disappearing messages. Your words belong to the people in the room.',
    bullets: [
      'E2E encryption, always on',
      ...(FEATURES.calls ? ['Voice & video calls'] : []),
      'Disappearing messages',
    ],
    connections: ['A', 'D', 'N'],
  },
  {
    letter: 'G',
    glyph: 'mod-creator',
    name: 'Creator Studio',
    tagline: 'Write once. Publish everywhere.',
    description:
      'An AI copilot and the One-to-Many Publishing Engine: one idea becomes an article, short & long video, audio, carousel, newsletter — and translations.',
    bullets: ['One-to-Many Publishing Engine', 'AI copilot, your voice', 'Auto-translation & dubbing'],
    connections: ['E', 'K', 'L', 'M', 'N'],
    cta: { label: 'Creator Studio →', to: '/creators' },
  },
  {
    letter: 'H',
    glyph: 'mod-family',
    name: 'Family Tree',
    tagline: 'Your bloodline, verified.',
    description:
      'A verified genealogy graph with infinite levels, corroboration workflows and the question every family asks: “How are we related?”',
    bullets: ['Verified relationship graph', 'Infinite generational levels', 'Path-finder between any two'],
    connections: ['A', 'D', 'I', 'N'],
    cta: { label: 'Explore Family Tree →', to: '/family' },
    feature: 'familyTree',
  },
  {
    letter: 'I',
    glyph: 'mod-candle',
    name: 'Digital Graveyard',
    tagline: 'Memory, kept with dignity.',
    description:
      'Memorials with candles and flowers, engraved QR codes on physical stones, legacy contacts and succession handled with care.',
    bullets: ['Candles, flowers & tributes', 'QR-linked physical memorials', 'Legacy contacts & succession'],
    connections: ['H', 'A', 'N'],
    cta: { label: 'Visit Memorials →', to: '/memorials' },
  },
  {
    letter: 'J',
    glyph: 'mod-calendar',
    name: 'Events',
    tagline: 'From invite to memory.',
    description:
      'Public and private events with ticketing, RSVPs and reminders — wired into communities, circles and your calendar.',
    bullets: ['Public & private events', 'Ticketing & RSVPs', 'Smart reminders'],
    connections: ['A', 'E', 'K'],
  },
  {
    letter: 'K',
    glyph: 'mod-storefront',
    name: 'Marketplace & Services',
    tagline: 'Honest commerce, transparent margins.',
    description:
      'Shops and services on a clear 20% margin model — sellers always know the math, buyers always know the price.',
    bullets: ['Shops & service listings', 'Transparent 20% markup', 'Escrowed until you confirm'],
    connections: ['B', 'E', 'G', 'J', 'L', 'M', 'O'],
    cta: { label: 'Explore Commerce →', to: '/commerce' },
    feature: 'marketplace',
  },
  {
    letter: 'L',
    glyph: 'mod-megaphone',
    name: 'Advertising Platform',
    tagline: 'Reach, without the creep.',
    description:
      'Auction floors from $0.50 CPM and an AI campaign builder that targets geography and context — never shadow profiles.',
    bullets: ['Auctions from $0.50 CPM', 'AI campaign builder', 'Geographic targeting, 20 km radius'],
    connections: ['B', 'G', 'K', 'N'],
    cta: { label: 'Advertising Engine →', to: '/commerce' },
  },
  {
    letter: 'M',
    glyph: 'mod-coins',
    name: 'Commission & Earnings',
    tagline: 'Growth that pays its people.',
    description:
      'Every way to earn on Kinjy, in one wallet: 20% of Kinjy’s revenue on everything the members you invited do, 40% of the ad revenue your content brings in, your own sales in the marketplace, paid access to your community, a seat in the referral pool, and the monthly Kinjy Leaders share. Withdrawn in USDT once your identity is verified, on a ledger you can audit.',
    bullets: [
      'Invite · 20% of our revenue on your members',
      'Create · 40% of the ad revenue on your content',
      'Sell · products and services, held in escrow',
      'Communities · paid access to yours',
      'Referral pool · new sign-ups assigned to you',
      'Kinjy Leaders · 5% of monthly revenue',
      'Withdraw in USDT after KYC',
    ],
    connections: ['A', 'G', 'K'],
  },
  {
    letter: 'N',
    glyph: 'mod-ai',
    name: 'AI Intelligence Layer',
    tagline: 'Woven through everything.',
    description:
      FEATURES.assistant
        ? 'Personal assistants, autonomous creator agents, community managers, forum assistants and knowledge vaults — an intelligent layer, not a feature.'
        : 'Autonomous creator agents, community managers, forum assistants and knowledge vaults — an intelligent layer, not a feature.',
    bullets: FEATURES.assistant
      ? ['Personal AI per user', 'Autonomous agents with approvals', 'Knowledge vaults']
      : ['Autonomous agents with approvals', 'AI community managers', 'Knowledge vaults'],
    connections: ['A', 'C', 'E', 'F', 'G', 'H', 'I', 'L', 'O'],
  },
  {
    letter: 'O',
    glyph: 'mod-code',
    name: 'Developer Platform',
    tagline: 'Open, agent-readable, yours to build on.',
    description:
      'REST & GraphQL APIs, webhooks, OAuth, an App Marketplace and agent-readable endpoints — even feed algorithms are publishable.',
    bullets: ['REST / GraphQL / webhooks', 'OAuth & App Marketplace', 'Publish feed algorithms'],
    connections: ['K', 'N', 'G'],
    cta: { label: 'Developer Platform →', to: '/developers' },
  },
]

/**
 * The modules the public site presents.
 *
 * One whose feature is switched off in lib/features.ts is left out, and so is
 * everything that pointed at it — constellation edges, "connects to" names and
 * calls to action — so a hidden module cannot come back as a link.
 *
 * The rest are lettered again from A, so the sequence has no hole where a hidden
 * module used to be; `visual` keeps the original letter its illustration is
 * filed under.
 */
const SHOWN = ALL_MODULES.filter((m) => !m.feature || FEATURES[m.feature])
const LETTER = new Map(SHOWN.map((m, i) => [m.letter, String.fromCharCode(65 + i)]))

export const MODULES: ModuleInfo[] = SHOWN.map((m) => ({
  ...m,
  letter: LETTER.get(m.letter)!,
  visual: m.letter,
  connections: m.connections.flatMap((c) => LETTER.get(c) ?? []),
  cta: m.cta && isRouteAvailable(m.cta.to) ? m.cta : undefined,
}))

/** Module lookup by letter. */
export const MODULE_BY_LETTER = new Map(MODULES.map((m) => [m.letter, m]))

/** Names of a module's connections, for the constellation side panel. */
export function connectionNames(letter: string): string[] {
  const m = MODULE_BY_LETTER.get(letter)
  if (!m) return []
  return m.connections.map((c) => MODULE_BY_LETTER.get(c)?.name ?? c)
}

/** Deduped edge list (pairs of letters) for the constellation. */
export const CONSTELLATION_EDGES: Array<[string, string]> = (() => {
  const seen = new Set<string>()
  const edges: Array<[string, string]> = []
  for (const m of MODULES) {
    for (const c of m.connections) {
      const key = [m.letter, c].sort().join('-')
      if (!seen.has(key)) {
        seen.add(key)
        edges.push([m.letter, c])
      }
    }
  }
  return edges
})()
