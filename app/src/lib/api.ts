/**
 * Kinjy API client.
 *
 * Everything goes through the gateway on a single origin (`/api`), which Vite
 * proxies to the gateway container in dev. No per-service URLs live in the
 * frontend — if a route moves between services, only the gateway changes.
 */

const BASE = import.meta.env.VITE_API_BASE ?? '/api'
/** For the few requests fetch() cannot make, such as uploads that report progress. */
export const API_BASE = BASE

const ACCESS_KEY = 'kaluta.access_token'
const REFRESH_KEY = 'kaluta.refresh_token'

export class ApiError extends Error {
  // Declared as fields rather than constructor parameter properties: this
  // project builds with `erasableSyntaxOnly`, which forbids the shorthand.
  readonly status: number
  readonly detail?: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS_KEY)
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
  set({ access_token, refresh_token }: { access_token: string; refresh_token: string }) {
    localStorage.setItem(ACCESS_KEY, access_token)
    localStorage.setItem(REFRESH_KEY, refresh_token)
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

/** Pull a readable message out of FastAPI's error shapes. */
function messageFrom(status: number, body: unknown): string {
  if (typeof body === 'string' && body) return body
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    // 422 validation errors arrive as a list of {loc, msg}
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (typeof d?.msg === 'string' ? d.msg : JSON.stringify(d)))
        .join(' · ')
    }
  }
  return `Request failed (${status})`
}

let refreshing: Promise<boolean> | null = null

/**
 * Swap an expired access token for a fresh one. Concurrent 401s share one call.
 * Also used by the realtime socket when the server closes it with 4401.
 */
export async function refreshAccessToken(): Promise<boolean> {
  const refresh_token = tokens.refresh
  if (!refresh_token) return false
  if (refreshing) return refreshing

  refreshing = (async () => {
    try {
      const response = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token }),
      })
      if (!response.ok) {
        tokens.clear()
        return false
      }
      tokens.set(await response.json())
      return true
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()

  return refreshing
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  /** Attach the bearer token when one is stored. Default true. */
  auth?: boolean
  /** Language the API should answer in. */
  lang?: string
  signal?: AbortSignal
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, lang, headers, ...rest } = options

  const send = async (): Promise<Response> => {
    const finalHeaders = new Headers(headers)
    if (body !== undefined && !(body instanceof FormData)) {
      finalHeaders.set('Content-Type', 'application/json')
    }
    if (lang) finalHeaders.set('X-Lang', lang)
    const token = auth ? tokens.access : null
    if (token) finalHeaders.set('Authorization', `Bearer ${token}`)

    return fetch(`${BASE}${path}`, {
      ...rest,
      headers: finalHeaders,
      body:
        body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    })
  }

  let response = await send()

  // One transparent retry after refreshing an expired access token.
  if (response.status === 401 && auth && tokens.refresh) {
    if (await refreshAccessToken()) response = await send()
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload = text ? safeJson(text) : null

  if (!response.ok) {
    throw new ApiError(response.status, messageFrom(response.status, payload), payload)
  }
  return payload as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export const api = {
  get: <T>(path: string, options?: RequestOptions) => request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'PUT', body }),
  delete: <T>(path: string, options?: RequestOptions) =>
    request<T>(path, { ...options, method: 'DELETE' }),
}

/* ------------------------------------------------------------------ */
/* Typed endpoints — only what the frontend actually consumes today.   */
/* ------------------------------------------------------------------ */

export interface StackStatus {
  healthy: number
  total: number
  services: Record<string, { status: 'ok' | 'degraded' | 'down' }>
}

export interface EconomyRules {
  /** Always "direct", and always one level — kept on the wire so a client can
      tell a stale deployment from a current one rather than guessing. */
  programme: string
  levels: number
  sponsor_commission_pct: string
  leaders_pool_pct: string
  platform_pct: string
  basis: string
  marketplace: {
    markup_pct: string
    note: string
    worked_example: {
      seller_price: string
      customer_price: string
      kinjy_revenue: string
      sponsor_commission: string
      leaders_pool: string
    }
  }
  creator_revenue: { creator_pct: string; note: string }
  leaders: { pool_pct: string; pool_size: number; ranked_by: string; allocation: string }
  referral_pool: { entry_price: string; cap: number }
  escrow: {
    custodian: string
    licence: string
    auto_release_days: number
    note: string
  }
  payout: { threshold_usd: string; requires: string[] }
}

export interface SplitShare {
  beneficiary: string
  role: string
  amount: string
  reason: string
}

export interface Simulation {
  kind: string
  source: string
  basis: string
  distributed: string
  shares: SplitShare[]
}

export interface Algorithm {
  id: string
  name: string
  description: string
  builtin: boolean
  author_id: string | null
  installs: number
  weights: Record<string, number>
  filters: { topics: string | null; formats: string | null }
}

export interface FeedMode {
  id: string
  label: string
  ranked: boolean
  description: string
}

export interface AuthUser {
  id: string
  email: string
  handle: string
  display_name: string
  role: string
  lang: string
  kyc_verified: boolean
  referral_code: string
  status: string
  /** Null means never changed since the account was created. */
  password_changed_at: string | null
  /** False for an account that signs in with a passkey only. */
  has_password: boolean
}

export interface PasswordChangeResult {
  changed_at: string
  sessions_ended: number
  signed_out_here: boolean
}

export interface AuthResult {
  user: AuthUser
  tokens: { access_token: string; refresh_token: string; expires_in: number }
}

export interface Wallet {
  owner_id: string
  currency: string
  available: string
  pending: string
  lifetime_earned: string
  lifetime_paid: string
  payout_threshold: string
  payout_eligible: boolean
  blocked_by: string | null
}

export interface CommissionsPage {
  total: number
  /** { "marketplace_order": "12.40", "ad_purchase": "3.10", … }. There is one
      commission level, so the useful breakdown is by what generated it. */
  by_source: Record<string, string>
  items: Array<{
    id: string
    source_kind: string
    source_ref: string | null
    role: string
    amount: string
    status: string
    created_at: string
    /** The member whose payment earned it. */
    from_member_id?: string | null
    from_name?: string | null
  }>
}

export interface PayoutEligibility {
  eligible: boolean
  blocked_by: string | null
  accrued_usd: string
  threshold_usd: string
  kyc_verified: boolean
  wallet_on_file: boolean
  action_required: string | null
}

export interface KycStatus {
  year: number
  fee_paid: boolean
  status: 'unverified' | 'pending' | 'verified' | 'rejected'
  attempts_used: number
  attempts_allowed: number
  verified_on?: string | null
  expires_on?: string | null
  fee_usd: string
}

/**
 * Where commissions are paid: a BSC address, confirmed from the member's
 * e-mail, then approved by an admin (whose name is kept). A new address
 * starts both checks again.
 */
export type PayoutWalletStatus = 'pending_email' | 'pending_admin' | 'confirmed' | 'rejected'

export interface PayoutWallet {
  id: number
  address: string
  asset: string
  network: string
  status: PayoutWalletStatus
  email_confirmed_at?: string | null
  reviewed_by_name?: string | null
  reviewed_at?: string | null
  review_note?: string | null
  updated_at?: string | null
}

export interface PayoutWalletReview extends PayoutWallet {
  user_id: string
  display_name?: string | null
  handle?: string | null
  kyc_verified?: boolean | null
  reviewed_by?: string | null
}

export interface WithdrawQuote {
  available: string
  /** The smallest balance that can be withdrawn: Sonco Pay's minimum, plus the fee when the member carries it. */
  minimum: string
  fee: string | null
  fee_paid_by: 'member' | 'platform'
  you_receive: string | null
  asset: string
  network: string
  wallet: PayoutWallet | null
  eligible: boolean
  blocked_by: string[]
}

export interface Withdrawal {
  id: string
  amount: string
  fee: string
  net: string
  asset: string
  network: string
  address: string
  status: 'reserved' | 'submitted' | 'completed' | 'failed'
  tx_hash?: string | null
  failure_reason?: string | null
  created_at: string
  completed_at?: string | null
}

export interface PaymentIntentStatus {
  payment_id: string
  purpose: string
  reference?: string | null
  amount: string
  status: string
  settled: boolean
  /** The step Sonco Pay asks for next, when the payment is still open. */
  action?: PayAction | null
  /** A declined request (mobile money refused...): choose a method again. */
  retryable?: boolean
}

/** Paying on Kinjy's own page: what Sonco Pay offers for one payment. */
export interface PayMethods {
  status?: string
  mobile_money: Array<{
    code: string
    operator: string
    label?: string | null
    country: string
    prefix: string
    currency?: string | null
    estimated_pay_amount?: string | null
    estimated_payer_total?: string | null
    redirect?: boolean
    needs_operator?: boolean
  }>
  crypto: Array<{
    asset: string
    network: string
    network_label?: string | null
    label?: string | null
    pay_currency?: string | null
    estimated_pay_amount?: string | null
  }>
  other: Array<{ method: 'card' | 'binance_pay'; code?: string | null; label?: string | null }>
}

export interface PayAction {
  type: 'send_crypto' | 'approve_on_phone' | 'redirect' | 'open_wallet' | 'wait_for_confirmation' | 'choose_method'
  expires_at?: string | null
  asset?: string
  network?: string
  network_label?: string
  address?: string
  memo?: string | null
  amount?: string
  amount_rule?: string
  underpaid?: boolean
  qr_url?: string
  phone?: string
  operator?: string
  currency?: string
  instructions?: string | null
  url?: string
  retry?: boolean
  last_error?: string | null
}

export interface PayChoice {
  method: 'mobile_money' | 'crypto' | 'card' | 'binance_pay'
  code?: string
  asset?: string
  network?: string
  phone?: string
  country?: string
  return_path?: string
}

export interface DeviceSession {
  id: string
  device_label: string | null
  user_agent: string | null
  ip: string | null
  created_at: string
  last_seen_at: string
  expires_at: string
  revoked_at: string | null
}

export interface Passkey {
  id: string
  label: string
  transports: string | null
  created_at: string
  last_used_at: string | null
}

/** What anyone can see: `GET /users/{handle}`. */
export interface Profile {
  user_id: string
  handle: string
  display_name: string
  bio: string | null
  avatar_url: string | null
  cover_url: string | null
  /** ISO 3166 alpha-2, upper case. */
  country: string | null
  state: string | null
  city: string | null
  /** ISO 639 codes the member speaks, most fluent first, e.g. "fr,ln". */
  languages: string
  is_creator: boolean
  verified: boolean
  followers_count: number
  following_count: number
  created_at: string
}

/**
 * What only the owner sees: `GET /users/me`. The neighbourhood is a precise
 * location and never appears on the public profile.
 */
export interface MyProfile extends Profile {
  neighborhood: string | null
  /** Interface language: one Kinjy is translated into. */
  lang: string
}

/**
 * `PATCH /users/me`. Omit a field to leave it unchanged; `null` (or an empty
 * string) clears it. Images are referenced by the asset id returned by
 * `uploadProfileImage`, never by URL: the server refuses URLs.
 */
export interface ProfileUpdate {
  display_name?: string
  bio?: string | null
  avatar_asset_id?: string | null
  cover_asset_id?: string | null
  country?: string | null
  state?: string | null
  city?: string | null
  /** Adults only; refused (403) for younger members. */
  neighborhood?: string | null
  languages?: string
  lang?: string
}

/**
 * `GET /users/me/eligibility`: which age-gated fields the editor should offer.
 * A readout only; `PATCH /users/me` enforces the rule itself.
 */
export interface ProfileEligibility {
  neighborhood: boolean
}

/** The staff roles, narrowest first. */
export type StaffRole = 'moderator' | 'admin' | 'superadmin'

/** Whether an account's role is one of the staff's. */
export function isStaff(role: string | null | undefined): boolean {
  return role === 'moderator' || role === 'admin' || role === 'superadmin'
}

export interface PostMedia {
  /** Null when data saver withheld it — the bytes were never sent. */
  url: string | null
  /** Where an article places this file in its text. */
  media_id?: string
  /** A video's cover, signed like the video. 404 when there is none. */
  poster_url?: string | null
  kind: string
  alt_text: string | null
  deferred?: boolean
  /** Measured client-side at upload; lets the player frame a clip without a jump. */
  width?: number | null
  height?: number | null
  duration_seconds?: number | null
}

export interface WhyFactor {
  factor: string
  label: string
  contribution: number
}

export interface ReactionSummary {
  counts: Record<string, number>
  total: number
  /** The viewer's own reaction, or null. */
  mine: string | null
}

export interface PostAuthor {
  handle: string
  display_name: string
  avatar_url: string | null
  verified: boolean
}

export interface Post {
  /**
   * handle -> display name, for the @mentions in `body`.
   *
   * The body keeps the handle, because that is what the author typed and what
   * must survive an edit; this is what the reader is shown instead.
   */
  mentions?: Record<string, string>
  id: string
  author_id: string
  author: PostAuthor | null
  body: string
  format: string
  lang: string
  visibility: string
  circle_id: string | null
  community_id: string | null
  country: string | null
  city: string | null
  topics: string[]
  provenance: string
  series_id: string | null
  episode_number: number | null
  likes_count: number
  comments_count: number
  /** People the viewer follows who reacted or commented, plus a count of the rest. */
  known_actors?: KnownActors | null
  reposts_count: number
  views_count?: number
  /** The shared post, when this card is a repost. */
  repost_of?: Post | null
  reposted_by_me?: boolean
  created_at: string
  edited_at: string | null
  media: PostMedia[]
  reactions: ReactionSummary
  /** Author's own declaration; a minor's feed never carries these. */
  mature?: boolean
  /** True when the server trimmed this card for data saver. */
  data_saver?: boolean
  /** False when the member turned autoplay off. */
  autoplay?: boolean
  /** Present only on ranked feeds — the real score breakdown, not a blurb. */
  why: WhyFactor[] | null
}

export interface FeedPage {
  mode: string
  algorithm: string
  algorithm_name?: string
  ranked: boolean
  /**
   * On a date-ordered mode, the chosen algorithm's filter that still applied
   * (Video Only keeps videos in New too), or null when it filters nothing.
   */
  filtered?: { name: string; formats: string[]; topics: string[] } | null
  total_candidates?: number
  empty_reason?: string
  /**
   * The member's age could not be confirmed, so this feed was filtered as for
   * a minor and may be missing posts. Temporary; worth saying so on screen.
   */
  degraded?: boolean
  /** Whether asking for the next page is worth it. Absent on an older server. */
  has_more?: boolean
  items: Post[]
}

export interface WhyExplanation {
  post_id: string
  mode: string
  ranked: boolean
  /** What put this post in the feed, before any ranking. */
  reasons: Array<{ kind: string; label: string }>
  explanation?: string
  algorithm?: string
  algorithm_name?: string
  score?: number
  factors: WhyFactor[]
  actions: string[]
}

export interface UploadedMedia {
  id: string
  url: string
  kind: string
  content_type?: string
  filename?: string
  size_bytes: number
  provenance: string
  provenance_signed: boolean
  alt_text?: string | null
  deduplicated?: boolean
  /** Measured in the browser when the file is picked, so the feed can reserve its box. */
  width?: number
  height?: number
}

export interface LinkCard {
  url: string
  title: string
  description: string
  image: string | null
  site_name: string
  cached?: boolean
}

export interface KnownActors {
  people: Array<{
    id: string
    name: string
    handle: string | null
    avatar_url: string | null
    action: 'reacted' | 'commented'
  }>
  /** Everyone else who acted, so a post can say "and 40 others" without naming them. */
  others: number
  /** What those others did, so the line never has to say "reacted or commented". */
  others_action?: 'reacted' | 'commented' | 'both'
}

export interface NewPost {
  body: string
  format?: string
  visibility?: string
  circle_id?: string
  /** Required when visibility is 'community'; the server checks you are a member. */
  community_id?: string
  topics?: string[]
  lang?: string
  country?: string
  city?: string
  provenance?: string
  /** The author declaring adult content, so minors' feeds can exclude it. */
  mature?: boolean
  media?: Array<{
    media_id: string
    url: string
    kind: string
    alt_text?: string | null
    /** Measured from the file before upload; the reel frames the clip with these. */
    width?: number | null
    height?: number | null
    duration_seconds?: number | null
  }>
}

export interface WellbeingStatus {
  enabled: boolean
  minutes_today: number
  limit_minutes: number | null
  remaining_minutes: number | null
  over_limit: boolean
}

/** Someone who joined on this member's invitation. */
export interface Referral {
  user_id: string
  handle: string
  display_name: string
  joined_at: string
  kyc_verified: boolean
  status: string
}

/** What a member's own writing has done. */
export interface MyStats {
  posts: number
  posts_last_30_days: number
  reactions_received: number
  comments_received: number
  reposts_received: number
  views: number
  comments_written: number
  /** One row a day for the last 30 days, including the empty ones. */
  daily: Array<{ date: string; posts: number; reactions: number }>
  top_posts: Array<{
    id: string
    body: string
    created_at: string
    likes: number
    comments: number
    reposts: number
    views: number
  }>
}

/** One person in a "who reacted" list. */
export interface Reactor {
  user_id: string
  kind: string
  at: string
  handle: string | null
  display_name: string
  avatar_url: string | null
}

export interface CommentAuthor {
  id: string
  handle: string
  display_name: string
  avatar_url: string | null
}

export interface CommentNode {
  id: string
  author_id: string
  author: CommentAuthor | null
  parent_id: string | null
  /** 0 = top level. Capped server-side; deeper replies flatten and mention. */
  depth: number
  reply_to: string | null
  reply_to_user: CommentAuthor | null
  body: string
  lang: string
  created_at: string
  /** Set when the author fixed it, so the card can say "edited". */
  edited_at?: string | null
  /** Likes on this comment, and whether this viewer is one of them. */
  likes: number
  liked: boolean
  /** Stickers on this comment, by catalogue id, with how many people chose each. */
  stickers?: Record<string, number>
  /** The sticker this viewer chose, if they chose one rather than a plain like. */
  my_sticker?: string | null
}

export interface PersonSuggestion {
  user_id: string
  handle: string
  display_name: string
  bio: string | null
  avatar_url: string | null
  city: string | null
  country: string | null
  followers_count: number
  verified: boolean
  /**
   * Where the viewer already stands with this person, as the server knows it:
   * `none`, `invited` (we asked, they have not answered), `invites_you` (they
   * asked us) or `connected`.
   *
   * Optional so an older server simply reads as `none`.
   */
  connection?: 'none' | 'invited' | 'invites_you' | 'connected'
}

export interface Discoveries {
  reason: string
  communities: Array<{
    id: string
    slug: string
    name: string
    description: string
    kind: string
    members_count: number
  }>
  forums: Array<{ id: string; slug: string; name: string; hierarchy: string; threads_count: number }>
}

export interface ConnectionEntry {
  id: number
  user_id: string
  profile: { handle: string; display_name: string; avatar_url: string | null } | null
  status: string
  message: string | null
  created_at: string
}

export interface Permissions {
  connected: boolean
  pending?: boolean
  invited_by_me?: boolean
  can_message: boolean
  can_add_family: boolean
  can_add_community: boolean
  can_invite: boolean
  reason: string
  /** Whether the viewer already follows them. Only on `/connections/with`. */
  following?: boolean
  /** Whether they follow the viewer. */
  follows_you?: boolean
}

export interface Privacy {
  who_can_invite: string
  who_can_message: string
  who_can_add_family: string
  who_can_add_community: string
  discoverable: boolean
}

/**
 * What a smart circle is made of: a relationship, optionally narrowed to where
 * people live. Evaluated on the server at every read — it holds no list.
 */
export interface CircleRule {
  source: 'connections' | 'followers' | 'following' | 'mutuals'
  country?: string | null
  city?: string | null
}

export interface Circle {
  id: string
  owner_id?: string
  name: string
  kind: string
  color?: string | null
  rule?: CircleRule | null
  /** Who a post shared to it would reach right now. */
  members_count: number
  created_at?: string
}

export interface CircleMember {
  user_id: string
  handle: string | null
  display_name: string | null
  avatar_url: string | null
  city: string | null
  verified: boolean
  /** False when the circle can no longer reach them (a block, or the age rule). */
  active: boolean
}

export interface CircleDetail extends Circle {
  members: CircleMember[]
}

export interface Community {
  id: string
  slug: string
  name: string
  description: string
  kind: string
  price_usd: string | null
  members_count: number
  country: string | null
  city: string | null
  avatar_url?: string | null
  cover_url?: string | null
  /** The viewer's role when they are a member (member|moderator|owner), else null. */
  my_role?: string | null
  /** pending (request sent), active, banned - or null when not a member. */
  my_status?: string | null
}

export interface Forum {
  id: string
  slug: string
  name: string
  hierarchy: string
  scope: string | null
  scope_value: string | null
  parent_id: string | null
  threads_count: number
}

export type ThreadSort = 'recent' | 'trending' | 'solved' | 'unanswered'

export interface Thread {
  id: string
  title: string
  author_id: string
  author?: PersonBrief | null
  /** At least one reply has been accepted as the answer. */
  solved?: boolean
  replies_count: number
  views_count: number
  pinned: boolean
  ai_summary: string | null
  last_activity_at: string
  created_at?: string
}

export interface KnowledgeEntry {
  id: string
  /** `community`: an answer the asker accepted and members upvoted. `curated`: written down by a reviewer. */
  origin: 'community' | 'curated'
  question: string
  answer: string
  lang: string
  confidence: number | null
  votes: number
  /** The threads this came from. */
  sources: string[]
  reviewed: boolean
}

export interface ThreadDetail {
  id: string
  forum_id: string
  title: string
  body: string
  author_id: string
  author?: PersonBrief | null
  lang: string
  ai_summary: string | null
  locked: boolean
  created_at?: string
  replies: Array<{
    id: string
    author_id: string
    author?: PersonBrief | null
    parent_id?: string | null
    body: string
    upvotes: number
    /** Whether the signed-in viewer has upvoted this reply. */
    voted_by_me?: boolean
    accepted_answer?: boolean
    created_at: string
  }>
}

export interface PersonBrief {
  handle: string
  display_name: string
  avatar_url?: string | null
  verified?: boolean
  city?: string | null
}

/** Somebody in a group, with what they may do in it. */
export interface GroupMember {
  user_id: string
  role: 'owner' | 'admin' | 'member'
  joined_at: string
  handle: string | null
  display_name: string
  avatar_url: string | null
}

export interface Conversation {
  id: string
  kind: string
  title: string | null
  /** Groups only: what the room is for. */
  description?: string | null
  /** A group's own photo and banner. */
  avatar_url?: string | null
  cover_url?: string | null
  /** The one room everybody is in. It cannot be left behind by somebody else. */
  is_welcome?: boolean
  /** What the viewer may do here: owner and admin run it, member does not. */
  my_role?: 'owner' | 'admin' | 'member'
  /** Groups: what the share link does. Public lets people in, private asks an admin. */
  privacy?: 'public' | 'private' | null
  /** Groups: whether members added later read the earlier messages. */
  history_for_new?: 'all' | 'from_join' | null
  /** Pinned to the top of the viewer's list. */
  pinned?: boolean
  /** False while this is a request the viewer has not answered. */
  accepted?: boolean
  encrypted: boolean
  last_message_at: string
  /**
   * Everybody in a direct conversation; only a few faces of a group.
   *
   * The welcome group holds the whole platform, so a list that carried every
   * member of every room carried most of the platform on every poll. Use
   * `participant_count` for the size, and the thread's own `profiles` to name
   * whoever actually wrote something.
   */
  participants: string[]
  /** How many people are really in it, however few are listed above. */
  participant_count?: number
  /** Resolved by messaging-service so the list can show people, not ids. */
  profiles?: Record<string, PersonBrief>
  unread?: number
  /** Each participant's last read time — what "Seen" is drawn from. */
  read_state?: Record<string, string | null>
  /** Stored encrypted on the server (at rest) — not end to end. */
  sealed_at_rest?: boolean
  last_message?: {
    sender_id: string
    /** Text, or "Sent a photo: …" for an attachment. Never ciphertext. */
    preview: string
    media_kind: string | null
    created_at: string
  } | null
}

export interface Presence {
  online: boolean
  /** Unknown (null) after a server restart — never a guessed time. */
  last_seen: string | null
}

export interface Message {
  id: string
  sender_id: string
  encrypted: boolean
  ciphertext_b64: string | null
  body: string | null
  kind: string
  media_url?: string | null
  /** image | video | audio | document | file — as media-service classified it. */
  media_kind?: string | null
  media_name?: string | null
  media_type?: string | null
  media_size?: number | null
  /** Held back from this reader (a minor) until they accept the group. */
  media_withheld?: boolean
  /** The message this one answers; the quote itself is built client-side. */
  reply_to_id?: string | null
  /** True when the original has expired or been deleted. Its content is never sent. */
  reply_to_deleted?: boolean
  /** For kind "sticker": the catalogue id, and nothing else. */
  sticker_id?: string | null
  /** Resolved by the server from its catalogue; a message only stores the id. */
  sticker?: Sticker | null
  /** Set when the text has been changed. Shown, never silent. */
  edited_at?: string | null
  /** The message was deleted: it keeps its place and has lost its words. */
  deleted?: boolean
  /** Emoji to count, across everyone in the room. */
  reactions?: Record<string, number>
  /** Which one is mine, if any. */
  my_reaction?: string | null
  created_at: string
}

export interface Sticker {
  id: string
  glyph: string
  label: string
  pack?: string
  /** Real artwork when there is some; the client draws the glyph large when not. */
  image_url: string | null
}

export interface StickerPack {
  id: string
  name: string
  stickers: Sticker[]
}

/** The reactions a message may carry. The server refuses anything else. */
export const MESSAGE_REACTIONS = ['👍', '👎', '❤️', '😂', '😮', '😢', '🙏', '🔥'] as const

export interface Person {
  id: string
  user_id?: string | null
  given_name: string
  family_name?: string | null
  gender?: string | null
  birth_date?: string | null
  death_date?: string | null
  deceased?: boolean
  photo_url?: string | null
  status?: string
  confirmations?: number
}

/** One person in full, with what the caller may do about them (decided by the server). */
export interface PersonDetail extends Person {
  other_names: string | null
  birth_place: string | null
  death_place: string | null
  biography: string | null
  is_me: boolean
  /** Derived from the graph, relative to the caller's own node; null when they are not in this family. */
  relation_to_me: string | null
  counts: { parents: number; children: number; spouses: number; siblings: number }
  my_decision: 'confirm' | 'dispute' | null
  permissions: { can_edit: boolean; can_delete: boolean; can_link: boolean; can_confirm: boolean }
}

/** What may be sent for a person. `null` clears a field; leaving it out leaves it alone. */
export interface PersonFields {
  given_name: string
  family_name?: string | null
  other_names?: string | null
  gender?: string | null
  birth_date?: string | null
  birth_place?: string | null
  death_date?: string | null
  death_place?: string | null
  deceased?: boolean
  biography?: string | null
}

export type RelativeKind = 'parent' | 'adoptive_parent' | 'child' | 'spouse' | 'sibling'

/** What a parent is to a child. A property of the link; never worked out from anyone's gender. */
export type ParentRole = 'father' | 'mother'

/** What the extra questions of "add a relative" send. Each applies to one kind of relative. */
export interface RelativeExtras {
  /** parent / adoptive_parent: what the new person is to this one. */
  role?: ParentRole
  /** child: what this person is to the child, who the other parent is, and what they are. */
  anchor_role?: ParentRole
  other_parent_id?: string
  other_parent_role?: ParentRole
  /** sibling: the parents of this person who are also the new person's. */
  shared_parent_ids?: string[]
}

export interface FamilyTree {
  root: string
  depth: number
  /** The caller's own node, if they have added themselves. */
  me: string | null
  /** Everyone in this family; the view may hold fewer. */
  family_size: number
  /** True when someone shown has parents or children that are not. */
  truncated: boolean
  nodes: Array<{
    person: Person
    /** 0 = the root, positive = ancestors, negative = descendants. */
    level: number
    relation: string
    closeness: number
    sibling_kind: string | null
    /** More of the family lies beyond this person. */
    more: boolean
    mine: boolean
    editable: boolean
  }>
  edges: Array<{ id: string; from: string; to: string; kind: string; role: ParentRole | null; status: string; removable: boolean }>
}

export interface HowRelated {
  related: boolean
  relation: string
  steps?: number
  /** How much of the chain is corroborated — a path is as good as its weakest link. */
  verified_links?: number
  total_links?: number
  path: Array<{
    person_id: string
    name: string | null
    /** The stored edge kind. */
    step: string
    /** The derived relation to the person the path started from. */
    relation?: string
  }>
  common_ancestors?: Array<{ person_id: string; name: string | null }>
}

export interface Memorial {
  id: string
  full_name: string
  birth_date: string | null
  death_date: string | null
  biography: string | null
  /** Signed for this viewer, short-lived: never store or share them. */
  photo_url: string | null
  cover_url: string | null
  faith_style: string
  faith_style_source: string | null
  audio: { url: string | null; autoplay: boolean }
  qr_code: string
  qr_url: string
  visibility: 'public' | 'private'
  moderation: 'open' | 'pending_approval'
  /** unconfirmed → reported → under_review → verified */
  death_status: string
  verified_at: string | null
  tribute_counts: Record<string, number>
  /** Whether the viewer administers it. */
  is_admin: boolean
  /** Administrators only. */
  pending_tributes?: number
  /** Administrators only: 1 is the first in the succession, who alone may delete it. */
  admin_rank?: number
  grave: {
    lat: number | null
    lng: number | null
    label: string | null
    verified: boolean
    /** What the device said when it was captured at the grave, in metres. */
    accuracy_m: number | null
  }
  created_at: string
}

export interface Tribute {
  id: string
  kind: string
  author_name: string
  body: string | null
  media_url: string | null
  created_at: string
  status?: string
}

/** One photo or video in a memorial's gallery. The url is a ticket: short-lived, never to be stored. */
export interface GalleryItem {
  id: string
  kind: 'image' | 'video'
  url: string
  caption: string | null
  /** original | edited | ai_assisted | ai_generated | verified_source */
  provenance: string
  /** The family's call: blurred until the visitor chooses to look. */
  sensitive: boolean
  position: number
  size_bytes: number
  /** A visitor's tribute photo the family promoted; it still belongs to the tribute. */
  from_tribute: boolean
}

export interface Gallery {
  limit: number
  max_bytes: number
  /** Administrators only. */
  bytes_used?: number
  items: GalleryItem[]
}

export interface MemorialEvent {
  id: string
  year: number
  month: number | null
  day: number | null
  title: string
  body: string | null
}

export interface MemorialAdmin {
  user_id: string
  succession_order: number
  handle: string | null
  display_name: string | null
  avatar_url: string | null
}

export interface DeathReviewItem {
  id: string
  full_name: string
  death_date: string | null
  death_status: string
  qr_code: string
  reports: Array<{ id: number; evidence: string; document_url: string | null; reported_by: string; created_at: string }>
}

export interface Product {
  id: string
  title: string
  description: string
  kind: string
  vendor_id: string
  customer_price: string
  currency: string
  country: string | null
  city: string | null
  images: string[]
  vendor?: VendorBrief
  rating_average?: number | null
  rating_count?: number
}

export interface VendorBrief {
  user_id: string
  handle: string | null
  display_name: string | null
  avatar_url: string | null
}

export interface ProductPricing {
  vendor_price: string
  margin: string
  customer_price: string
  markup_pct: string
}

export interface VendorStats {
  listings_active: number
  sales_settled: number
}

/** Owners also get the full price split; everyone else only customer_price. */
export interface ProductDetail {
  id: string
  title: string
  description: string
  kind: string
  stock: number | null
  country: string | null
  city: string | null
  images: string[]
  currency: string
  /** active | paused | removed */
  status: string
  created_at: string
  vendor_id: string
  vendor: VendorBrief
  vendor_since: string | null
  vendor_stats: VendorStats
  is_owner: boolean
  customer_price: string
  vendor_price?: string
  margin?: string
  markup_pct?: string
}

export interface MyProduct extends ProductPricing {
  id: string
  title: string
  description: string
  kind: string
  stock: number | null
  status: string
  currency: string
  country: string | null
  city: string | null
  images: string[]
  created_at: string
}

export interface ProductPatch {
  title?: string
  description?: string
  vendor_price?: string
  stock?: number | null
  country?: string | null
  city?: string | null
  images?: string[]
  status?: 'active' | 'paused'
}

export interface Sale extends Order {
  product_title: string | null
  buyer: VendorBrief
  /** In escrow and not yet handed over. */
  to_ship: boolean
}

export interface SellerFinances {
  currency: string
  escrow_pending: string
  released: string
  refunded: string
  counts: { escrow_pending: number; to_ship: number; settled: number; refunded: number; disputed: number }
}

export interface StorefrontResponse {
  vendor: VendorBrief
  since: string | null
  stats: VendorStats
  total: number
  items: Product[]
}

export interface OrderProduct {
  id: string
  title: string
  description: string | null
  kind: string
  images: string[]
  country: string | null
  city: string | null
  /** False when the seller removed or paused the listing; the buyer still sees what they bought. */
  available: boolean
}

export interface OrderCounterpart {
  user_id: string
  handle: string | null
  display_name: string | null
  avatar_url: string | null
}

export interface Order {
  id: string
  role: string | null
  product?: OrderProduct | null
  /** The other party: the seller for a buyer, the buyer for a seller. */
  counterpart?: OrderCounterpart | null
  /** The viewer already reviewed this order. */
  reviewed?: boolean
  product_id: string
  quantity: number
  customer_price: string
  vendor_price: string
  margin: string
  refunded_amount: string
  /** pending | in_escrow | delivered | settled | part_refunded | refunded | disputed | cancelled */
  status: string
  custodian: string | null
  custodian_name?: string
  escrow_funded_at: string | null
  delivered_at: string | null
  delivery_note: string | null
  dispute_window_ends: string | null
  escrow_released_at: string | null
  dispute_id?: string | null
  /** Only present for the buyer and the vendor of the order. */
  shipping?: OrderShipping | null
  carrier?: string | null
  tracking_number?: string | null
  tracking_url?: string | null
  created_at: string
}

export interface OrderPayment {
  payment_id: string
  rail: string
  /** True when no live payment rail is configured: nothing real is charged. */
  mock: boolean
  amount: string
  pay_address?: string | null
  pay_currency?: string | null
  checkout_url?: string | null
  status: string
}

export interface OrderShipping {
  full_name: string
  line1: string
  line2?: string | null
  city: string
  region?: string | null
  postal_code?: string | null
  /** ISO 3166-1 alpha-2 */
  country: string
  phone?: string | null
}

export interface ProductReview {
  id: string
  rating: number
  comment: string | null
  created_at: string
  author_id: string
  reviewer?: { id?: string; handle?: string; display_name?: string; avatar_url?: string | null } | null
}

export interface ReviewsSummary {
  average: number
  count: number
  /** Review counts keyed '1'..'5'. */
  distribution: Record<string, number>
  items: ProductReview[]
}

export interface EscrowTerms {
  custodian: string
  provider: string
  licence: string
  holds_funds: string
  released_when: string
  auto_release_days: number
  dispute_response_days: number
  arbitration_days: number
  outcomes: string[]
}

export interface DisputeMessage {
  author_id: string
  author_role: 'buyer' | 'seller' | 'kinjy'
  body: string
  evidence: string[]
  created_at: string
  author?: DisputeParty['profile']
}

export interface DisputeParty {
  id: string
  profile: { id?: string; handle?: string; display_name?: string; avatar_url?: string | null } | null
}

export interface Dispute {
  id: string
  order_id: string
  opened_by: string
  against: string
  role: 'buyer' | 'seller'
  category: string
  reason: string
  amount_claimed: string
  /** open | answered | arbitration | resolved | withdrawn */
  status: string
  outcome: string | null
  refund_amount: string
  resolution_note: string | null
  respond_by: string | null
  arbitrate_by: string | null
  resolved_at: string | null
  created_at: string
  messages?: DisputeMessage[]
  /** Detail-only fields, from GET /commerce/disputes/{id}. */
  viewer_role?: 'buyer' | 'seller' | 'admin'
  can_reply?: boolean
  can_withdraw?: boolean
  can_concede?: boolean
  seconds_left_to_respond?: number | null
  seconds_left_to_arbitrate?: number | null
  buyer?: DisputeParty
  seller?: DisputeParty
  order?: {
    id: string
    product_id: string
    title: string | null
    quantity: number
    customer_price: string
    currency: string
    status: string
  }
}

export interface LeaderStanding {
  rank: number
  member_id: string
  direct_commissions: string
  share_ratio: string
  projected_payout: string
}

export interface LeaderBoard {
  period: string
  pool_amount: string
  pool_pct: string
  pool_size: number
  qualifying: number
  items: LeaderStanding[]
}

export interface MyLeaderStanding {
  period: string
  direct_commissions: string
  qualifying: boolean
  rank: number | null
  share_ratio: string | null
  projected_payout: string
  pool_amount: string
  pool_size: number
}

export interface ReferralPoolState {
  entry_price: string
  currency: string
  cap: number
  seats_taken: number
  seats_left: number
  open: boolean
  active_seats: number
  my_seat: {
    seat_id: string
    seat_number: number
    status: string
    assigned_count: number
    last_assigned_at: string | null
    joined_at: string
  } | null
  note: string
}

/* --- WebAuthn wire format ------------------------------------------------ */
/* The server speaks base64url JSON; the browser API speaks ArrayBuffers. These
   translate between the two — the only place in the app that needs to.        */

export interface PublicKeyCredentialCreationOptionsJSON {
  challenge: string
  rp: { id: string; name: string }
  user: { id: string; name: string; displayName: string }
  pubKeyCredParams: Array<{ type: 'public-key'; alg: number }>
  timeout?: number
  excludeCredentials?: Array<{ id: string; type: 'public-key'; transports?: string[] }>
  authenticatorSelection?: Record<string, unknown>
  attestation?: string
}

export interface PublicKeyCredentialRequestOptionsJSON {
  challenge: string
  timeout?: number
  rpId?: string
  allowCredentials?: Array<{ id: string; type: 'public-key'; transports?: string[] }>
  userVerification?: string
}

function fromB64Url(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4))
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

function toB64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function parseCreationOptions(
  options: PublicKeyCredentialCreationOptionsJSON,
): PublicKeyCredentialCreationOptions {
  return {
    ...options,
    challenge: fromB64Url(options.challenge),
    user: { ...options.user, id: fromB64Url(options.user.id) },
    excludeCredentials: options.excludeCredentials?.map((c) => ({
      ...c,
      id: fromB64Url(c.id),
      transports: c.transports as AuthenticatorTransport[] | undefined,
    })),
  } as PublicKeyCredentialCreationOptions
}

function parseRequestOptions(
  options: PublicKeyCredentialRequestOptionsJSON,
): PublicKeyCredentialRequestOptions {
  return {
    ...options,
    challenge: fromB64Url(options.challenge),
    allowCredentials: options.allowCredentials?.map((c) => ({
      ...c,
      id: fromB64Url(c.id),
      transports: c.transports as AuthenticatorTransport[] | undefined,
    })),
  } as PublicKeyCredentialRequestOptions
}

function serializeCredential(credential: PublicKeyCredential): Record<string, unknown> {
  const response = credential.response as AuthenticatorAttestationResponse &
    AuthenticatorAssertionResponse
  const payload: Record<string, unknown> = {
    id: credential.id,
    rawId: toB64Url(credential.rawId),
    type: credential.type,
    clientExtensionResults: credential.getClientExtensionResults(),
    response: {
      clientDataJSON: toB64Url(response.clientDataJSON),
    } as Record<string, unknown>,
  }
  const inner = payload.response as Record<string, unknown>
  if (response.attestationObject) inner.attestationObject = toB64Url(response.attestationObject)
  if (response.authenticatorData) inner.authenticatorData = toB64Url(response.authenticatorData)
  if (response.signature) inner.signature = toB64Url(response.signature)
  if (response.userHandle) inner.userHandle = toB64Url(response.userHandle)
  return payload
}

/** Whether this browser can do passkeys at all. */
export const passkeysSupported = () =>
  typeof window !== 'undefined' && Boolean(window.PublicKeyCredential)

export interface SupervisionDisclosure {
  can_see: string[]
  cannot_see: string[]
  can_do: string[]
  cannot_do: string[]
  note: string
}

export interface SupervisionLink {
  id: string
  role: 'parent' | 'teen'
  parent_id: string
  teen_id: string
  status: 'invited' | 'active' | 'declined' | 'ended'
  invited_by: string
  created_at: string
  accepted_at: string | null
  ended_at: string | null
  ended_by: string | null
}

export interface SupervisionRequest {
  id: string
  setting: string
  requested_value: string
  status: 'pending' | 'approved' | 'declined'
  created_at: string
  answered_at?: string | null
  role: 'parent' | 'teen'
}

export interface SupervisionState {
  items: SupervisionLink[]
  requests: SupervisionRequest[]
  disclosure: SupervisionDisclosure
}

export interface SupervisedView {
  supervision: SupervisionLink
  settings: Record<string, unknown>
  time: { daily_limit_minutes: number | null; minutes_today: number }
  requests: Omit<SupervisionRequest, 'role'>[]
  not_included: string[]
}

export interface ModerationOverview {
  classified_total: number
  pending_review: number
  child_safety_escalations: number
  reports_24h: number
  reports_total: number
  appeals: {
    open: number
    overdue: number
    answered: number
    overturned: number
    overturn_rate: number | null
  }
}

export interface ReviewItem {
  content_id: string
  content_kind: string
  age_rating: string
  classifier_source: string
  confidence: number
  exploitation_risk: number
  levels: Record<string, number>
  body: string | null
  author_id: string | null
  media_count: number
  reports: number
  created_at: string
}

export interface AppealItem {
  id: string
  decision_id: string
  appellant_id: string
  grounds: string | null
  due_at: string
  overdue: boolean
  content_id: string | null
  content_kind: string | null
  action: string | null
  age_rating: string | null
  body_snapshot: string | null
  decided_by: string | null
}

export interface MyDecision {
  id: string
  content_id: string
  content_kind: string
  action: 'refused_publication' | 'restricted_by_rating' | 'restricted_by_reports' | 'human_review'
  age_rating: string
  decided_by: 'automatic' | 'a reviewer'
  appealable: boolean
  created_at: string
  appeal: {
    id: string
    status: 'open' | 'upheld' | 'overturned'
    created_at: string
    answered_at: string | null
    reviewer_note: string | null
  } | null
}

export const kaluta = {
  status: () => api.get<StackStatus>('/status', { auth: false }),

  economy: {
    rules: () => api.get<EconomyRules>('/ledger/rules', { auth: false }),
    simulate: (kind: Simulation['kind'], amount: string | number) =>
      api.post<Simulation>('/ledger/simulate', { kind, amount: String(amount) }, { auth: false }),
    marketplacePricing: (vendorPrice: string | number) =>
      api.get<Record<string, string>>(`/commerce/pricing?vendor_price=${vendorPrice}`, { auth: false }),
  },

  calls: {
    /**
     * Relay one WebRTC signal. The media itself never touches the server —
     * this only introduces the two browsers to each other.
     *
     * Without a TURN relay a call between peers behind symmetric NATs will not
     * connect; the UI must say so rather than spin.
     */
    signal: (input: {
      conversation_id: string
      signal: 'offer' | 'answer' | 'candidate' | 'hangup' | 'reject' | 'busy'
      payload?: Record<string, unknown>
      media?: 'audio' | 'video'
    }) => api.post<{ delivered: number; reachable: boolean }>('/calls/signal', input),
  },

  notifications: {
    list: (unreadOnly = false) =>
      api.get<{
        unread: number
        items: Array<{
          id: number
          kind: string
          title: string
          body: string | null
          link: string | null
          read: boolean
          created_at: string
        }>
      }>(`/notifications${unreadOnly ? '?unread_only=true' : ''}`),
    markRead: () => api.post<{ read: boolean }>('/notifications/read'),
  },

  creators: {
    /**
     * One-to-Many (blueprint §Creator Studio): one idea, several formats.
     *
     * Targets that need a media pipeline come back `unsupported` with the
     * reason rather than sitting queued forever, so the UI can say why.
     */
    publish: (input: {
      source_text: string
      source_lang: string
      targets: string[]
      target_langs?: string[]
      source_ref?: string
    }) =>
      api.post<{
        job_id: string
        status: string
        outputs: number
        ready: number
        unsupported: string[]
      }>('/creators/publish', { source_kind: 'text', ...input }),

    job: (jobId: string) =>
      api.get<{
        job_id: string
        status: string
        outputs: Array<{
          target: string
          lang: string
          status: string
          content: string | null
          media_url: string | null
          provenance: string
          error: string | null
        }>
      }>(`/creators/publish/${jobId}`),

    enable: () => api.post<{ enabled: boolean }>('/creators/enable'),
  },

  shorts: {
    /** The vertical reel — posts published as shorts, newest first. */
    page: (params: { limit?: number; offset?: number; author?: string } = {}) => {
      const query = new URLSearchParams()
      query.set('limit', String(params.limit ?? 12))
      if (params.offset) query.set('offset', String(params.offset))
      if (params.author) query.set('author', params.author)
      return api.get<{
        items: Post[]
        has_more: boolean
        applied_settings: { age_mode?: string; autoplay_media?: boolean; degraded?: boolean }
      }>(`/shorts?${query}`)
    },
  },

  people: {
    /**
     * Find someone to message. Honours their `discoverable` setting, so a
     * member who opted out simply does not appear.
     */
    search: (q: string, limit = 10) =>
      api.get<{ items: Array<PersonBrief & { user_id: string }> }>(
        `/users/search?q=${encodeURIComponent(q)}&limit=${limit}`,
      ),
  },

  stats: {
    /** This member's own numbers. The server refuses anybody else's. */
    me: () => api.get<MyStats>('/stats/me'),
  },

  feeds: {
    modes: () => api.get<{ modes: FeedMode[] }>('/feed/modes', { auth: false }),
    algorithms: () => api.get<{ items: Algorithm[] }>('/algorithms', { auth: false }),

    page: (
      params: {
        mode: string
        algorithm_id?: string
        city?: string
        country?: string
        topic?: string
        limit?: number
        offset?: number
      },
      options: { signal?: AbortSignal } = {},
    ) => {
      const query = new URLSearchParams({ mode: params.mode })
      if (params.algorithm_id) query.set('algorithm_id', params.algorithm_id)
      if (params.city) query.set('city', params.city)
      if (params.country) query.set('country', params.country)
      if (params.topic) query.set('topic', params.topic)
      query.set('limit', String(params.limit ?? 20))
      if (params.offset) query.set('offset', String(params.offset))
      return api.get<FeedPage>(`/feed?${query}`, { signal: options.signal })
    },

    /** The score breakdown for one post, for this viewer, under this algorithm. */
    why: (postId: string, algorithmId: string, mode = 'new') =>
      api.get<WhyExplanation>(
        `/feed/why/${postId}?algorithm_id=${algorithmId}&mode=${encodeURIComponent(mode)}`,
      ),

    /** "Show less like this" — stored and honoured on the next feed build. */
    signal: (input: { kind: string; target_type: string; target_id: string }) =>
      api.post<{ recorded: boolean; applies_from: string }>('/feed/signals', input),
  },

  posts: {
    create: (post: NewPost) => api.post<Post>('/posts', post),
    get: (id: string) => api.get<Post>(`/posts/${id}`),
    /**
     * The card for a link in a post. Read by the server, not the browser: a
     * page may be unreachable from the member's network, and having every
     * reader fetch whatever a post links to points a crowd at someone's site.
     */
    linkPreview: (url: string) =>
      api.get<LinkCard>(`/link-preview?url=${encodeURIComponent(url)}`),
    /** Ask for media data saver withheld — "load it anyway", for this post only. */
    media: (id: string) => api.get<{ post_id: string; media: PostMedia[] }>(`/posts/${id}/media`),
    byAuthor: (userId: string, limit = 20) =>
      api.get<{ total: number; items: Post[] }>(`/posts/by/${userId}?limit=${limit}`),
    remove: (id: string) => api.delete<void>(`/posts/${id}`),
    /** Share to your own audience. A body makes it a quote rather than a plain share. */
    /** Fix your own post, within its first 24 hours. */
    /**
     * Fix a post within its first 24 hours. For an article, `media` names the
     * files newly placed in its text (already uploaded); sending it also tells
     * the server to detach the ones taken out of the text.
     */
    edit: (postId: string, body: string, media?: Array<{ media_id: string; url: string; kind: string }>) =>
      api.patch<Post>(`/posts/${postId}`, media ? { body, media } : { body }),
    /** Fix a comment, within its first 24 hours. */
    editComment: (commentId: string, body: string) =>
      api.patch<{ id: string; body: string; edited_at: string }>(`/comments/${commentId}`, { body }),
    /** Remove a comment. The author's own, or any on a post you wrote. */
    deleteComment: (commentId: string) => api.delete<void>(`/comments/${commentId}`),
    /**
     * Answer a comment with a like or a sticker, or take it back. One call
     * either way; the server answers with the new state.
     *
     * One reaction per person per comment, so sending a different sticker
     * replaces the one before it rather than adding to it.
     */
    likeComment: (commentId: string, stickerId: string | null = null) =>
      api.post<{
        liked: boolean
        likes: number
        stickers: Record<string, number>
        my_sticker: string | null
      }>(`/comments/${commentId}/reaction`, { sticker_id: stickerId }),
    repost: (id: string, body = '') => api.post<Post>(`/posts/${id}/repost`, { body }),
    undoRepost: (id: string) => api.delete<void>(`/posts/${id}/repost`),
    /**
     * Report posts that were actually on screen. Batched, and deduplicated by
     * the server, so the figure means "people who saw this".
     */
    views: (postIds: string[]) =>
      api.post<{ recorded: number; counts: Record<string, number> }>('/posts/views', {
        post_ids: postIds,
      }),
    like: (id: string) => api.post<{ liked: boolean; likes_count: number }>(`/posts/${id}/like`),
    /** Set, switch or clear a reaction. Tapping the current one clears it. */
    react: (id: string, kind: string) =>
      api.post<{ post_id: string; reactions: ReactionSummary }>(`/posts/${id}/react`, { kind }),
    /** Who reacted to a post. The server allows this to its author only. */
    reactionPeople: (id: string) =>
      api.get<{ total: number; items: Reactor[] }>(`/posts/${id}/reactions/people`),
    /** Who liked a comment. Its author only, likewise. */
    commentLikePeople: (id: string) =>
      api.get<{ total: number; items: Reactor[] }>(`/comments/${id}/likes`),
    comments: (id: string) =>
      api.get<{ max_depth: number; mentions?: Record<string, string>; items: CommentNode[] }>(
        `/posts/${id}/comments`,
      ),
    comment: (id: string, body: string, parentId?: string) =>
      api.post<{
        id: string
        created_at: string
        depth: number
        parent_id: string | null
        reply_to: string | null
        /** Names for the @mentions in what was just written. */
        mentions?: Record<string, string>
      }>(`/posts/${id}/comments`, { body, parent_id: parentId }),
  },

  media: {
    /**
     * Which of these files the signed-in member may put in a post of their
     * own - the same rule the server applies when the post is saved. Asked by
     * the article editor before it pastes an article copied from Kinjy, so
     * the writer's own pictures come along and anybody else's are left out.
     * Ids that are not the caller's are simply absent from the answer.
     */
    attachable: (ids: string[]) =>
      api.post<{ attachable: Array<{ id: string; kind: string }> }>('/media/attachable', { ids }),
    /**
     * Copy a picture from a web address into the member's own post media -
     * for an article pasted from another site. The server fetches it (public
     * addresses only, images only) and stores it like an upload; the preview
     * address is signed for the author.
     */
    importUrl: (url: string, altText?: string) =>
      api.post<UploadedMedia & { preview_url: string }>('/media/import-url', {
        url,
        alt_text: altText || undefined,
      }),
    /**
     * Upload a file and get back its public URL.
     *
     * The provenance label travels with the upload, not with the post: the
     * asset keeps its own label so a photo re-used elsewhere stays labelled.
     */
    async upload(file: File, provenance = 'original', altText?: string): Promise<UploadedMedia> {
      const form = new FormData()
      form.append('file', file)
      form.append('provenance', provenance)
      if (altText) form.append('alt_text', altText)
      return api.post<UploadedMedia>('/media/upload', form)
    },

    /**
     * Upload with real progress.
     *
     * fetch() cannot report how much of a request body has been sent, so a
     * "uploading…" spinner on a 40 MB clip tells the member nothing for a
     * minute. XHR can, and a video upload is exactly where that matters.
     */
    uploadWithProgress(
      file: File,
      onProgress: (fraction: number) => void,
      options: {
        provenance?: string
        altText?: string
        signal?: AbortSignal
        /**
         * 'chat' accepts any file type; a post only formats every browser renders.
         * 'memorial' is a gallery photo or video: JPEG/PNG/WebP up to 25 MB, MP4/WebM up
         * to 50 MB, and the bytes must be what the type says.
         */
        purpose?: 'post' | 'chat' | 'memorial'
      } = {},
    ): Promise<UploadedMedia> {
      const form = new FormData()
      form.append('file', file)
      form.append('provenance', options.provenance ?? 'original')
      form.append('purpose', options.purpose ?? 'post')
      if (options.altText) form.append('alt_text', options.altText)

      // XHR bypasses request(), so it also bypasses its refresh-on-401: an
      // upload started after the access token expired would simply fail.
      const attempt = (retried: boolean): Promise<UploadedMedia> => new Promise<UploadedMedia>((resolve, reject) => {
        const request = new XMLHttpRequest()
        request.open('POST', `${BASE}/media/upload`)
        const token = tokens.access
        if (token) request.setRequestHeader('Authorization', `Bearer ${token}`)

        request.upload.onprogress = (event) => {
          if (event.lengthComputable) onProgress(event.loaded / event.total)
        }
        request.onload = () => {
          let payload: unknown = null
          try {
            payload = JSON.parse(request.responseText)
          } catch {
            payload = null
          }
          if (request.status >= 200 && request.status < 300) {
            onProgress(1)
            resolve(payload as UploadedMedia)
          } else if (request.status === 401 && !retried && tokens.refresh) {
            onProgress(0)
            void refreshAccessToken().then((ok) =>
              ok ? attempt(true).then(resolve, reject) : reject(new ApiError(401, 'Your session expired — sign in again')),
            )
          } else {
            const detail = (payload as { detail?: string } | null)?.detail
            reject(new ApiError(request.status, detail ?? `Upload failed (${request.status})`, payload))
          }
        }
        request.onerror = () => reject(new ApiError(0, 'The upload could not reach the server'))
        request.onabort = () => reject(new ApiError(0, 'Upload cancelled'))
        options.signal?.addEventListener('abort', () => request.abort())
        request.send(form)
      })
      return attempt(false)
    },
  },

  ai: {
    /**
     * One-click translation (blueprint §14).
     *
     * `source_lang` should be the *declared* language of the content when we
     * have one: the server's detector only separates scripts and falls back to
     * English, so it cannot tell Swahili from French. Passing the author's
     * declared language avoids a confidently wrong guess.
     */
    translate: (text: string, target_lang: string, source_lang?: string) =>
      api.post<{
        translated: string
        source_lang: string
        provider?: string
        mock?: boolean
        cached: boolean
        noop?: boolean
      }>('/ai/translate', { text, target_lang, source_lang }, { auth: false }),
  },

  social: {
    follow: (userId: string) => api.post<{ following: boolean; already?: boolean }>(`/users/${userId}/follow`),
    unfollow: (userId: string) => api.delete<void>(`/users/${userId}/follow`),
  },

  /**
   * Connections — mutual, unlike follows. Acceptance is what unlocks
   * messaging, family links and community invites.
   */
  connections: {
    list: () =>
      api.get<{ incoming: ConnectionEntry[]; outgoing: ConnectionEntry[]; accepted: ConnectionEntry[] }>(
        '/connections',
      ),
    invite: (userId: string, message?: string) =>
      api.post<{ status: string; already?: boolean; note?: string }>(`/connections/${userId}`, { message }),
    respond: (userId: string, accept: boolean) =>
      api.post<{ status: string }>(`/connections/${userId}/respond?accept=${accept}`),
    remove: (userId: string) => api.delete<void>(`/connections/${userId}`),
    with: (userId: string) => api.get<Permissions>(`/connections/with/${userId}`),
  },

  suggestions: {
    people: (limit = 4) =>
      api.get<{ reason: string; items: PersonSuggestion[] }>(`/users/suggestions?limit=${limit}`),
    places: (limit = 3) => api.get<Discoveries>(`/discover?limit=${limit}`),
  },

  circles: {
    list: () => api.get<Circle[]>('/circles'),
    /** One circle and its members — its owner only. */
    get: (id: string) => api.get<CircleDetail>(`/circles/${id}`),
    create: (input: { name: string; kind?: string; color?: string; rule?: CircleRule }) =>
      api.post<CircleDetail>('/circles', { kind: 'custom', ...input }),
    update: (id: string, patch: { name?: string; kind?: string; color?: string | null; rule?: CircleRule }) =>
      api.patch<CircleDetail>(`/circles/${id}`, patch),
    remove: (id: string) => api.delete<void>(`/circles/${id}`),
    addMember: (circleId: string, memberId: string) =>
      api.post<{ added: boolean; already?: boolean; members_count?: number }>(
        `/circles/${circleId}/members/${memberId}`,
      ),
    removeMember: (circleId: string, memberId: string) =>
      api.delete<void>(`/circles/${circleId}/members/${memberId}`),
  },

  communities: {
    /**
     * One community. A secret one answers 404 unless you are a member, or
     * unless `invite` is the code a member's share link carries.
     */
    get: (id: string, invite?: string | null) =>
      api.get<{
        id: string
        slug: string
        name: string
        description: string
        kind: string
        /**
         * An open community whose content anybody may read: its page, a link
         * to one of its posts, and a preview when that link is shared off
         * Kinjy. Reading only — taking part still needs joining.
         */
        public_content?: boolean
        price_usd: string | null
        members_count: number
        avatar_url?: string | null
        cover_url?: string | null
        owner_id?: string
        /** Requests waiting - for the owner and moderators only, else null. */
        pending_count?: number | null
        my_role: string | null
        my_status: string | null
        my_pinned?: boolean
        /** Whether you are told about each new post here. */
        my_notify_posts?: boolean
      }>(`/communities/${id}${invite ? `?invite=${encodeURIComponent(invite)}` : ''}`),

    /** The owner renames it, or changes its description, photo (asset id) or cover. */
    edit: (
      id: string,
      patch: { name?: string; description?: string; avatar_asset_id?: string | null; cover_asset_id?: string | null },
    ) =>
      api.patch<{ id: string; name: string; description: string; avatar_url: string | null; cover_url: string | null }>(
        `/communities/${id}`,
        patch,
      ),
    /** Who is in it: names, faces, roles. Members see it; anybody, for a public one. */
    roster: (id: string) =>
      api.get<{
        total: number
        items: Array<{
          user_id: string
          role: string
          profile: { handle: string; display_name: string; avatar_url: string | null } | null
        }>
      }>(`/communities/${id}/roster`),

    /** The groups pinned to the sidebar, oldest pin first. */
    pinned: () =>
      api.get<{
        items: Array<{ id: string; slug: string; name: string; kind: string; avatar_url: string | null }>
        free_pins: number
      }>('/communities/pinned'),
    /** Three pins are free; more answer 402 until the year's KYC fee is paid. */
    pin: (id: string) => api.post<{ pinned: boolean; already?: boolean; count?: number }>(`/communities/${id}/pin`),
    unpin: (id: string) => api.delete<{ pinned: boolean }>(`/communities/${id}/pin`),
    /** Be told about each new post in a group you belong to, or not. */
    setPostNotifications: (id: string, on: boolean) =>
      api.put<{ notify_posts: boolean }>(`/communities/${id}/notifications`, { notify_posts: on }),

    /** The roll and the queue waiting on it. Owner and moderators only. */
    members: (id: string, status?: string) =>
      api.get<{
        items: Array<{
          user_id: string
          role: string
          status: string
          joined_at: string
          profile: { handle: string; display_name: string; avatar_url: string | null } | null
        }>
      }>(`/communities/${id}/members${status ? `?status=${status}` : ''}`),

    act: (id: string, userId: string, action: 'approve' | 'reject' | 'ban' | 'unban' | 'promote' | 'demote') =>
      api.post<{ ok: boolean; action: string }>(`/communities/${id}/members/${userId}`, { action }),

    list: (params: { q?: string; country?: string; limit?: number; mine?: boolean } = {}) => {
      const query = new URLSearchParams()
      if (params.q) query.set('q', params.q)
      if (params.mine) query.set('mine', 'true')
      if (params.country) query.set('country', params.country)
      query.set('limit', String(params.limit ?? 30))
      // Signed in when there is a session, so each card can say Joined / Request sent.
      return api.get<{ total: number; items: Community[] }>(`/communities?${query}`)
    },
    create: (input: { name: string; description?: string; kind?: string; price_usd?: number; country?: string }) =>
      api.post<{ id: string; slug: string; kind: string }>('/communities', { kind: 'public', ...input }),
    join: (id: string, invite?: string | null) =>
      api.post<{ joined: boolean; status: string }>(
        `/communities/${id}/join${invite ? `?invite=${encodeURIComponent(invite)}` : ''}`,
      ),
    /** Public, private or secret. Owner and moderators; a paid community stays paid. */
    setPrivacy: (id: string, kind: 'public' | 'private' | 'secret') =>
      api.patch<{ kind: string; changed: boolean; admitted: number }>(`/communities/${id}/privacy`, { kind }),
    /**
     * Whether an open community's content can be read without joining.
     * Owner and moderators, and only on an open community — the server
     * answers 409 for any other kind.
     */
    setPublicContent: (id: string, publicContent: boolean) =>
      api.patch<{ public_content: boolean; changed: boolean }>(
        `/communities/${id}/public-content`,
        { public_content: publicContent },
      ),
    /**
     * The path to send somebody. For a secret community it carries the access
     * code that lets the page open for them, and only a member can get it.
     */
    shareLink: (id: string) =>
      api.post<{ path: string; invite: string | null }>(`/communities/${id}/share-link`),
    /** Leave, or withdraw a request that has not been answered. */
    leave: (id: string) => api.post<{ left: boolean }>(`/communities/${id}/leave`),
    /**
     * What has been posted inside a community.
     *
     * Served by social-service, where posts live, which is why the path is
     * under /feed rather than /communities: the gateway sends /communities to
     * community-service, and membership and posts are owned by different
     * services on purpose.
     */
    feed: (id: string, limit = 20, offset = 0) =>
      api.get<{
        community: {
          id: string
          name: string
          slug: string
          kind: string
          members_count: number
          public_content?: boolean
        }
        /**
         * Whether this reader may take part in what they are reading. False
         * for anybody who has not joined an open community that publishes
         * publicly — they can read every word and write none of it.
         */
        can_engage?: boolean
        total: number
        items: Post[]
      }>(`/feed/community/${id}?limit=${limit}&offset=${offset}`, { auth: false }),
  },

  forums: {
    /**
     * Digest a long thread. Refuses short ones on purpose — a summary of six
     * replies is a worse version of scrolling.
     */
    summary: (threadId: string, lang = 'en') =>
      api.get<{
        summarised: boolean
        reason?: string
        summary?: string
        replies_counted: number
        provider?: string
        model?: string
        mock?: boolean
      }>(`/threads/${threadId}/summary?lang=${lang}`, { auth: false }),

    list: (params: { hierarchy?: string; scope?: string } = {}) => {
      const query = new URLSearchParams()
      if (params.hierarchy) query.set('hierarchy', params.hierarchy)
      if (params.scope) query.set('scope', params.scope)
      return api.get<{ geo_scopes: string[]; items: Forum[] }>(`/forums?${query}`, { auth: false })
    },
    create: (input: { name: string; description?: string; hierarchy?: string; scope?: string; scope_value?: string }) =>
      api.post<{ id: string; slug: string }>('/forums', { hierarchy: 'topic', ...input }),
    threads: (forumId: string, sort: ThreadSort = 'recent') =>
      api.get<{ items: Thread[] }>(`/forums/${forumId}/threads?sort=${sort}`, { auth: false }),
    createThread: (forumId: string, input: { title: string; body: string; lang?: string }) =>
      api.post<{ id: string }>(`/forums/${forumId}/threads`, input),
    thread: (threadId: string) => api.get<ThreadDetail>(`/threads/${threadId}`, { auth: false }),
    reply: (threadId: string, body: string) => api.post<{ id: string }>(`/threads/${threadId}/replies`, { body }),
    /** What a forum has settled, with the discussions it came from. */
    knowledge: (forumId: string, q = '') =>
      api.get<{ items: KnowledgeEntry[] }>(
        `/forums/${forumId}/knowledge${q ? `?q=${encodeURIComponent(q)}` : ''}`,
        { auth: false },
      ),
    /** Upvote a reply, or take the vote back. One vote each, never on your own reply. */
    upvote: (replyId: string) => api.post<{ upvotes: number; voted: boolean }>(`/replies/${replyId}/upvote`),
    /** Mark a reply as the answer. Only the author of the thread can. */
    acceptAnswer: (replyId: string) =>
      api.post<{ accepted: boolean; reply_id: string }>(`/replies/${replyId}/accept`),
  },

  /**
   * Browser notifications. The key is public and says whether this
   * installation can push at all, so the switch is never offered when it
   * cannot work.
   */
  push: {
    key: () => api.get<{ available: boolean; public_key: string | null }>('/push/key', { auth: false }),
    subscribe: (subscription: { endpoint: string; p256dh: string; auth: string }) =>
      api.post<{ subscribed: boolean }>('/push/subscribe', subscription),
    unsubscribe: (endpoint: string) =>
      api.delete<void>(`/push/subscribe?endpoint=${encodeURIComponent(endpoint)}`),
  },

  messages: {
    /** Turn disappearing messages on (seconds) or off (0) for a conversation. */
    setDisappearing: (conversationId: string, seconds: number) =>
      api.post<{ seconds: number }>(`/conversations/${conversationId}/disappearing`, { seconds }),
    conversations: () => api.get<{ items: Conversation[] }>('/conversations'),
    /** The Messages badge: unread messages from others, and in how many threads. */
    unreadCount: () => api.get<{ messages: number; conversations: number }>('/conversations/unread-count'),
    start: (participantIds: string[]) =>
      api.post<{ id: string; encrypted: boolean; existing: boolean }>('/conversations', {
        participant_ids: participantIds,
        kind: participantIds.length > 1 ? 'group' : 'direct',
        // Opt out of E2E for this demo surface: the server refuses plaintext in
        // an encrypted conversation, and no key exchange is implemented yet.
        encrypted: false,
      }),
    /** A named room. Groups are never end to end: there is no group key. */
    createGroup: (input: { title: string; description?: string; participantIds: string[] }) =>
      api.post<{ id: string; encrypted: boolean }>('/conversations', {
        participant_ids: input.participantIds,
        kind: 'group',
        title: input.title,
        description: input.description,
        encrypted: false,
      }),
    /**
     * Who is in a room and who runs it, a page at a time. Members only.
     *
     * Paged because the welcome group holds the whole platform: admins come
     * first, so the first page is the part anybody actually reads.
     */
    participants: (conversationId: string, offset = 0) =>
      api.get<{ total: number; items: GroupMember[]; offset: number; has_more: boolean }>(
        `/conversations/${conversationId}/participants?offset=${offset}`,
      ),
    /**
     * Rename a group, say what it is for, or change its photo and banner.
     * Admins only. An image is applied by the asset id the upload returned;
     * null takes it off.
     */
    editGroup: (
      conversationId: string,
      input: {
        title?: string
        description?: string
        avatar_asset_id?: string | null
        cover_asset_id?: string | null
      },
    ) =>
      api.patch<{
        id: string
        title: string | null
        description: string | null
        avatar_url: string | null
        cover_url: string | null
      }>(`/conversations/${conversationId}`, input),
    addMembers: (conversationId: string, userIds: string[]) =>
      api.post<{ added: string[] }>(`/conversations/${conversationId}/participants`, {
        user_ids: userIds,
      }),
    removeMember: (conversationId: string, userId: string) =>
      api.delete<{ removed: string }>(`/conversations/${conversationId}/participants/${userId}`),
    /** Public or private. Admins only. Opening a group lets in whoever was waiting. */
    setPrivacy: (conversationId: string, privacy: 'public' | 'private') =>
      api.patch<{ privacy: string; changed: boolean; admitted: number }>(
        `/conversations/${conversationId}/privacy`,
        { privacy },
      ),
    /** Whether members added later read the earlier messages. Admins only. */
    setHistory: (conversationId: string, history: 'all' | 'from_join') =>
      api.patch<{ history: string; changed: boolean }>(`/conversations/${conversationId}/history`, { history }),
    /** Answer a conversation request: yes. Replying does the same. */
    accept: (conversationId: string) => api.post<unknown>(`/conversations/${conversationId}/accept`),
    /** The group's link, for any member to send. */
    shareLink: (conversationId: string) =>
      api.post<{ path: string; code: string }>(`/conversations/${conversationId}/share-link`),
    /** What a link leads to, before deciding. Never the messages or the members. */
    previewLink: (code: string) =>
      api.get<{
        id: string | null
        title: string | null
        description: string | null
        avatar_url: string | null
        cover_url: string | null
        privacy: 'public' | 'private'
        members_count: number
        my_state: 'member' | 'pending' | 'none'
      }>(`/conversations/join/${encodeURIComponent(code)}`),
    joinByLink: (code: string) =>
      api.post<{ status: 'member' | 'pending'; conversation_id: string | null }>(
        `/conversations/join/${encodeURIComponent(code)}`,
      ),
    requests: (conversationId: string) =>
      api.get<{ items: Array<GroupMember & { created_at: string }> }>(`/conversations/${conversationId}/requests`),
    answerRequest: (conversationId: string, userId: string, action: 'approve' | 'reject') =>
      api.post<{ user_id: string; status: string }>(`/conversations/${conversationId}/requests/${userId}`, {
        action,
      }),
    /** Three groups pin free; more answer 402 until this year's KYC fee is paid. */
    pin: (conversationId: string) =>
      api.post<{ pinned: boolean; already?: boolean; count?: number }>(`/conversations/${conversationId}/pin`),
    unpin: (conversationId: string) => api.delete<{ pinned: boolean }>(`/conversations/${conversationId}/pin`),
    setRole: (conversationId: string, userId: string, role: 'admin' | 'member') =>
      api.post<{ user_id: string; role: string }>(
        `/conversations/${conversationId}/participants/${userId}/role`,
        { role },
      ),
    /**
     * A page of the thread, oldest first. `after` catches up after a
     * reconnect; `before` pages back through history.
     */
    list: (conversationId: string, options: { after?: string; before?: string } = {}) => {
      const query = new URLSearchParams()
      if (options.after) query.set('after', options.after)
      if (options.before) query.set('before', options.before)
      const qs = query.toString()
      return api.get<{ items: Message[]; profiles?: Record<string, PersonBrief>; mentions?: Record<string, string> }>(
        `/conversations/${conversationId}/messages${qs ? `?${qs}` : ''}`,
      )
    },
    /**
     * Text, an uploaded attachment (`mediaId`), or both. `clientId` makes a
     * retry idempotent: the same id is never stored twice.
     */
    send: (
      conversationId: string,
      message: {
        body?: string | null
        mediaId?: string
        clientId?: string
        replyToId?: string | null
        /** Sends a standalone sticker: a catalogue id, no text, no file. */
        stickerId?: string | null
      },
    ) =>
      api.post<{ id: string; created_at: string; client_id: string | null; duplicate?: boolean }>(
        `/conversations/${conversationId}/messages`,
        {
          body: message.body || null,
          kind: message.stickerId ? 'sticker' : message.mediaId ? 'media' : 'text',
          media_id: message.mediaId,
          client_id: message.clientId,
          reply_to_id: message.replyToId || undefined,
          sticker_id: message.stickerId || undefined,
        },
      ),

    /** The sticker packs, as the server curates them. */
    stickers: () => api.get<{ packs: StickerPack[] }>('/stickers'),

    /** Change the text of your own message. The result is marked as edited. */
    edit: (conversationId: string, messageId: string, body: string) =>
      api.patch<{ id: string; body: string; edited_at: string }>(
        `/conversations/${conversationId}/messages/${messageId}`,
        { body },
      ),

    /** Delete your own message. The row stays, the words go. */
    remove: (conversationId: string, messageId: string) =>
      api.delete<{ id: string; deleted: boolean }>(
        `/conversations/${conversationId}/messages/${messageId}`,
      ),

    /**
     * React, swap the reaction, or take it back - the server decides which,
     * from what is already there. One per person per message.
     */
    react: (conversationId: string, messageId: string, emoji: string) =>
      api.post<{ message_id: string; counts: Record<string, number>; mine: string | null }>(
        `/conversations/${conversationId}/messages/${messageId}/reactions`,
        { emoji },
      ),
    /** The thread is on screen: record it as read and tell the room. */
    markRead: (conversationId: string) =>
      api.post<{ read_at: string }>(`/conversations/${conversationId}/read`),
    /** Online state for connections and conversation partners; others are omitted. */
    presence: (userIds: string[]) =>
      api.get<{ items: Record<string, Presence> }>(
        `/presence?ids=${encodeURIComponent(userIds.join(','))}`,
      ),
  },

  family: {
    /** Searches your own family only — the server scopes it to your graph. */
    search: (q: string) => api.get<{ items: Person[] }>(`/family/search?q=${encodeURIComponent(q)}`),
    /** Where the member stands: their own node, or null before they have added themselves. */
    me: () => api.get<{ person: PersonDetail | null }>('/family/me'),
    addPerson: (input: PersonFields & { user_id?: string }) => api.post<Person>('/family/persons', input),
    person: (personId: string) => api.get<PersonDetail>(`/family/persons/${personId}`),
    /** Only the fields sent change; `null` clears one. */
    updatePerson: (personId: string, changes: Partial<PersonFields>) =>
      api.patch<PersonDetail>(`/family/persons/${personId}`, changes),
    /** Takes the person's relationships with them. */
    deletePerson: (personId: string) => api.delete<void>(`/family/persons/${personId}`),
    /** The person and the relationship are written together, or not at all. */
    addRelative: (personId: string, relation: RelativeKind, person: PersonFields, extras: RelativeExtras = {}) =>
      api.post<{
        person: Person
        relationship: { id: string; kind: string; from: string; to: string; role: ParentRole | null }
        relationships: Array<{ id: string; kind: string; from: string; to: string; role: ParentRole | null }>
      }>(`/family/persons/${personId}/relatives`, { relation, person, ...extras }),
    tree: (personId: string, depth = 3) =>
      // Authenticated on purpose: the tree is family-only, and the server
      // refuses a caller with no part in it.
      api.get<FamilyTree>(`/family/tree/${personId}?depth=${depth}`),
    /** The people in the families you belong to. */
    persons: (limit = 30) => api.get<{ items: Person[] }>(`/family/persons?limit=${limit}`),
    link: (input: { from_person_id: string; to_person_id: string; kind: string; role?: ParentRole }) =>
      api.post<{ id: string; status: string }>('/family/relationships', input),
    unlink: (relationshipId: string) => api.delete<void>(`/family/relationships/${relationshipId}`),
    /** Say, or take back (null), whether a parent is the father or the mother. */
    setRole: (relationshipId: string, role: ParentRole | null) =>
      api.patch<{ id: string; role: ParentRole | null }>(`/family/relationships/${relationshipId}`, { role }),
    howRelated: (from: string, to: string) =>
      api.get<HowRelated>(`/family/how-related?from_person=${from}&to_person=${to}`),
    confirm: (personId: string, decision: 'confirm' | 'dispute', note?: string) =>
      api.post<{ status: string; confirmations: number; threshold: number }>(
        `/family/persons/${personId}/confirm`,
        { decision, note },
      ),
  },

  memorials: {
    /**
     * Browse the graveyard. Public on purpose: a memorial exists to be
     * visited, and requiring an account to find a grave is the wrong default.
     * `mine` lists the memorials you look after instead, private ones included.
     */
    list: (params: { q?: string; mine?: boolean; limit?: number } = {}) => {
      const query = new URLSearchParams()
      if (params.q) query.set('q', params.q)
      if (params.mine) query.set('mine', 'true')
      query.set('limit', String(params.limit ?? 30))
      return api.get<{ total: number; items: Memorial[] }>(`/memorials?${query}`)
    },
    create: (input: {
      full_name: string
      birth_date?: string
      death_date?: string
      biography?: string
      visibility?: 'public' | 'private'
    }) => api.post<Memorial>('/memorials', input),
    get: (id: string) => api.get<Memorial>(`/memorials/${id}`),
    byQr: (code: string) => api.get<Memorial>(`/memorials/qr/${encodeURIComponent(code)}`),
    update: (
      id: string,
      patch: Partial<{
        full_name: string
        birth_date: string | null
        death_date: string | null
        biography: string | null
        visibility: 'public' | 'private'
        moderation: 'open' | 'pending_approval'
        faith_style: string
        faith_style_source: 'documented_wish' | 'admin_choice' | null
        photo_media_id: string | null
        cover_media_id: string | null
        audio_media_id: string | null
        audio_autoplay: boolean
      }>,
    ) => api.patch<Memorial>(`/memorials/${id}`, patch),
    remove: (id: string) => api.delete<void>(`/memorials/${id}`),

    /**
     * Approved tributes, newest first. `kind` is one kind or several joined by
     * commas — the guest book asks for `message,photo` so candles cannot push
     * the words off the first page. `counts` is always whole; `total` follows
     * the filter.
     */
    tributes: (id: string, opts: { kind?: string; limit?: number; offset?: number } = {}) => {
      const query = new URLSearchParams()
      if (opts.kind) query.set('kind', opts.kind)
      if (opts.limit) query.set('limit', String(opts.limit))
      if (opts.offset) query.set('offset', String(opts.offset))
      const qs = query.toString()
      return api.get<{ total: number; counts: Record<string, number>; items: Tribute[] }>(
        `/memorials/${id}/tributes${qs ? `?${qs}` : ''}`,
      )
    },
    tribute: (id: string, input: { kind: string; author_name?: string; body?: string; media_id?: string }) =>
      api.post<{ id: string; status: string }>(`/memorials/${id}/tributes`, input),
    pendingTributes: (id: string) => api.get<{ items: Tribute[] }>(`/memorials/${id}/tributes/pending`),
    moderate: (id: string, tributeId: string, decision: 'approved' | 'rejected') =>
      api.post<{ id: string; status: string }>(
        `/memorials/${id}/tributes/${tributeId}/moderate?decision=${decision}`,
      ),

    gallery: (id: string) => api.get<Gallery>(`/memorials/${id}/media`),
    addMedia: (id: string, input: { media_id: string; caption?: string; sensitive?: boolean }) =>
      api.post<GalleryItem>(`/memorials/${id}/media`, input),
    updateMedia: (
      id: string,
      itemId: string,
      patch: Partial<{ caption: string | null; sensitive: boolean; provenance: string }>,
    ) => api.patch<GalleryItem>(`/memorials/${id}/media/${itemId}`, patch),
    /** Every item, once, in the order wanted. */
    reorderMedia: (id: string, ids: string[]) =>
      api.post<{ items: GalleryItem[] }>(`/memorials/${id}/media/reorder`, { ids }),
    removeMedia: (id: string, itemId: string) => api.delete<void>(`/memorials/${id}/media/${itemId}`),
    /** Put a visitor's approved photo in the gallery; the file stays the tribute's. */
    promoteTribute: (id: string, tributeId: string) =>
      api.post<GalleryItem>(`/memorials/${id}/media/from-tribute/${tributeId}`),

    events: (id: string) => api.get<{ items: MemorialEvent[] }>(`/memorials/${id}/events`),
    addEvent: (id: string, input: { year: number; month?: number; day?: number; title: string; body?: string }) =>
      api.post<MemorialEvent>(`/memorials/${id}/events`, input),
    deleteEvent: (id: string, eventId: string) => api.delete<void>(`/memorials/${id}/events/${eventId}`),

    setLocation: (
      id: string,
      input: { lat: number; lng: number; label?: string; captured_on_site: boolean; accuracy_m?: number },
    ) =>
      api.post<{ verified: boolean; lat: number; lng: number; label: string | null; accuracy_m: number | null }>(
        `/memorials/${id}/location`,
        input,
      ),
    clearLocation: (id: string) => api.delete<void>(`/memorials/${id}/location`),

    admins: (id: string) => api.get<{ max: number; items: MemorialAdmin[] }>(`/memorials/${id}/admins`),
    addAdmin: (id: string, userId: string) =>
      api.post<{ added: boolean; already?: boolean; succession_order?: number }>(`/memorials/${id}/admins/${userId}`),
    removeAdmin: (id: string, userId: string) => api.delete<void>(`/memorials/${id}/admins/${userId}`),

    reminders: (id: string) =>
      api.get<{ items: Array<{ occasion: string; due_at: string; offset_hours: number | null }> }>(
        `/memorials/${id}/reminders`,
      ),
    reportDeath: (id: string, input: { evidence: string; document_media_id?: string }) =>
      api.post<{ death_status: string }>(`/memorials/${id}/report-death`, input),

    /** Platform staff: deaths waiting for a decision. */
    reviewQueue: () => api.get<{ items: DeathReviewItem[] }>('/admin/memorials/death-reports'),
    startReview: (id: string) => api.post<{ death_status: string }>(`/admin/memorials/${id}/review`),
    decideDeath: (id: string, outcome: 'verified' | 'rejected') =>
      api.post<{ death_status: string }>(`/admin/memorials/${id}/verify-death?outcome=${outcome}`),
  },

  market: {
    products: (
      params: {
        q?: string
        country?: string
        city?: string
        kind?: string
        min_price?: string
        max_price?: string
        sort?: 'recent' | 'price_asc' | 'price_desc'
        limit?: number
        offset?: number
        /** Only the signed-in member's own active listings. Needs the token, unlike the public list. */
        mine?: boolean
      } = {},
    ) => {
      const query = new URLSearchParams()
      if (params.q) query.set('q', params.q)
      if (params.mine) query.set('mine', 'true')
      if (params.offset) query.set('offset', String(params.offset))
      if (params.country) query.set('country', params.country)
      if (params.city) query.set('city', params.city)
      if (params.kind) query.set('kind', params.kind)
      if (params.min_price) query.set('min_price', params.min_price)
      if (params.max_price) query.set('max_price', params.max_price)
      if (params.sort) query.set('sort', params.sort)
      query.set('limit', String(params.limit ?? 30))
      return api.get<{ total: number; items: Product[] }>(`/commerce/products?${query}`, {
        auth: params.mine === true,
      })
    },
    createProduct: (input: {
      title: string
      description?: string
      kind?: 'product' | 'service' | 'digital'
      vendor_price: string
      stock?: number | null
      country?: string
      city?: string
      images?: string[]
    }) =>
      api.post<{ id: string; pricing: Record<string, string> }>('/commerce/products', input),
    order: (productId: string, quantity = 1, shipping?: OrderShipping) =>
      api.post<{ id: string; status: string; customer_price: string }>('/commerce/orders', {
        product_id: productId,
        quantity,
        ...(shipping ? { shipping } : {}),
      }),
    myOrders: (params: { status?: string; role?: 'buyer' | 'seller' } = {}) => {
      const query = new URLSearchParams()
      if (params.status) query.set('status', params.status)
      if (params.role) query.set('role', params.role)
      const qs = query.toString()
      return api.get<{ items: Order[] }>(`/commerce/orders/me${qs ? `?${qs}` : ''}`)
    },
    order_: (id: string) => api.get<Order>(`/commerce/orders/${id}`),

    /** Public; signed in, the owner also gets the price breakdown. */
    product: (id: string) => api.get<ProductDetail>(`/commerce/products/${id}`),
    myProducts: () => api.get<{ items: MyProduct[] }>('/commerce/products/mine'),
    updateProduct: (id: string, patch: ProductPatch) =>
      api.patch<{ id: string; status: string; pricing: ProductPricing }>(`/commerce/products/${id}`, patch),
    removeProduct: (id: string) => api.delete<{ id: string; status: string }>(`/commerce/products/${id}`),
    sales: (status?: string) =>
      api.get<{ total: number; items: Sale[] }>(
        `/commerce/sales${status ? `?status=${encodeURIComponent(status)}` : ''}`,
      ),
    sellerFinances: () => api.get<SellerFinances>('/commerce/seller/finances'),
    vendor: (id: string, params: { limit?: number; offset?: number } = {}) => {
      const query = new URLSearchParams()
      if (params.limit != null) query.set('limit', String(params.limit))
      if (params.offset != null) query.set('offset', String(params.offset))
      return api.get<StorefrontResponse>(`/commerce/vendors/${id}?${query}`, { auth: false })
    },

    /** Who holds the money and for how long. Readable signed out on purpose. */
    escrowTerms: () => api.get<EscrowTerms>('/commerce/escrow/terms', { auth: false }),

    /** The seller says it has shipped. This does not release the money. */
    markDelivered: (
      orderId: string,
      input: string | { note?: string; carrier?: string; tracking_number?: string; tracking_url?: string } = '',
    ) =>
      api.post<Order>(
        `/commerce/orders/${orderId}/delivered`,
        typeof input === 'string' ? { note: input } : input,
      ),

    /** The buyer releases the escrow. Only they can. */
    confirmDelivery: (orderId: string) =>
      api.post<{ id: string; status: string }>(`/commerce/orders/${orderId}/confirm-delivery`, {}),

    /**
     * Start paying for an order. The amount must be the order's price: the server
     * checks it, so a different one is refused. Without a live payment rail the
     * reply is a mock checkout that `settleMockPayment` completes; with one,
     * `checkout_url` and `pay_address` say where to pay.
     */
    payOrder: (orderId: string, amount: string) =>
      api.post<OrderPayment>('/payments/checkout', {
        purpose: 'order',
        amount,
        reference: orderId,
        return_path: `/market/orders/${orderId}`,
      }),
    settleMockPayment: (paymentId: string) =>
      api.post<{ payment_id: string; status: string; mock: boolean }>(`/payments/${paymentId}/mock-settle`, {}),

    openDispute: (
      orderId: string,
      input: { category: string; reason: string; amount_claimed?: string; evidence?: string[] },
    ) => api.post<Dispute>(`/commerce/orders/${orderId}/dispute`, input),

    myDisputes: () => api.get<{ items: Dispute[] }>('/commerce/disputes/me'),
    dispute: (id: string) => api.get<Dispute>(`/commerce/disputes/${id}`),
    replyToDispute: (id: string, input: { body: string; evidence?: string[] }) =>
      api.post<{ ok: boolean; status: string }>(`/commerce/disputes/${id}/messages`, input),
    withdrawDispute: (id: string) => api.post<Dispute>(`/commerce/disputes/${id}/withdraw`, {}),
    concedeDispute: (id: string) => api.post<Dispute>(`/commerce/disputes/${id}/concede`, {}),

    /** Buyer only, once the order is settled; one review per order. */
    reviewOrder: (orderId: string, input: { rating: number; comment?: string }) =>
      api.post<{ id: string; order_id: string; product_id: string; rating: number; comment: string | null }>(
        `/commerce/orders/${orderId}/review`,
        input,
      ),
    productReviews: (productId: string, params: { limit?: number; offset?: number } = {}) => {
      const query = new URLSearchParams()
      if (params.limit != null) query.set('limit', String(params.limit))
      if (params.offset != null) query.set('offset', String(params.offset))
      const qs = query.toString()
      return api.get<ReviewsSummary>(`/commerce/products/${productId}/reviews${qs ? `?${qs}` : ''}`, {
        auth: false,
      })
    },
    vendorRating: (vendorId: string) =>
      api.get<{ vendor_id: string; average: number; count: number }>(`/commerce/vendors/${vendorId}/rating`, {
        auth: false,
      }),
  },

  /** Kinjy Leaders — 5% of monthly revenue, split by commission earned. */
  leaders: {
    standings: (period?: string) =>
      api.get<LeaderBoard>(`/leaders/standings${period ? `?period=${period}` : ''}`, { auth: false }),
    me: (period?: string) =>
      api.get<MyLeaderStanding>(`/leaders/me${period ? `?period=${period}` : ''}`),
    frozen: (period: string) =>
      api.get<{ period: string; items: Array<LeaderStanding & { leader_id: string; payout_amount: string; paid: boolean }> }>(
        `/leaders-pool/${period}`,
        { auth: false },
      ),
  },

  /** The referral pool — a paid seat that receives uninvited sign-ups. */
  referralPool: {
    state: () => api.get<ReferralPoolState>('/auth/referral-pool', { auth: false }),
    mine: () => api.get<ReferralPoolState>('/auth/referral-pool'),
    assignments: () =>
      api.get<{
        items: Array<{
          member_id: string
          handle: string | null
          display_name: string | null
          seats_in_draw: number
          assigned_at: string
        }>
      }>('/auth/referral-pool/assignments'),
    join: () =>
      api.post<{ payment_id: string; rail: string; amount: string; checkout_url?: string; mock?: boolean }>(
        '/payments/referral-pool/join',
        {},
      ),
  },

  ads: {
    rateCard: () =>
      api.get<{ floors: Record<string, string>; currency: string; note: string }>('/ads/rate-card', {
        auth: false,
      }),
  },

  auth: {
    async register(input: {
      email: string
      password: string
      display_name: string
      handle: string
      /** ISO date. Required: the server derives the account's age tier from it
          and will refuse a registration without one. The tier itself is never
          sent by the client. */
      date_of_birth: string
      country?: string
      lang?: string
      referral_code?: string
    }) {
      const result = await api.post<AuthResult>('/auth/register', input, { auth: false })
      tokens.set(result.tokens)
      return result.user
    },
    /** Sign in with a passkey instead of a password. */
    async loginWithPasskey(email: string) {
      const options = await api.post<PublicKeyCredentialRequestOptionsJSON & { handle: string }>(
        '/auth/passkeys/login/options',
        { email },
        { auth: false },
      )
      const assertion = (await navigator.credentials.get({
        publicKey: parseRequestOptions(options),
      })) as PublicKeyCredential | null
      if (!assertion) throw new ApiError(401, 'No passkey was offered')
      const result = await api.post<AuthResult>(
        '/auth/passkeys/login/verify',
        { handle: options.handle, credential: serializeCredential(assertion) },
        { auth: false },
      )
      tokens.set(result.tokens)
      return result.user
    },

    async login(email: string, password: string) {
      const result = await api.post<AuthResult>('/auth/login', { email, password }, { auth: false })
      tokens.set(result.tokens)
      return result.user
    },
    me: () => api.get<AuthUser>('/auth/me'),

    /** The members this one brought in. One level - the programme pays on these. */
    referrals: () =>
      api.get<{ total: number; verified: number; items: Referral[] }>('/auth/referrals'),

    /**
     * Who an invitation code belongs to, or a 404.
     *
     * Asked before a code off a link is remembered, so noise in a `?ref=`
     * cannot displace a real invitation, and so the sign-up page can name the
     * person who invited you instead of showing a code in a box.
     */
    referral: (code: string) =>
      api.get<{ code: string; display_name: string; handle: string }>(
        `/auth/referral/${encodeURIComponent(code)}`,
        { auth: false },
      ),

    /**
     * Whether a reset link can actually be delivered.
     *
     * Asked before the form is offered: an installation with no mail configured
     * can only answer "not available", and finding that out after typing your
     * address reads as a fault on your side.
     */
    resetAvailable: () =>
      api.get<{ available: boolean }>('/auth/password/reset-available', { auth: false }),

    /**
     * Ask for a reset link. Succeeds whether or not the address has an account -
     * the server will not say, and neither will this.
     */
    requestPasswordReset: (email: string) =>
      api.post<{ status: string; note: string }>(
        '/auth/password/reset-request', { email }, { auth: false },
      ),

    /** Set a new password from the link. Every session is signed out, this one included. */
    async resetPassword(token: string, new_password: string) {
      const result = await api.post<{ status: string; sessions_ended: number }>(
        '/auth/password/reset', { token, new_password }, { auth: false },
      )
      // Whatever was stored belongs to the session the server has just revoked.
      tokens.clear()
      return result
    },
    async logout() {
      const refresh_token = tokens.refresh
      // Revoke the session server-side so the device disappears from the
      // device manager; clearing local storage alone would leave it listed.
      if (refresh_token) {
        try {
          await api.post('/auth/logout', { refresh_token })
        } catch {
          /* the local sign-out must succeed even if the call fails */
        }
      }
      tokens.clear()
    },
    isSignedIn: () => Boolean(tokens.access),
  },

  /**
   * Parental supervision.
   *
   * `disclosure` is deliberately unauthenticated and fetched by both sides
   * before either agrees: what a parent can and cannot see is part of the
   * agreement, not a policy page somebody may or may not have read.
   */
  supervision: {
    disclosure: () => api.get<SupervisionDisclosure>('/supervision/disclosure', { auth: false }),
    mine: () => api.get<SupervisionState>('/supervision'),
    invite: (other_handle: string) =>
      api.post<SupervisionLink>('/supervision/invite', { other_handle }),
    answer: (id: string, approve: boolean) =>
      api.post<SupervisionLink>(`/supervision/${id}/answer`, { approve }),
    end: (id: string) => api.post<SupervisionLink>(`/supervision/${id}/end`),
    view: (id: string) => api.get<SupervisedView>(`/supervision/${id}/view`),
    setTimeLimit: (id: string, daily_limit_minutes: number | null) =>
      api.post<{ daily_limit_minutes: number | null }>(
        `/supervision/${id}/time-limit`, { daily_limit_minutes },
      ),
    answerRequest: (id: string, approve: boolean) =>
      api.post<{ id: string; status: string }>(`/supervision/requests/${id}`, { approve }),
  },

  /** Trust & Safety. Every call here is staff-only at the service. */
  /** The staff: who may use the console, and how far. Superadmins change it. */
  team: {
    list: () =>
      api.get<{
        items: Array<{ user_id: string; email: string; handle: string; display_name: string; role: StaffRole }>
        can_manage: boolean
      }>('/admin/team'),
    add: (who: string, role: StaffRole) => api.post<{ user_id: string; role: StaffRole }>('/admin/team', { who, role }),
    change: (userId: string, role: StaffRole) =>
      api.patch<{ user_id: string; role: StaffRole }>(`/admin/team/${userId}`, { role }),
    remove: (userId: string) => api.delete<{ user_id: string; role: string }>(`/admin/team/${userId}`),
  },

  /** Admin: payout wallets waiting for approval, and the withdrawals they paid. */
  payoutAdmin: {
    wallets: (status: PayoutWalletStatus | 'all' = 'pending_admin') =>
      api.get<{ items: PayoutWalletReview[] }>(`/admin/payouts/wallets?status=${status}`),
    approve: (walletId: number, note?: string) =>
      api.post<{ wallet: PayoutWalletReview }>(`/admin/payouts/wallets/${walletId}/approve`, { note }),
    reject: (walletId: number, note?: string) =>
      api.post<{ wallet: PayoutWalletReview }>(`/admin/payouts/wallets/${walletId}/reject`, { note }),
    withdrawals: (status?: string) =>
      api.get<{ items: Array<Withdrawal & { user_id: string; soncopay_payout_id?: string | null }> }>(
        `/admin/payouts/withdrawals${status ? `?status=${status}` : ''}`,
      ),
  },

  trustSafety: {
    overview: () => api.get<ModerationOverview>('/admin/moderation/overview'),
    reviewQueue: (limit = 50) =>
      api.get<{ pending: number; shown: number; items: ReviewItem[] }>(
        `/admin/classification-queue?limit=${limit}`,
      ),
    rate: (contentId: string, body: Record<string, unknown>) =>
      api.post(`/admin/classification/${contentId}/review`, body),
    appeals: (limit = 50) =>
      api.get<{ stats: ModerationOverview['appeals']; items: AppealItem[] }>(
        `/admin/moderation/appeals?limit=${limit}`,
      ),
    decideAppeal: (appealId: string, overturn: boolean, note?: string) =>
      api.post<{ id: string; status: string }>(`/admin/moderation/appeals/${appealId}`, {
        overturn,
        note,
      }),
  },

  /** What moderation did to you, and how to contest it. */
  moderation: {
    myDecisions: () => api.get<{ items: MyDecision[] }>('/moderation/decisions'),
    appeal: (decisionId: string, grounds: string) =>
      api.post(`/moderation/decisions/${decisionId}/appeal`, { grounds }),
    reportPost: (postId: string, reason: string, note?: string) =>
      api.post(`/posts/${postId}/report`, { reason, note }),
    reportComment: (commentId: string, reason: string, note?: string) =>
      api.post(`/comments/${commentId}/report`, { reason, note }),
  },

  /** Everything behind the member dashboard. */
  account: {
    profile: () => api.get<MyProfile>('/users/me'),
    profileByHandle: (handle: string) => api.get<Profile>(`/users/${handle}`, { auth: false }),
    preferences: () => api.get<Record<string, unknown>>('/preferences'),
    setPreferences: (patch: Record<string, unknown>) => api.patch('/preferences', patch),

    wellbeing: () => api.get<WellbeingStatus>('/wellbeing'),
    /**
     * Report time spent. The server clamps the claim against the wall clock, so
     * a missed beat cannot be made up for and a fast one cannot inflate the
     * count — the limit means the same thing across tabs and devices.
     */
    wellbeingBeat: (minutes: number) =>
      api.post<WellbeingStatus>('/wellbeing/heartbeat', { minutes }),
    updateProfile: (patch: ProfileUpdate) => api.patch<MyProfile>('/users/me', patch),
    profileEligibility: () => api.get<ProfileEligibility>('/users/me/eligibility'),

    wallet: () => api.get<Wallet>('/wallet'),
    commissions: (params: { source_kind?: string; limit?: number } = {}) => {
      const query = new URLSearchParams()
      if (params.source_kind) query.set('source_kind', params.source_kind)
      query.set('limit', String(params.limit ?? 20))
      return api.get<CommissionsPage>(`/commissions?${query}`)
    },
    payoutEligibility: () => api.get<PayoutEligibility>('/payments/eligibility'),
    addPayoutDestination: (input: { address: string; rail?: string; currency?: string; label?: string }) =>
      api.post<{ rail: string; address: string; whitelisted: boolean; note: string }>(
        '/payments/destinations',
        { rail: 'nowpayments', currency: 'usdtbsc', ...input },
      ),

    kyc: () => api.get<KycStatus>('/kyc/status'),
    /** Opens (or reopens) the Kaluta KYC session; `verification_url` is where the member goes next. */
    startKyc: () =>
      api.post<{ status: string; attempt?: number; verification_url?: string; resumed?: boolean; already?: boolean }>(
        '/kyc/start',
      ),
    /** Pay this year's verification fee. The server holds the price; `amount` must match it. */
    payKycFee: (amount: string) =>
      api.post<OrderPayment>('/payments/checkout', {
        purpose: 'kyc_fee',
        amount,
        return_path: '/dashboard?tab=verification',
      }),
    /** Where a payment stands, read back from Sonco Pay - what the page asks on the member's return. */
    paymentStatus: (paymentId: string) => api.get<PaymentIntentStatus>(`/payments/intents/${paymentId}`),
    /** The methods this payment can be paid with, on Kinjy's own page. */
    paymentMethods: (paymentId: string) => api.get<PayMethods>(`/payments/intents/${paymentId}/methods`),
    /** Pass the member's choice to Sonco Pay; the answer is the step to show next. */
    choosePaymentMethod: (paymentId: string, choice: PayChoice) =>
      api.post<{ status: string; settled: boolean; action: PayAction | null }>(
        `/payments/intents/${paymentId}/choose`,
        choice,
      ),

    payoutWallet: () => api.get<{ wallet: PayoutWallet | null; asset: string; network: string }>('/payments/payout-wallet'),
    /** Saves the address and mails the confirmation link. The same address again changes nothing. */
    setPayoutWallet: (address: string) =>
      api.put<{ wallet: PayoutWallet; changed: boolean }>('/payments/payout-wallet', { address }),
    resendWalletConfirmation: () => api.post<{ sent: boolean }>('/payments/payout-wallet/resend', {}),
    /** The link from the e-mail. Works signed out: having the mail is the proof. */
    confirmPayoutWallet: (token: string) =>
      api.post<{ wallet: PayoutWallet }>('/payments/payout-wallet/confirm', { token }, { auth: false }),
    withdrawQuote: () => api.get<WithdrawQuote>('/payments/withdraw/quote'),
    /** Withdraws the whole available balance to the approved wallet. */
    withdraw: () => api.post<{ withdrawal: Withdrawal }>('/payments/withdraw', {}),
    withdrawals: () => api.get<{ items: Withdrawal[] }>('/payments/withdrawals'),

    /**
     * Change the password, and sign the other devices out.
     *
     * The current refresh token goes with the request so the server knows which
     * session to keep - without it the member would be signed out of the very
     * device they are standing at. If the server signs us out anyway (no token
     * to hand it), the stored tokens are cleared rather than left to fail on
     * the next call.
     */
    async changePassword(current_password: string, new_password: string) {
      const result = await api.post<PasswordChangeResult>('/auth/password', {
        current_password,
        new_password,
        refresh_token: tokens.refresh,
      })
      if (result.signed_out_here) tokens.clear()
      return result
    },

    sessions: () => api.get<DeviceSession[]>('/auth/sessions'),
    revokeSession: (id: string) => api.delete<void>(`/auth/sessions/${id}`),
    passkeys: () => api.get<Passkey[]>('/auth/passkeys'),

    /**
     * Register a passkey. The private key is generated by the authenticator and
     * never leaves it; what crosses the wire is a public key and an attestation.
     */
    async registerPasskey(label?: string) {
      const options = await api.post<PublicKeyCredentialCreationOptionsJSON>(
        '/auth/passkeys/register/options',
      )
      const credential = (await navigator.credentials.create({
        publicKey: parseCreationOptions(options),
      })) as PublicKeyCredential | null
      if (!credential) throw new ApiError(400, 'No passkey was created')
      return api.post<{ id: string; label: string }>('/auth/passkeys/register/verify', {
        credential: serializeCredential(credential),
        label,
      })
    },
    removePasskey: (id: string) => api.delete<void>(`/auth/passkeys/${id}`),

    /** Blueprint §4 — no justification field, by design. */
    closeAccount: (input: { password: string; mode: 'deactivate' | 'delete'; cooling_period_days: number }) =>
      api.post<{ status: string; effective_at: string | null; note: string }>(
        '/auth/account/delete',
        input,
      ),
  },
}
