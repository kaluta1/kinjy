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

/**
 * The group a post lives in, when the answer to opening it was "members
 * only" - so the page can send the reader there to join.
 */
export function membersOnlyCommunity(err: unknown): { slug: string; name: string; kind: string } | null {
  if (!(err instanceof ApiError) || err.status !== 403) return null
  const detail = (err.detail as { detail?: { code?: string; community?: { slug: string; name: string; kind: string } } } | null)
    ?.detail
  return detail?.code === 'community_members_only' && detail.community ? detail.community : null
}

/** Pull a readable message out of FastAPI's error shapes. */
function messageFrom(status: number, body: unknown): string {
  if (typeof body === 'string' && body) return body
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    // A structured answer carries its sentence in `message`.
    if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
      return (detail as { message: string }).message
    }
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
  marketplace_payments: {
    held_until: string
    automatic_release: boolean
    payments: string
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
  /** The community a community post is in; null when it no longer exists. */
  community?: { name: string; slug: string } | null
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
  /** published, or for its author: pending (waiting for approval) / scheduled. */
  status?: string
  /** For a community post: the space it is in. */
  community_space?: CommunityPostSpace | null
  /** Pinned to the top of its community. */
  pinned?: boolean
  /** Pinned by a Kinjy admin above everything in the feed. */
  featured?: boolean
  /** When a scheduled post goes out. */
  publish_at?: string | null
  /** The comment marked as the answer to a community discussion. */
  accepted_comment_id?: string | null
  /** Saved by the reader. */
  bookmarked?: boolean
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
  /** A circle post: one or more of your circles, plus or minus named people. */
  circle_ids?: string[]
  include_ids?: string[]
  exclude_ids?: string[]
  /** 'live' follows your circles; 'snapshot' fixes the audience at publication. */
  audience_mode?: AudienceMode
  /** Tell the audience the post is there (no circle name is ever shown). */
  notify_audience?: boolean
  /** Required when visibility is 'community'; the server checks you are a member. */
  community_id?: string
  topics?: string[]
  lang?: string
  country?: string
  city?: string
  provenance?: string
  /** The author declaring adult content, so minors' feeds can exclude it. */
  mature?: boolean
  /** A community post's space (announcements are for stewards). */
  space?: CommunityPostSpace
  /** Publish later (ISO time, at least a minute ahead). */
  publish_at?: string
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
  /** Group chats in Messages: being put straight into one, and being invited. */
  who_can_add_group?: string
  who_can_invite_group?: string
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
  description?: string | null
  icon?: string | null
  archived?: boolean
  last_used_at?: string | null
  /** Smart circles: people left out whatever the rule says. */
  excluded_count?: number
}

export type CircleSort = 'name' | 'recent' | 'size' | 'created'
export type AudienceMode = 'live' | 'snapshot'

export interface AudiencePerson {
  user_id: string
  display_name: string | null
  handle: string | null
  avatar_url?: string | null
  city?: string | null
}

export interface AudienceSelection {
  circle_ids: string[]
  include_ids: string[]
  exclude_ids: string[]
}

export interface AudiencePreview {
  count: number
  people: AudiencePerson[]
  excluded: AudiencePerson[]
  refused: AudiencePerson[]
  includes_smart_circle: boolean
  archived_circles: string[]
}

/** The author's view of who a post goes to. */
export interface PostAudience {
  visibility: string
  audience_mode: AudienceMode
  audience_fixed_at: string | null
  notify_audience: boolean
  circles?: Array<{ id: string; name: string | null; deleted: boolean }>
  include?: AudiencePerson[]
  exclude?: AudiencePerson[]
  eligible_count?: number
  eligible?: AudiencePerson[]
  /** Only when enough people opted out that it points at nobody. */
  opted_out_count?: number | null
  note?: string
}

export interface AudienceChange extends AudienceSelection {
  visibility: 'public' | 'followers' | 'connections' | 'circle' | 'private'
  audience_mode: AudienceMode
  confirm_added?: boolean
}

/** What a 409 on an audience change says: who would newly see the post. */
export interface AudienceWidening {
  message: string
  added_count: number | null
  added: AudiencePerson[]
  becomes: string
}

export interface AudiencePrefState {
  muted: boolean
  hidden: boolean
  opted_out: boolean
  opt_out_past: boolean
  opted_out_at: string | null
}

export interface AudienceControl extends AudiencePrefState {
  /** A label only this reader sees; it says nothing about the list itself. */
  token: string
  label: string
}

export interface AudiencePrefRow extends AudiencePrefState {
  id: number
  label: string
  author: { display_name?: string | null; handle?: string | null; avatar_url?: string | null } | null
}

export interface CirclePostRow {
  id: string
  excerpt: string
  format: string
  status: string
  publish_at: string | null
  created_at: string
  audience_mode: AudienceMode
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

export type CommunityPostSpace = 'announcements' | 'discussions' | 'articles' | 'opportunities'

export interface CommunitySpace {
  id: string
  label: string
  description: string
  enabled: boolean
  /** False while the feature behind the space is not built: it cannot be switched on. */
  available: boolean
  /** Who may post in it: members, stewards, or null for a space that is not a stream of posts. */
  posters: 'members' | 'stewards' | null
}

export interface CommunityTemplate {
  id: string
  label: string
  description: string
  kind: string
  public_content: boolean
  spaces: string[]
  post_approval: 'none' | 'new_members' | 'all'
}

export interface CommunityTier {
  id: string
  name: string
  description: string
  benefits: string[]
  period: 'monthly' | 'annual' | 'one_time'
  trial_days: number
  active: boolean
  /** The owner's price. */
  owner_price: string
  /** What a member pays: the owner's price plus Kinjy's fee. */
  price: string
  intro_price: string | null
}

export interface CommunityEntitlement {
  id: string
  community_id: string
  tier_id: string
  status: 'trial' | 'active' | 'grace' | 'expired' | 'cancelled' | 'refunded'
  has_access: boolean
  period_end: string | null
  grace_until: string | null
  cancel_at_period_end: boolean
  started_at: string
}

export interface CommunityPaymentRow {
  id: string
  kind: 'membership' | 'ticket'
  tier_id: string | null
  event_id: string | null
  owner_price: string
  kinjy_fee: string
  total: string
  refunded: string
  status: string
  coupon_code: string | null
  period_from: string | null
  period_to: string | null
  payment_ref: string
  created_at: string
  user_id: string
}

export interface CommunityQuote {
  quote_id: string
  kind: 'membership' | 'ticket'
  owner_price: string
  markup_pct: string
  markup: string
  total: string
  expires_at: string
  /** Present when a 100% code made it free: credited at once. */
  credited?: { payment_id: string }
}

export interface EventPresenter {
  user_id: string
  handle: string | null
  display_name: string | null
  added_at: string
}

export interface CommunityEvent {
  id: string
  community_id: string
  title: string
  description: string
  starts_at: string
  ends_at: string | null
  timezone: string
  mode: 'online' | 'in_person' | 'hybrid'
  location: string | null
  /** For registered attendees and hosts only. */
  online_url: string | null
  capacity: number | null
  registered_count: number
  places_left: number | null
  waitlist: boolean
  speakers: string[]
  recording_consent: boolean
  recording_url: string | null
  status: 'scheduled' | 'cancelled'
  host_id: string
  ticket_owner_price: string | null
  /** What a member pays for a ticket (owner's price + Kinjy's fee). */
  ticket_price: string | null
  my_status: 'registered' | 'waitlisted' | 'cancelled' | null
  /** The viewer runs it: the host, or a presenter the host named. No ticket for them. */
  my_role?: 'host' | 'presenter' | null
  /** The community it happens in, for its page and its link. */
  community?: { id: string; slug: string; name: string } | null
  /** The host, or the community's owner and moderators. */
  can_edit?: boolean
}

/** What the event form sets; on an edit, only what is sent changes. */
export interface EventInput {
  title: string
  description: string
  starts_at: string
  ends_at?: string | null
  timezone: string
  mode: string
  location?: string | null
  online_url?: string | null
  capacity?: number | null
  waitlist?: boolean
  ticket_price?: number | null
  speakers?: string[]
  recording_consent?: boolean
}

/** Template, spaces and the rules of the house, with what the reader may do. */
export interface CommunitySpaceView {
  /** free, paid (joining needs a membership) or mixed (some spaces are paid). */
  access_model?: 'free' | 'paid' | 'mixed'
  paid_spaces?: string[]
  /** Whether the reader has paid access (or runs the community). */
  my_entitled?: boolean
  template: string
  spaces: CommunitySpace[]
  post_approval: 'none' | 'new_members' | 'all'
  /** For the owner and moderators only (null for everybody else). */
  welcome_message: string | null
  rules: string
  my_can_post: Record<CommunityPostSpace, boolean>
  directory_open: boolean
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
  description?: string
  /** The community the forum belongs to, when it belongs to one. */
  community?: { id: string; slug: string; name: string } | null
  // 27/10: organising and access.
  category?: string | null
  tags?: string[]
  lang?: string | null
  country?: string | null
  /** Who can read it: public, the community's members only, or its moderators. */
  visibility?: 'public' | 'members' | 'private'
  unanswered_count?: number
  last_activity_at?: string | null
  following?: boolean
  post_policy?: ForumPolicy
  reply_policy?: ForumPolicy
}

export type ForumPolicy = 'everyone' | 'members' | 'moderators'
export type ThreadKind = 'question' | 'discussion' | 'guide' | 'announcement'

/** One forum's own page: its rules, moderators and what the viewer may do. */
export interface ForumDetail extends Forum {
  rules: string | null
  visibility_setting: 'inherit' | 'public' | 'members' | 'private'
  review_days: number
  community: { id: string; slug: string; name: string; kind: string } | null
  follow: { notify_new: boolean; digest: boolean } | null
  moderators: Array<{ user_id: string; display_name: string | null; handle: string | null; can_moderate: boolean; can_edit_knowledge: boolean }>
  can_post: boolean
  can_announce: boolean
  can_moderate: boolean
  can_configure: boolean
  can_edit_knowledge: boolean
  sessions: ExpertSession[]
}

export interface ExpertSession {
  id: string
  title: string
  description: string
  expert_id: string
  expert?: PersonBrief | null
  /** How the organiser introduces the expert: their words, not a check by Kinjy. */
  expert_intro: string
  starts_at: string
  ends_at: string
  open: boolean
}

export interface ForumAttachment {
  id: string
  url: string | null
  kind: string | null
  name: string | null
  type: string | null
  size: number | null
}

export interface ForumPoll {
  id: string
  question: string
  options: Array<{ index: number; text: string; votes: number }>
  multiple: boolean
  closes_at: string | null
  closed: boolean
  voters: number
  my_votes: number[]
}

/** A thread as a search result, a suggestion or a saved item. */
export interface ThreadBrief {
  id: string
  title: string
  excerpt: string
  kind: ThreadKind
  forum_id: string
  forum_name: string | null
  solved: boolean
  replies_count: number
  author?: PersonBrief | null
  last_activity_at: string
}

export interface ForumPrefs {
  replies: boolean
  mentions: boolean
  followed_threads: boolean
  topics: boolean
  digests: boolean
  background: string | null
  show_activity: boolean
  topics_followed?: string[]
}

export interface ForumModerationQueue {
  reports: Array<{
    id: number
    target_kind: 'thread' | 'reply' | 'knowledge' | 'review'
    target_id: string
    reason: string
    note: string | null
    reporter: string | null
    status: string
    resolution: string | null
    created_at: string
    target: { kind: string; title?: string | null; excerpt?: string | null; status?: string; thread_id?: string | null; moderation_reason?: string | null }
  }>
  suspected_spam: Array<{ id: string; thread_id: string | null; author_id: string; signal: string; excerpt: string }>
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
  kind?: ThreadKind
  tags?: string[]
  lang?: string
  country?: string | null
  region?: string | null
  edited_at?: string | null
  locked?: boolean
  /** The author says it promotes something they sell or are paid for. */
  commercial?: boolean
  has_poll?: boolean
  session_id?: string | null
  last_reply_at?: string | null
  attachments_count?: number
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
  source_reply_id?: string | null
  // 27/10: what makes an article traceable.
  status?: 'draft' | 'published'
  /** Written by an AI from the cited contributions; published only after a person reviewed it. */
  ai_generated?: boolean
  source_reply_ids?: string[]
  source_links?: Array<{ url: string; label: string | null }>
  contributors?: Array<{ user_id: string; display_name?: string | null; handle?: string | null }> | string[]
  tags?: string[]
  created_at?: string
  published_at?: string | null
  updated_at?: string | null
  reviewed_at?: string | null
  needs_review?: boolean
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
  /** Set when this thread was merged into another: open that one instead. */
  merged_into?: string
  kind?: ThreadKind
  tags?: string[]
  country?: string | null
  region?: string | null
  pinned?: boolean
  edited_at?: string | null
  reopened_at?: string | null
  commercial?: boolean
  solved?: boolean
  replies_count?: number
  views_count?: number
  last_activity_at?: string
  attachments?: ForumAttachment[]
  poll?: ForumPoll | null
  event?: { id: string; title: string; starts_at: string; timezone: string; status: string } | null
  session?: { id: string; title: string; expert_id: string } | null
  forum?: { id: string; name: string; rules: string | null; community_id: string | null }
  following?: boolean
  saved?: boolean
  can_reply?: boolean
  can_edit?: boolean
  can_moderate?: boolean
  can_accept?: boolean
  can_reopen?: boolean
  /** What the author says about their background: claimed, not verified. */
  author_background?: string | null
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
    edited_at?: string | null
    commercial?: boolean
    attachments?: ForumAttachment[]
    is_author?: boolean
    is_expert?: boolean
    author_background?: string | null
    can_edit?: boolean
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
export type GroupRole = 'owner' | 'admin' | 'moderator' | 'member'

export interface GroupMember {
  user_id: string
  role: GroupRole
  joined_at: string
  handle: string | null
  display_name: string
  avatar_url: string | null
  /** The viewer has a block with this member (either side). */
  blocked?: boolean
}

/** A member's own notifications for one conversation. */
export interface ConversationNotifications {
  level: 'all' | 'mentions' | 'announcements'
  muted_until: string | null
  muted_forever: boolean
  read_receipts: boolean
}

export interface GroupInviteLink {
  enabled: boolean
  expires_at: string | null
  max_uses: number | null
  uses: number
  path?: string | null
}

export interface GroupPoll {
  id: string
  question: string
  multiple: boolean
  results: 'always' | 'after_vote' | 'after_close'
  closes_at: string | null
  closed: boolean
  /** Counts are null while the poll's setting keeps them hidden from this member. */
  options: Array<{ id: number; text: string; votes: number | null }>
  voters: number | null
  my_votes: number[]
  results_visible: boolean
  can_close: boolean
}

export interface GroupEvent {
  id: string
  title: string
  description: string | null
  starts_at: string
  ends_at: string | null
  /** Where it happens (IANA zone); the instant is starts_at. */
  timezone: string
  location: string | null
  online_url: string | null
  cancelled: boolean
  counts: { going: number; maybe: number; not_going: number }
  my_response: 'going' | 'maybe' | 'not_going' | null
  can_manage: boolean
  message_id?: string
}

export interface GroupTask {
  id: string
  title: string
  assignee_id: string | null
  assignee_name: string | null
  due_at: string | null
  done: boolean
  done_at: string | null
  can_complete: boolean
  can_edit: boolean
  message_id?: string
}

/** A message as lists show it (pins, saved, replies, search). */
export interface MessageBrief {
  id: string
  conversation_id: string
  sender_id: string
  kind: string
  body: string | null
  encrypted: boolean
  deleted: boolean
  media_url?: string | null
  media_kind?: string | null
  media_name?: string | null
  media_type?: string | null
  media_size?: number | null
  media_withheld?: boolean
  created_at: string
}

export interface GroupInvitation {
  id: number
  conversation_id: string
  title: string | null
  description: string | null
  rules: string | null
  avatar_url: string | null
  members_count: number
  invited_by: string | null
  created_at: string
}

export interface GroupAuditEntry {
  id: number
  action: string
  summary: string
  detail: string | null
  actor_id: string
  target_id: string | null
  created_at: string
}

export interface LinkedGroupChat {
  id: string
  title: string | null
  description: string | null
  avatar_url: string | null
  privacy: 'public' | 'private'
  members_count: number
  my_state: 'member' | 'pending' | 'none'
  join_path: string | null
}

export type ReportReason = 'spam' | 'harassment' | 'hate' | 'violence' | 'sexual' | 'child_safety' | 'scam' | 'other'

export interface MessageReportItem {
  id: number
  conversation_id: string
  conversation_title: string | null
  conversation_kind: string | null
  target_kind: 'message' | 'member' | 'group'
  target_id: string
  target_name: string | null
  message: {
    id?: string
    sender_id?: string
    sender_name?: string | null
    body?: string | null
    encrypted?: boolean
    deleted?: boolean
    media_kind?: string | null
    created_at?: string
    gone?: boolean
  } | null
  reason: ReportReason
  note: string | null
  reporter_name: string | null
  status: 'open' | 'closed'
  resolution: string | null
  created_at: string
  closed_at: string | null
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
  /** What the viewer may do in it: owner and admin run it, a moderator keeps order. */
  my_role?: GroupRole
  /** Groups: the rules, shown before joining. */
  rules?: string | null
  /** Groups: "admins" is announcement mode - only admins and moderators write. */
  posting?: 'all' | 'admins'
  /** Groups: who sees the member list. */
  members_visible?: 'all' | 'admins'
  /** Groups: who starts polls. */
  polls_by?: 'all' | 'admins'
  /** Groups: the Community this chat is linked to (a pointer; it grants nothing). */
  community_id?: string | null
  disappear_after_seconds?: number
  /** Groups, for the people who run them: the share link's limits. */
  invite_link?: GroupInviteLink | null
  /** The viewer's own notification settings for this conversation. */
  notifications?: ConversationNotifications
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
  /** Groups: taken down by a moderator (or by Kinjy) rather than deleted by its sender. */
  removed?: boolean
  /** Groups: marked as an announcement by an admin. */
  announcement?: boolean
  pinned?: boolean
  /** Bookmarked by the reader. Private to them. */
  saved?: boolean
  /** Groups: how many replies it has (the side thread). */
  reply_count?: number
  /** The card a message of kind poll, event or task carries. */
  poll?: GroupPoll
  event?: GroupEvent
  task?: GroupTask
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
  /** Set on ebook listings. */
  ebook?: boolean
  subtitle?: string | null
  author?: string | null
  category?: string | null
  language?: string | null
}

/** What an ebook listing says about the book. Never the file's address. */
export interface EbookDetail {
  subtitle: string | null
  author: string | null
  summary: string | null
  category: string | null
  language: string | null
  published_on: string | null
  edition: string | null
  page_count: number | null
  isbn: string | null
  license: 'personal' | 'commercial'
  format: 'pdf' | 'epub' | null
  size_bytes: number | null
  version: number | null
  updated_at: string | null
  sample: { url: string; format: string } | null
  download_limit: number | null
  access_days: number | null
  delivery: string
  description_format: 'html'
  /** For a signed-in buyer who already owns it. */
  purchased_order_id?: string | null
  /** Owner only. */
  file?: { filename: string; version: number; size_bytes: number } | null
  suspended_reason?: string | null
  sample_asset_id?: string | null
}

export interface EbookInput {
  file_asset_id?: string | null
  sample_asset_id?: string | null
  subtitle?: string | null
  author?: string | null
  summary?: string | null
  category?: string | null
  language?: string | null
  published_on?: string | null
  edition?: string | null
  page_count?: number | null
  isbn?: string | null
  license?: 'personal' | 'commercial'
  download_limit?: number | null
  access_days?: number | null
}

export interface EbookTerms {
  formats: string[]
  max_file_mb: number
  max_sample_mb: number
  categories: string[]
  licenses: string[]
  link_minutes: number
  scanned_for_malware: boolean
  access: string
}

export interface LibraryItem {
  order_id: string
  status: string
  purchased_at: string
  product: { id: string; title: string; subtitle: string | null; author: string | null; images: string[]; available: boolean }
  seller: VendorBrief
  file: { format: string; size_bytes: number; version: number; updated_at: string } | null
  update_available: boolean
  state: 'active' | 'expired' | 'refunded' | 'revoked' | 'limit_reached' | 'unavailable' | 'unpaid'
  access_until: string | null
  downloads_used: number
  download_limit: number | null
}

export interface EbookDownloadLink {
  url: string
  expires_in: number
  filename: string
  format: string
  size_bytes: number
  version: number
  counted: boolean
  downloads_used: number
  download_limit: number | null
}

export interface ProductSales {
  orders_paid: number
  units_sold: number
  awaiting_release: number
  gross: string
  refunds: string
  net_released: string
  currency: string
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
  ebook?: EbookDetail
  /** What the buyer is told before paying. Null fields mean "none". */
  protection?: ProductProtection
}

/** A seller's guarantee or warranty, as they state it. */
export interface ProductGuarantee {
  scope: string
  duration: string
  exclusions: string
  claim_contact: string
}

/** Insurance included with a purchase, as the seller states it. */
export interface ProductInsurance {
  insurer: string
  coverage: string
  exclusions: string
  claim_process: string
  premium_included: boolean
}

export interface ProductProtection {
  guarantee: ProductGuarantee | null
  insurance: ProductInsurance | null
  /** A service: what "completed" means and how the buyer can check it. */
  service_terms: string | null
  /** buyer_confirmation (products, services) or first_download (ebooks). */
  final_when: 'buyer_confirmation' | 'first_download'
  /** Always false: Kinjy does not arbitrate ordinary marketplace disputes. */
  kinjy_arbitrates: boolean
  policy_version: string
}

/** The marketplace policy a buyer acknowledges (GET /commerce/marketplace/policy). */
export interface MarketplacePolicy {
  version: string
  checkout: string[]
  acceptance: string[]
  download: string[]
  handover_awaiting: string
  handover_verified: string
  payments: string
  automatic_release: boolean
  kinjy_arbitrates: boolean
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
  ebook?: boolean
  author?: string | null
  has_file?: boolean
  suspended_reason?: string | null
}

export interface ProductPatch {
  title?: string
  description?: string
  vendor_price?: string
  stock?: number | null
  country?: string | null
  city?: string | null
  images?: string[]
  status?: 'active' | 'paused' | 'draft'
  ebook?: EbookInput
  /** null states there is none. */
  guarantee?: ProductGuarantee | null
  insurance?: ProductInsurance | null
  service_terms?: string | null
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
  ebook?: boolean
  author?: string | null
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
  /**
   * pending | in_escrow | delivered | accepted | settled | refund_pending | refunded | cancelled
   * (disputed and part_refunded on orders from before 27/10 only)
   */
  status: string
  /** kalutapay | soncopay | mock (a development machine) */
  payment_rail: string | null
  escrow_funded_at: string | null
  delivered_at: string | null
  delivery_note: string | null
  escrow_released_at: string | null
  /** The purchase made final - the one record both back offices show. */
  acceptance: OrderAcceptance
  /** For the seller only: what to do with the goods now. */
  handover: string | null
  /** Separate from acceptance: "seller paid" only when confirmed. */
  settlement: { status: 'none' | 'pending' | 'confirmed' | 'action_required'; error: string | null; confirmed_at: string | null }
  cancellation: OrderCancellation | null
  /** Detail only (GET /commerce/orders/{id}). */
  protection?: ProductProtection | null
  final_when?: 'buyer_confirmation' | 'first_download'
  can_confirm?: boolean
  /** Only present for the buyer and the vendor of the order. */
  shipping?: OrderShipping | null
  carrier?: string | null
  tracking_number?: string | null
  tracking_url?: string | null
  created_at: string
}

export interface OrderAcceptance {
  accepted: boolean
  /** awaiting | buyer_confirmed | first_download */
  status: 'awaiting' | 'buyer_confirmed' | 'first_download'
  /** The server's time. */
  accepted_at: string | null
  reference: string | null
  kind: 'buyer_confirmation' | 'first_download' | null
  policy_version: string | null
}

export interface OrderCancellation {
  cancelled_at: string
  reason: 'cannot_supply' | 'refused_at_handover' | 'delivery_failed' | 'unpaid' | 'operational'
  note: string | null
  refund_status: 'none' | 'pending' | 'confirmed' | 'action_required'
  refund_error: string | null
}

export type SupportKind = 'payment_problem' | 'duplicate_charge' | 'not_handed_over' | 'file_problem' | 'fraud' | 'legal'

export interface SupportCase {
  id: string
  order_id: string
  role: 'buyer' | 'seller'
  kind: SupportKind
  message: string
  status: 'open' | 'closed'
  resolution_note: string | null
  created_at: string
  closed_at: string | null
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

/** Paying an order from the Kinjy balance: what it would take (server's figures). */
export type WalletPayQuote =
  | { enabled: false }
  | {
      enabled: true
      /** Paying from the balance needs a valid identity verification. */
      verified: boolean
      order_id: string
      amount: string
      currency: string
      available: string
      enough: boolean
    }

export interface WalletPayment {
  payment_id: string
  rail: 'wallet'
  order_id: string
  amount: string
  currency: string
  /** paid | processing (a service is catching up; nothing to do) */
  status: string
  paid_at: string | null
}

/** One movement of the balance: positive in, negative out. */
export interface WalletHistoryItem {
  id: string
  date: string
  amount: string
  currency: string
  type: string
  kind: string
  status: 'completed' | 'pending' | 'returned'
  reference: string | null
  journal_id: string | null
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
  /** The price of one unit (the same as entry_price). */
  unit_price: string
  currency: string
  max_units_per_purchase: number
  cap: number
  seats_taken: number
  seats_left: number
  units_sold: number
  /** The cap counts members; units per member are not capped. */
  cap_counts: 'members'
  members: number
  members_left: number
  /** Open to new members. */
  open: boolean
  /** Whether the reader may buy: members already in always can. */
  can_buy: boolean
  active_seats: number
  active_units: number
  current_round: {
    number: number
    started_at: string
    units_snapshot: number
    assigned: number
    remaining: number
  } | null
  my_seat: ({
    seat_id: string
    seat_number: number
    status: string
    assigned_count: number
    last_assigned_at: string | null
    joined_at: string
  } & PoolMemberUnits) | null
  note: string
  rotation_note: string
}

/** A payment as the provider knows it, for finding it there. */
export interface AdminPaymentBrief {
  id: string
  user_id: string
  purpose: string
  amount: string
  currency: string
  rail: string
  rail_name: string
  external_id: string | null
  status: string
  settled_at: string | null
  created_at: string
}

export interface AdminMemberBrief {
  display_name: string
  handle: string
  email: string
  status: string
}

export interface AdminJournal {
  id: string
  kind: string
  reference: string | null
  amount: string
  currency: string
  created_at: string
  description?: string | null
  reverses_journal_id?: string | null
  entries: Array<{ account: string; beneficiary_id: string | null; role: string | null; debit: string; credit: string; reason: string | null }>
}

export interface FraudSignals {
  window_days: number
  note: string
  counts: { sponsor_shared_ip: number; signup_bursts: number; age_probing: number }
  sponsor_shared_ip: Array<{
    member_id: string
    member_handle: string
    created_at: string
    sponsor_id: string
    sponsor_handle: string
    ip: string
    via_pool: boolean
  }>
  signup_bursts: Array<{ ip: string; day: string; accounts: number; first_at: string; last_at: string }>
  age_probing: Array<{ subject: string; refusals: number; jurisdiction: string | null; last_at: string; later_allowed: boolean }>
}

/** A member's units, as the pool counts them. */
export interface PoolMemberUnits {
  total_active_units: number
  units_in_current_round: number
  round_quota_remaining: number
  units_awaiting_next_round: number
  units_under_review: number
  referrals_received: number
}

export interface PoolPurchase {
  id: string
  member_id: string
  payment_ref: string
  units: number
  units_reversed: number
  active_units: number
  unit_price: string
  amount: string
  currency: string
  status: 'credited' | 'partially_reversed' | 'reversed' | 'review' | 'rejected'
  source: 'payment' | 'migration'
  active_from_round: number
  review_note: string | null
  created_at: string
}

export interface PoolAdminMember extends PoolMemberUnits {
  seat_id: string
  seat_number: number
  member_id: string
  handle: string | null
  display_name: string | null
  status: string
  round_quota_total: number
  round_assigned: number
  round_excluded_reason: string | null
}

export interface PoolRound {
  id: string
  number: number
  status: 'open' | 'closed'
  members: number
  units_snapshot: number
  assigned: number
  remaining: number
  started_at: string
  closed_at: string | null
  close_reason: string | null
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

/** A member's short link, as auth-service returns it. */
export interface ShareLinkOut {
  token: string
  url: string
  /** The page it opens, as a path on this site. */
  destination: string
  destination_url: string
  kind: string
  include_invitation: boolean
  active: boolean
  created_at: string | null
  disabled_at: string | null
  /** Opened by a person (crawlers excluded). A count; nothing about who. */
  opens?: number
  /** What it led to, counted apart. Only in the list of your links. */
  registrations?: number
  community_joins?: number
  /** Only on creation: false when an existing link was returned. */
  created?: boolean
}

export type OneToManyTarget = 'short_video' | 'newsletter' | 'translation'

export type OneToManyCapabilities = {
  short_video: { available: boolean; reason: string | null }
  newsletter: { available: boolean; reason: string | null }
  translation: { available: boolean; reason: string | null; langs: string[] }
}

export type OneToManyOutput = {
  id: number
  target: OneToManyTarget | string
  lang: string
  rtl: boolean
  status: 'queued' | 'processing' | 'completed' | 'failed' | 'cancelled'
  error: string | null
  attempts: number
  max_attempts: number
  provenance: string
  provider: string | null
  updated_at: string | null
  completed_at: string | null
  /** Made from an earlier version of the post. */
  stale: boolean
  /** Short video: whether it is read aloud (videos made before narration are silent). */
  narrated?: boolean
  /** Short video: "natural" (a neural voice of the post's language) or "basic" (synthetic fallback). */
  voice_quality?: 'natural' | 'basic' | null
  legacy: boolean
  user_edited: boolean
  content: string | null
  title: string | null
  preview_text: string | null
  html: string | null
  video_url: string | null
  download_url: string | null
  duration_s: number | null
  warning: string | null
}

export type OneToManyJob = {
  job_id: string
  post_id: string | null
  status: 'queued' | 'processing' | 'completed' | 'partial' | 'failed' | 'cancelled'
  created_at: string | null
  source: { deleted: boolean; reachable: boolean; visibility: string | null; lang: string | null }
  outputs: OneToManyOutput[]
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
     * One-to-Many: a post also made into a short video, a newsletter draft and
     * its translations. Asked for after the post exists; produced in the
     * background by creator-service, which reads the post itself. Only the
     * author can ask, and only the author sees what comes out.
     */
    capabilities: (lang: string) =>
      api.get<{ targets: OneToManyCapabilities }>(`/creators/publish/capabilities?lang=${encodeURIComponent(lang)}`),

    publish: (input: { source_ref: string; targets: OneToManyTarget[] }) =>
      api.post<OneToManyJob & { created: boolean; added: number }>('/creators/publish', input),

    job: (jobId: string) => api.get<OneToManyJob>(`/creators/publish/${jobId}`),

    jobForPost: (postId: string) =>
      api.get<{ job: OneToManyJob | null }>(`/creators/publish/by-post/${encodeURIComponent(postId)}`),

    retryOutput: (outputId: number, force = false) =>
      api.post<OneToManyJob>(`/creators/publish/outputs/${outputId}/retry${force ? '?force=true' : ''}`),

    retryFailed: (jobId: string) =>
      api.post<OneToManyJob & { requeued: number }>(`/creators/publish/${jobId}/retry-failed`),

    cancelOutput: (outputId: number) => api.post<OneToManyJob>(`/creators/publish/outputs/${outputId}/cancel`),

    /** Save an edited newsletter draft. Saved only: Kinjy never sends it. */
    editNewsletter: (outputId: number, input: { subject: string; preview_text: string; html: string }) =>
      api.patch<OneToManyJob>(`/creators/publish/outputs/${outputId}`, input),

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
    /** Block somebody: no messages, invitations or group additions either way. */
    block: (userId: string) => api.post<{ blocked: boolean }>(`/users/${userId}/block`),
    unblock: (userId: string) => api.delete<void>(`/users/${userId}/block`),
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
    /** Save a post to read again (private to you), or take it off. */
    bookmark: (id: string) => api.post<{ bookmarked: boolean }>(`/posts/${id}/bookmark`),
    unbookmark: (id: string) => api.delete<{ bookmarked: boolean }>(`/posts/${id}/bookmark`),
    bookmarks: (offset = 0) => api.get<{ items: Post[] }>(`/bookmarks?offset=${offset}`),
    /** What an edited post said before, newest edit first. */
    revisions: (id: string) =>
      api.get<{ items: Array<{ body: string; written_at: string | null; replaced_at: string }> }>(`/posts/${id}/revisions`),
    /** Community stewards: pin to the top of the community, or unpin. */
    pin: (id: string) => api.post<{ pinned: boolean }>(`/posts/${id}/pin`),
    unpin: (id: string) => api.delete<{ pinned: boolean }>(`/posts/${id}/pin`),
    /** Kinjy admins: pin a public post above everything in everybody's feed. */
    feature: (id: string) => api.post<{ featured: boolean }>(`/posts/${id}/feature`),
    unfeature: (id: string) => api.delete<{ featured: boolean }>(`/posts/${id}/feature`),
    /** Community stewards: approve or reject a waiting post, or remove a published one. */
    moderate: (id: string, action: 'approve' | 'reject' | 'remove', reason?: string) =>
      api.post<Post>(`/posts/${id}/moderate`, { action, reason }),
    /** The asker or a steward marks a comment as the answer (null clears it). */
    acceptAnswer: (id: string, commentId: string | null) =>
      api.post<{ accepted_comment_id: string | null }>(`/posts/${id}/accept-answer`, { comment_id: commentId }),
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
    /** The author: who this post goes to and who can see it now. */
    audience: (id: string) => api.get<PostAudience>(`/posts/${id}/audience`),
    /** A change that lets new people in answers 409 (AudienceWidening) until confirmed. */
    changeAudience: (id: string, change: AudienceChange) =>
      api.patch<{ visibility: string; removed: number; added: number }>(`/posts/${id}/audience`, change),
    /** A reader of a circle post: the private audiences it came through, and their choices. */
    audienceControls: (id: string) =>
      api.get<{ author?: string; items: AudienceControl[]; explanation?: string }>(`/posts/${id}/audience-controls`),
    setAudiencePref: (input: {
      post_id: string
      token: string
      muted?: boolean
      hidden?: boolean
      opt_out?: boolean
      opt_out_past?: boolean
    }) => api.put<AudiencePrefState>('/audience-prefs', input),
    /** Every private audience I made a choice about, to review or undo. */
    audiencePrefs: () => api.get<{ items: AudiencePrefRow[] }>('/audience-prefs'),
    changeAudiencePref: (
      id: number,
      change: { muted?: boolean; hidden?: boolean; opt_out?: boolean; opt_out_past?: boolean },
    ) => api.patch<AudiencePrefState | { removed: true }>(`/audience-prefs/${id}`, change),
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
        purpose?: 'post' | 'chat' | 'memorial' | 'ebook' | 'ebook_sample'
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
    translate: (text: string, target_lang: string, source_lang?: string, requireLive = false) =>
      api.post<{
        translated: string
        source_lang: string
        provider?: string
        mock?: boolean
        cached: boolean
        noop?: boolean
      }>(
        '/ai/translate',
        // require_live: answer 503 rather than a placeholder when no model is configured.
        { text, target_lang, source_lang, ...(requireLive ? { require_live: true } : {}) },
        { auth: false },
      ),
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
    list: (opts: { sort?: CircleSort; archived?: boolean } = {}) =>
      api.get<Circle[]>(
        `/circles?sort=${opts.sort ?? 'created'}${opts.archived === undefined ? '' : `&archived=${opts.archived}`}`,
      ),
    /** One circle and its members — its owner only. `q` narrows the members. */
    get: (id: string, q?: string) =>
      api.get<CircleDetail>(`/circles/${id}${q ? `?q=${encodeURIComponent(q)}` : ''}`),
    create: (input: { name: string; kind?: string; color?: string; rule?: CircleRule; description?: string; icon?: string }) =>
      api.post<CircleDetail>('/circles', { kind: 'custom', ...input }),
    update: (
      id: string,
      patch: {
        name?: string
        kind?: string
        color?: string | null
        rule?: CircleRule
        description?: string | null
        icon?: string | null
        archived?: boolean
      },
    ) => api.patch<CircleDetail>(`/circles/${id}`, patch),
    bulk: (id: string, change: { add?: string[]; remove?: string[] }) =>
      api.post<{ added: string[]; removed: number; refused: string[]; members_count: number }>(
        `/circles/${id}/bulk-members`,
        { add: change.add ?? [], remove: change.remove ?? [] },
      ),
    duplicate: (id: string, name: string) => api.post<CircleDetail>(`/circles/${id}/duplicate`, { name }),
    exclusions: (id: string) => api.get<{ items: AudiencePerson[] }>(`/circles/${id}/exclusions`),
    exclude: (id: string, memberId: string) =>
      api.post<{ excluded: boolean; members_count: number }>(`/circles/${id}/exclusions/${memberId}`),
    unexclude: (id: string, memberId: string) => api.delete<void>(`/circles/${id}/exclusions/${memberId}`),
    /** Who a smart rule would reach right now, before saving it. */
    smartPreview: (rule: CircleRule) =>
      api.post<{ count: number; items: AudiencePerson[] }>('/circles/smart-preview', rule),
    /** Who a post with this audience would reach — for its author. */
    audiencePreview: (selection: AudienceSelection, q?: string) =>
      api.post<AudiencePreview>(`/circles/audience-preview${q ? `?q=${encodeURIComponent(q)}` : ''}`, selection),
    /** What I shared with one of my circles, scheduled posts included. */
    posts: (id: string) =>
      api.get<{ items: CirclePostRow[]; opted_out_count: number | null }>(`/me/circle-posts?circle_id=${id}`),
    optOuts: (ids: string[]) =>
      api.get<{ counts: Record<string, number | null> }>(`/me/circle-opt-outs?ids=${ids.join(',')}`),
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
     * Group chats (in Messages) linked to a community. Shown to whoever may
     * enter the community; the link grants nothing either way.
     */
    groupChats: (handle: string) =>
      api.get<{ items: LinkedGroupChat[]; can_manage: boolean }>(`/communities/${handle}/group-chats`),
    linkGroupChat: (handle: string, conversationId: string) =>
      api.post<{ linked: boolean }>(`/communities/${handle}/group-chats`, { conversation_id: conversationId }),
    unlinkGroupChat: (handle: string, conversationId: string) =>
      api.delete<{ linked: boolean }>(`/communities/${handle}/group-chats/${conversationId}`),
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
      } & CommunitySpaceView>(`/communities/${id}${invite ? `?invite=${encodeURIComponent(invite)}` : ''}`),

    /** The starting configurations, and every space with whether it exists yet. */
    templates: () =>
      api.get<{ templates: CommunityTemplate[]; spaces: CommunitySpace[] }>('/community-templates', { auth: false }),
    /** Switch spaces on or off. The owner's; a space not built yet answers 409. */
    setSpaces: (id: string, spaces: Record<string, boolean>) =>
      api.patch<CommunitySpaceView>(`/communities/${id}/spaces`, { spaces }),
    /** Template, post approval, welcome message and rules. The owner's. */
    settings: (
      id: string,
      patch: { template?: string; post_approval?: 'none' | 'new_members' | 'all'; welcome_message?: string; rules?: string },
    ) => api.patch<CommunitySpaceView>(`/communities/${id}/settings`, patch),
    /** What the owner and moderators did, newest first. For them only. */
    audit: (id: string, offset = 0) =>
      api.get<{
        total: number
        items: Array<{
          id: number
          action: string
          actor: { handle?: string; display_name?: string; id?: string }
          target_kind: string | null
          target_id: string | null
          target: { handle: string; display_name: string } | null
          reason: string | null
          created_at: string
        }>
      }>(`/communities/${id}/audit?offset=${offset}`),
    /** Posts waiting for approval. Stewards only (served by social-service). */
    pendingPosts: (id: string) => api.get<{ items: Post[] }>(`/feed/community/${id}/pending`),

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

    act: (
      id: string,
      userId: string,
      action: 'approve' | 'reject' | 'ban' | 'unban' | 'promote' | 'demote' | 'remove',
      reason?: string,
    ) => api.post<{ ok: boolean; action: string }>(`/communities/${id}/members/${userId}`, { action, reason }),

    list: (params: { q?: string; country?: string; limit?: number; mine?: boolean } = {}) => {
      const query = new URLSearchParams()
      if (params.q) query.set('q', params.q)
      if (params.mine) query.set('mine', 'true')
      if (params.country) query.set('country', params.country)
      query.set('limit', String(params.limit ?? 30))
      // Signed in when there is a session, so each card can say Joined / Request sent.
      return api.get<{ total: number; items: Community[] }>(`/communities?${query}`)
    },
    create: (input: { name: string; description?: string; kind?: string; price_usd?: number; country?: string; template?: string }) =>
      api.post<{ id: string; slug: string; kind: string }>('/communities', { kind: 'public', ...input }),
    /**
     * Join. `via` is the short link the member arrived on: it records who
     * invited them into this community, never their account's sponsor.
     */
    join: (id: string, invite?: string | null, via?: string | null) => {
      const query = new URLSearchParams()
      if (invite) query.set('invite', invite)
      if (via) query.set('via', via)
      const qs = query.toString()
      return api.post<{ joined: boolean; status: string }>(`/communities/${id}/join${qs ? `?${qs}` : ''}`)
    },
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
    feed: (id: string, limit = 20, offset = 0, space?: CommunityPostSpace, sort: 'latest' | 'top' = 'latest') =>
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
        /** The reader's own posts waiting for approval or for their time. */
        my_queued?: Post[]
      }>(
        // Signed in when there is a session (no token is sent otherwise): the
        // server answers membership-dependent things - whether the reader may
        // take part, their own queued posts, what they saved - for the reader.
        `/feed/community/${id}?limit=${limit}&offset=${offset}${space ? `&space=${space}` : ''}${sort !== 'latest' ? `&sort=${sort}` : ''}`,
      ),
  },

  /** Paid communities, events, listings and analytics (community-service). */
  communityPaid: {
    tiers: (id: string) =>
      api.get<{
        paid_enabled: boolean
        access_model: 'free' | 'paid' | 'mixed'
        markup_pct: string
        grace_days: number
        tiers: CommunityTier[]
        my_entitlement: CommunityEntitlement | null
      }>(`/communities/${id}/tiers`),
    createTier: (
      id: string,
      tier: { name: string; description?: string; benefits?: string[]; price: number; period: string; trial_days?: number; intro_price?: number },
    ) => api.post<CommunityTier>(`/communities/${id}/tiers`, tier),
    editTier: (id: string, tierId: string, patch: { name?: string; description?: string; benefits?: string[]; active?: boolean }) =>
      api.patch<CommunityTier>(`/communities/${id}/tiers/${tierId}`, patch),
    setAccess: (id: string, patch: { access_model?: 'free' | 'paid' | 'mixed'; paid_spaces?: Record<string, boolean> }) =>
      api.patch<{ access_model: string; paid_spaces: string[] }>(`/communities/${id}/access`, patch),
    coupons: (id: string) =>
      api.get<{ items: Array<{ id: string; code: string; percent_off: number; max_redemptions: number | null; redemptions: number; expires_at: string | null; active: boolean }> }>(
        `/communities/${id}/coupons`,
      ),
    createCoupon: (id: string, coupon: { code: string; percent_off: number; max_redemptions?: number; expires_at?: string }) =>
      api.post(`/communities/${id}/coupons`, coupon),
    disableCoupon: (id: string, couponId: string) => api.post(`/communities/${id}/coupons/${couponId}/disable`, {}),
    /** The price for this member, computed by the server. */
    quote: (id: string, input: { tier_id?: string; event_id?: string; coupon?: string }) =>
      api.post<CommunityQuote>(`/communities/${id}/quote`, input),
    trial: (id: string, tierId: string) => api.post<CommunityEntitlement>(`/communities/${id}/trial`, { tier_id: tierId }),
    /** Start paying a quote (Kaluta Pay / Sonco Pay, as for every payment). */
    checkout: (quoteId: string) =>
      api.post<{ payment_id: string; rail: string; amount: string; checkout_url?: string; mock?: boolean }>(
        '/payments/community/checkout',
        { quote_id: quoteId },
      ),
    billing: (id: string) =>
      api.get<{ entitlement: CommunityEntitlement | null; payments: CommunityPaymentRow[]; grace_days: number }>(
        `/communities/${id}/billing`,
      ),
    cancel: (id: string) => api.post<CommunityEntitlement>(`/communities/${id}/membership/cancel`, {}),
    resume: (id: string) => api.post<CommunityEntitlement>(`/communities/${id}/membership/resume`, {}),
    mine: () =>
      api.get<{
        items: Array<
          CommunityEntitlement & {
            community: { name: string; slug: string } | null
            tier: { name: string; period: string } | null
          }
        >
      }>('/me/community-memberships'),
    subscribers: (id: string) => api.get<{ items: Array<CommunityEntitlement & { user_id: string }> }>(`/communities/${id}/subscribers`),
    earnings: (id: string) =>
      api.get<{
        currency: string
        members_paid: string
        kinjy_fees: string
        owner_gross: string
        refunded_to_members: string
        owner_refunded: string
        owner_net: string
        by_kind: Record<string, string>
        payments: CommunityPaymentRow[]
        note: string
      }>(`/communities/${id}/earnings`),
    refund: (id: string, paymentId: string, amount: number, reason: string, requestId: string) =>
      api.post<{ refund_id: string; status: string; payment: CommunityPaymentRow }>(
        `/communities/${id}/payments/${paymentId}/refund`,
        { amount, reason, request_id: requestId },
      ),
    events: (id: string, past = false) =>
      api.get<{ items: CommunityEvent[] }>(`/communities/${id}/events${past ? '?past=true' : ''}`),
    createEvent: (
      id: string,
      event: {
        title: string
        description?: string
        starts_at: string
        ends_at?: string
        timezone: string
        mode: string
        location?: string
        online_url?: string
        capacity?: number
        waitlist?: boolean
        ticket_price?: number
        speakers?: string[]
        recording_consent?: boolean
      },
    ) => api.post<CommunityEvent>(`/communities/${id}/events`, event),
    /** One event, by its own address (/events/<id>); the community's rules decide who sees it. */
    event: (eventId: string) => api.get<CommunityEvent>(`/events/${eventId}`),
    updateEvent: (eventId: string, patch: Partial<EventInput>) => api.patch<CommunityEvent>(`/events/${eventId}`, patch),
    /** Upcoming events a member hosts, in communities the reader may read. */
    hostedEvents: (userId: string) => api.get<{ items: CommunityEvent[] }>(`/events/by-host/${userId}`),
    register: (eventId: string) => api.post<CommunityEvent>(`/events/${eventId}/register`, {}),
    unregister: (eventId: string) => api.post<CommunityEvent>(`/events/${eventId}/unregister`, {}),
    cancelEvent: (eventId: string) => api.post<{ cancelled: boolean; told: number }>(`/events/${eventId}/cancel`, {}),
    /** The members presenting at an event (host and moderators only). */
    presenters: (eventId: string) => api.get<{ items: EventPresenter[] }>(`/events/${eventId}/presenters`),
    addPresenter: (eventId: string, handle: string) =>
      api.post<{ items: EventPresenter[] }>(`/events/${eventId}/presenters`, { handle }),
    removePresenter: (eventId: string, userId: string) =>
      api.delete<{ items: EventPresenter[] }>(`/events/${eventId}/presenters/${userId}`),
    attendees: (eventId: string) =>
      api.get<{ items: Array<{ user_id: string; status: string; paid: boolean; created_at: string }> }>(`/events/${eventId}/attendees`),
    listings: (id: string) =>
      api.get<{ items: Array<{ id: string; title: string; description: string; kind: string; customer_price: string; currency: string; images: string[]; vendor?: { display_name?: string } | null; listed_by: string }> }>(
        `/communities/${id}/listings`,
      ),
    addListing: (id: string, productId: string) => api.post(`/communities/${id}/listings`, { product_id: productId }),
    removeListing: (id: string, productId: string) => api.delete(`/communities/${id}/listings/${productId}`),
    analytics: (id: string, days = 30) =>
      api.get<{
        window_days: number
        definitions: Record<string, string>
        members_total: number
        new_members: number
        via_invitations: number
        activated: number
        active_contributors: number
        posts: number | null
        comments: number | null
        unanswered: number | null
        pending_posts: number | null
        moderation_actions: number
        event_registrations: number
        upcoming_events: number
        money: { sales: number; owner_gross: string; renewals: number; cancellations: number; refunds: string; currency: string } | null
      }>(`/communities/${id}/analytics?days=${days}`),
    /** Admin: community markup, grace days, the switch for paid features. */
    adminSettings: () =>
      api.get<Record<'community_markup_pct' | 'community_grace_days' | 'community_paid_enabled', { value: string; default: string; updated_by: string | null; updated_at: string | null }>>(
        '/admin/community-settings',
      ),
    setAdminSettings: (patch: { community_markup_pct?: number; community_grace_days?: number; community_paid_enabled?: boolean }) =>
      api.patch('/admin/community-settings', patch),
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
      }>(`/threads/${threadId}/summary?lang=${lang}`),

    // Forum reads carry the sign-in when there is one: a forum of a private
    // community is for its members, and an anonymous read would refuse them.
    list: (
      params: {
        hierarchy?: string
        scope?: string
        community_id?: string
        category?: string
        lang?: string
        country?: string
        tag?: string
        q?: string
        followed?: boolean
      } = {},
    ) => {
      const query = new URLSearchParams()
      for (const [key, value] of Object.entries(params)) {
        if (value) query.set(key, String(value))
      }
      return api.get<{ geo_scopes: string[]; categories?: string[]; items: Forum[] }>(`/forums?${query}`)
    },
    /** With `community_id`, the forum belongs to that community (owner and moderators only). */
    create: (input: {
      name: string
      description?: string
      hierarchy?: string
      scope?: string
      scope_value?: string
      community_id?: string
      category?: string
      tags?: string[]
      lang?: string
      country?: string
      visibility?: 'inherit' | 'public' | 'members' | 'private'
      post_policy?: ForumPolicy
      reply_policy?: ForumPolicy
      rules?: string
    }) => api.post<{ id: string; slug: string }>('/forums', { hierarchy: 'topic', ...input }),
    detail: (forumId: string) => api.get<ForumDetail>(`/forums/${forumId}`),
    update: (
      forumId: string,
      input: Partial<{
        name: string
        description: string
        category: string
        tags: string[]
        lang: string
        country: string
        visibility: 'inherit' | 'public' | 'members' | 'private'
        post_policy: ForumPolicy
        reply_policy: ForumPolicy
        rules: string
        review_days: number
      }>,
    ) => api.patch<{ changed: string[] }>(`/forums/${forumId}`, input),
    threads: (
      forumId: string,
      sort: ThreadSort = 'recent',
      filters: { q?: string; kind?: ThreadKind; lang?: string; country?: string; tag?: string; session_id?: string } = {},
    ) => {
      const query = new URLSearchParams({ sort })
      for (const [key, value] of Object.entries(filters)) {
        if (value) query.set(key, value)
      }
      return api.get<{ items: Thread[] }>(`/forums/${forumId}/threads?${query}`)
    },
    createThread: (
      forumId: string,
      input: {
        title: string
        body: string
        lang?: string
        kind?: ThreadKind
        tags?: string[]
        country?: string
        region?: string
        commercial?: boolean
        attachments?: string[]
        poll?: { question: string; options: string[]; multiple?: boolean; closes_at?: string | null }
        event_id?: string
        session_id?: string
        draft_id?: string
      },
    ) => api.post<{ id: string }>(`/forums/${forumId}/threads`, input),
    thread: (threadId: string) => api.get<ThreadDetail>(`/threads/${threadId}`),
    /** `parentId` makes it a comment on that answer. */
    reply: (threadId: string, body: string, options: { parentId?: string; attachments?: string[]; commercial?: boolean } = {}) =>
      api.post<{ id: string }>(`/threads/${threadId}/replies`, {
        body,
        parent_id: options.parentId,
        attachments: options.attachments,
        commercial: options.commercial,
      }),
    editThread: (
      threadId: string,
      input: Partial<{ title: string; body: string; tags: string[]; lang: string; country: string; region: string; commercial: boolean }>,
    ) => api.patch<{ id: string; edited_at: string }>(`/threads/${threadId}`, input),
    editReply: (replyId: string, body: string) => api.patch<{ id: string; edited_at: string }>(`/replies/${replyId}`, { body }),
    /** The asker says the accepted answer no longer settles it. */
    reopen: (threadId: string, reason?: string) => api.post<{ reopened: boolean }>(`/threads/${threadId}/reopen`, { reason }),
    followThread: (threadId: string, input: { following?: boolean; saved?: boolean }) =>
      api.post<{ following: boolean; saved: boolean }>(`/threads/${threadId}/follow`, input),
    votePoll: (threadId: string, options: number[]) => api.post<ForumPoll>(`/threads/${threadId}/poll/vote`, { options }),
    follow: (forumId: string, input: { notify_new?: boolean; digest?: boolean } = {}) =>
      api.post<{ following: boolean; notify_new: boolean; digest: boolean }>(`/forums/${forumId}/follow`, input),
    unfollow: (forumId: string) => api.delete<{ following: boolean }>(`/forums/${forumId}/follow`),
    /** Across every forum the member may read, or within one. */
    search: (q: string, filters: { forum_id?: string; kind?: ThreadKind; lang?: string; country?: string; tag?: string } = {}) => {
      const query = new URLSearchParams({ q })
      for (const [key, value] of Object.entries(filters)) {
        if (value) query.set(key, value)
      }
      return api.get<{ threads: ThreadBrief[]; knowledge: Array<KnowledgeEntry & { forum_id: string }> }>(`/forums/search?${query}`)
    },
    related: (title: string, forumId?: string) =>
      api.get<{ items: ThreadBrief[] }>(
        `/forums/related?title=${encodeURIComponent(title)}${forumId ? `&forum_id=${forumId}` : ''}`,
      ),
    recommended: () => api.get<{ items: Array<Forum & { reason: string }> }>('/forums/recommended'),
    saved: () => api.get<{ items: ThreadBrief[] }>('/forum-saved'),
    drafts: (forumId?: string) =>
      api.get<{
        items: Array<{ id: string; forum_id: string; title: string; body: string; kind: ThreadKind; tags: string[]; lang: string | null; country: string | null; region: string | null; updated_at: string }>
      }>(`/forum-drafts${forumId ? `?forum_id=${forumId}` : ''}`),
    saveDraft: (input: { id?: string; forum_id: string; title: string; body: string; kind: ThreadKind; tags?: string[]; lang?: string; country?: string; region?: string }) =>
      api.post<{ id: string; updated_at: string }>('/forum-drafts', input),
    deleteDraft: (draftId: string) => api.delete<{ deleted: boolean }>(`/forum-drafts/${draftId}`),
    prefs: () => api.get<ForumPrefs>('/forum-prefs'),
    setPrefs: (input: Partial<Omit<ForumPrefs, 'topics_followed'>>) => api.patch<ForumPrefs>('/forum-prefs', input),
    followTopic: (tag: string) => api.post<{ tag: string; following: boolean }>(`/forum-topics/${encodeURIComponent(tag)}/follow`),
    unfollowTopic: (tag: string) => api.delete<{ following: boolean }>(`/forum-topics/${encodeURIComponent(tag)}/follow`),
    profile: (userId: string) =>
      api.get<{
        user_id: string
        display_name: string | null
        handle: string | null
        account_verified: boolean
        background: string | null
        background_note: string | null
        show_activity: boolean
        stats: { answers: number; helpful_votes: number; accepted: number; knowledge: number; badges: Array<{ key: string; label: string }> } | null
        recognition_note: string
      }>(`/forum-profiles/${userId}`),
    // --- moderation ---
    moderators: {
      add: (forumId: string, input: { user_id: string; can_moderate?: boolean; can_edit_knowledge?: boolean }) =>
        api.post<{ user_id: string }>(`/forums/${forumId}/moderators`, input),
      remove: (forumId: string, userId: string) => api.delete<{ removed: boolean }>(`/forums/${forumId}/moderators/${userId}`),
    },
    moderateThread: (
      threadId: string,
      action: 'pin' | 'unpin' | 'lock' | 'unlock' | 'hide' | 'restore' | 'mark_commercial' | 'unmark_commercial',
      reason?: string,
    ) => api.post<{ id: string; pinned: boolean; locked: boolean; status: string }>(`/threads/${threadId}/moderate`, { action, reason }),
    moderateReply: (replyId: string, action: 'hide' | 'restore' | 'mark_commercial' | 'unmark_commercial', reason?: string) =>
      api.post<{ id: string; status: string }>(`/replies/${replyId}/moderate`, { action, reason }),
    moveThread: (threadId: string, forumId: string, reason: string, acknowledge = false) =>
      api.post<{ id: string; forum_id: string }>(`/threads/${threadId}/move`, {
        forum_id: forumId,
        reason,
        acknowledge_audience_change: acknowledge,
      }),
    mergeThread: (threadId: string, intoThreadId: string, reason: string, acknowledge = false) =>
      api.post<{ merged_into: string; replies_moved: number }>(`/threads/${threadId}/merge`, {
        into_thread_id: intoThreadId,
        reason,
        acknowledge_audience_change: acknowledge,
      }),
    report: (targetKind: 'thread' | 'reply' | 'knowledge', targetId: string, reason: string, note?: string) =>
      api.post<{ received: boolean }>('/forum-reports', { target_kind: targetKind, target_id: targetId, reason, note }),
    requestReview: (targetKind: 'thread' | 'reply', targetId: string, note: string) =>
      api.post<{ received: boolean; reason_given: string | null }>('/forum-reviews', { target_kind: targetKind, target_id: targetId, note }),
    moderation: (forumId: string, status: 'open' | 'closed' = 'open') =>
      api.get<ForumModerationQueue>(`/forums/${forumId}/moderation?status=${status}`),
    resolveReport: (reportId: number, resolution: string) =>
      api.post<{ closed: boolean }>(`/forum-reports/${reportId}/resolve`, { resolution }),
    audit: (forumId: string) =>
      api.get<{ items: Array<{ id: number; actor: string | null; action: string; target_kind: string | null; target_id: string | null; reason: string | null; created_at: string }> }>(
        `/forums/${forumId}/audit`,
      ),
    createSession: (forumId: string, input: { title: string; description?: string; expert_id: string; expert_intro?: string; starts_at: string; ends_at: string }) =>
      api.post<{ id: string }>(`/forums/${forumId}/sessions`, input),
    cancelSession: (sessionId: string) => api.post<{ cancelled: boolean }>(`/expert-sessions/${sessionId}/cancel`),
    communityActivity: (handle: string) => api.get<{ items: ThreadBrief[] }>(`/communities/${handle}/forum-activity`),
    /** What a forum has settled, with the discussions it came from. */
    knowledge: (forumId: string, q = '') =>
      api.get<{ items: KnowledgeEntry[]; can_edit?: boolean; review_days?: number }>(
        `/forums/${forumId}/knowledge${q ? `?q=${encodeURIComponent(q)}` : ''}`,
      ),
    article: {
      create: (
        forumId: string,
        input: {
          question: string
          answer: string
          source_thread_ids?: string[]
          source_reply_ids?: string[]
          source_links?: Array<{ url: string; label?: string }>
          tags?: string[]
          publish?: boolean
        },
      ) => api.post<{ id: string; status: string }>(`/forums/${forumId}/knowledge`, input),
      edit: (
        entryId: string,
        input: { question?: string; answer?: string; source_links?: Array<{ url: string; label?: string | null }>; tags?: string[]; note?: string; mark_reviewed?: boolean },
      ) => api.patch<{ id: string }>(`/knowledge/${entryId}`, input),
      publish: (entryId: string) => api.post<{ id: string; status: string }>(`/knowledge/${entryId}/publish`),
      revisions: (entryId: string) =>
        api.get<{ items: Array<{ id: number; editor: string | null; question: string; answer: string; note: string | null; created_at: string }> }>(
          `/knowledge/${entryId}/revisions`,
        ),
      suggest: (entryId: string, kind: 'correction' | 'outdated', note: string) =>
        api.post<{ received: boolean }>(`/knowledge/${entryId}/suggestions`, { kind, note }),
      suggestions: (entryId: string) =>
        api.get<{ items: Array<{ id: number; kind: string; note: string; status: string; by: string | null; created_at: string }> }>(
          `/knowledge/${entryId}/suggestions`,
        ),
      resolveSuggestion: (suggestionId: number) => api.post<{ closed: boolean }>(`/knowledge-suggestions/${suggestionId}/resolve`),
      /** An AI draft for an editor to check; refused when no real model is configured. */
      aiDraft: (threadId: string) => api.post<{ id: string; status: string; ai_generated: boolean }>(`/threads/${threadId}/knowledge-draft`),
    },
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
    /**
     * A named room. Groups are never end to end: there is no group key.
     * People who only accept invitations from the creator are invited, not
     * added (`invited`); anybody who accepts neither makes it fail.
     */
    createGroup: (input: {
      title: string
      description?: string
      rules?: string
      participantIds: string[]
      avatarAssetId?: string | null
      coverAssetId?: string | null
    }) =>
      api.post<{ id: string; encrypted: boolean; invited?: string[] }>('/conversations', {
        participant_ids: input.participantIds,
        kind: 'group',
        title: input.title,
        description: input.description,
        rules: input.rules,
        avatar_asset_id: input.avatarAssetId || undefined,
        cover_asset_id: input.coverAssetId || undefined,
        encrypted: false,
      }),
    /**
     * Who is in a room and who runs it, a page at a time. Members only.
     *
     * Paged because the welcome group holds the whole platform: admins come
     * first, so the first page is the part anybody actually reads.
     */
    participants: (conversationId: string, offset = 0) =>
      api.get<{
        total: number
        items: GroupMember[]
        offset: number
        has_more: boolean
        /** The group's real size, and whether the list is only the people who run it. */
        members_count?: number
        limited?: boolean
      }>(`/conversations/${conversationId}/participants?offset=${offset}`),
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
        rules?: string
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
        rules?: string | null
      }>(`/conversations/${conversationId}`, input),
    /** Adds whoever allows it; invites whoever only accepts invitations. */
    addMembers: (conversationId: string, userIds: string[]) =>
      api.post<{ added: string[]; invited?: string[] }>(`/conversations/${conversationId}/participants`, {
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
        rules?: string | null
        /** Why the link cannot be used now (off, expired, used up), or null. */
        unavailable?: string | null
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
    setRole: (conversationId: string, userId: string, role: 'admin' | 'moderator' | 'member') =>
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
      return api.get<{
        items: Message[]
        profiles?: Record<string, PersonBrief>
        mentions?: Record<string, string>
        /** Groups: senders on this page the reader has a block with. */
        blocked?: string[]
      }>(
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
      api.delete<{ id: string; deleted: boolean; removed?: boolean }>(
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

    /** What works here: translation needs a real model; group calls a media server. */
    capabilities: () =>
      api.get<{ translate: boolean; group_calls: boolean; summaries: boolean }>('/conversations/capabilities'),

    // --- groups: settings, the link, ownership --------------------------------
    /** Announcement mode, who sees the member list, who starts polls. Admins. */
    setGroupSettings: (
      conversationId: string,
      input: { posting?: 'all' | 'admins'; members_visible?: 'all' | 'admins'; polls_by?: 'all' | 'admins' },
    ) => api.patch<{ posting: string; members_visible: string; polls_by: string }>(`/conversations/${conversationId}/settings`, input),
    /** Turn the link on or off, or limit it. null removes a limit. Admins. */
    setInviteLink: (
      conversationId: string,
      input: { enabled?: boolean; expires_at?: string | null; max_uses?: number | null },
    ) => api.patch<GroupInviteLink>(`/conversations/${conversationId}/invite-link`, input),
    /** A new link; the old one stops working. Admins. */
    resetInviteLink: (conversationId: string) =>
      api.post<GroupInviteLink>(`/conversations/${conversationId}/invite-link/reset`),
    /** Hand the group on. The owner stays as an admin. */
    transferOwnership: (conversationId: string, userId: string) =>
      api.post<{ owner: string; your_role: string }>(`/conversations/${conversationId}/transfer`, { user_id: userId }),
    /** Delete the group for everybody. The owner, typing its name. */
    deleteGroup: (conversationId: string, confirmTitle: string) =>
      api.post<{ deleted: boolean }>(`/conversations/${conversationId}/delete`, { confirm_title: confirmTitle }),
    audit: (conversationId: string) =>
      api.get<{ items: GroupAuditEntry[] }>(`/conversations/${conversationId}/audit`),

    // --- invitations ------------------------------------------------------------
    invite: (conversationId: string, userIds: string[]) =>
      api.post<{ invited: string[] }>(`/conversations/${conversationId}/invites`, { user_ids: userIds }),
    pendingInvites: (conversationId: string) =>
      api.get<{ items: Array<GroupMember & { invited_by: string; created_at: string }> }>(
        `/conversations/${conversationId}/invites`,
      ),
    cancelInvite: (conversationId: string, userId: string) =>
      api.delete<{ cancelled: boolean }>(`/conversations/${conversationId}/invites/${userId}`),
    myInvites: () => api.get<{ items: GroupInvitation[] }>('/conversations/invites'),
    answerInvite: (inviteId: number, action: 'accept' | 'decline') =>
      api.post<{ status: string; conversation_id?: string }>(`/conversations/invites/${inviteId}`, { action }),

    // --- per member -------------------------------------------------------------
    setNotifications: (
      conversationId: string,
      input: { level?: 'all' | 'mentions' | 'announcements'; mute?: '1h' | '8h' | '1d' | '1w' | 'forever' | 'off'; read_receipts?: boolean },
    ) => api.patch<ConversationNotifications>(`/conversations/${conversationId}/notifications`, input),
    receipts: (conversationId: string, messageId: string) =>
      api.get<{ read: number; members: number; readers: Array<{ user_id: string; display_name: string }> | null }>(
        `/conversations/${conversationId}/messages/${messageId}/receipts`,
      ),

    // --- messages: pins, saved, announcements, replies, search, files --------------
    pinMessage: (conversationId: string, messageId: string) =>
      api.post<{ pinned: boolean }>(`/conversations/${conversationId}/messages/${messageId}/pin`),
    unpinMessage: (conversationId: string, messageId: string) =>
      api.delete<{ pinned: boolean }>(`/conversations/${conversationId}/messages/${messageId}/pin`),
    pins: (conversationId: string) =>
      api.get<{ items: MessageBrief[]; profiles: Record<string, PersonBrief> }>(`/conversations/${conversationId}/pins`),
    saveMessage: (conversationId: string, messageId: string) =>
      api.post<{ saved: boolean }>(`/conversations/${conversationId}/messages/${messageId}/save`),
    unsaveMessage: (conversationId: string, messageId: string) =>
      api.delete<{ saved: boolean }>(`/conversations/${conversationId}/messages/${messageId}/save`),
    saved: () =>
      api.get<{
        items: Array<MessageBrief & { saved_at: string; conversation_title: string | null; conversation_kind: string }>
        profiles: Record<string, PersonBrief>
      }>('/conversations/saved'),
    markAnnouncement: (conversationId: string, messageId: string) =>
      api.post<{ announcement: boolean; notified?: number }>(`/conversations/${conversationId}/messages/${messageId}/announcement`),
    unmarkAnnouncement: (conversationId: string, messageId: string) =>
      api.delete<{ announcement: boolean }>(`/conversations/${conversationId}/messages/${messageId}/announcement`),
    replies: (conversationId: string, messageId: string) =>
      api.get<{ root: MessageBrief; items: MessageBrief[]; profiles: Record<string, PersonBrief> }>(
        `/conversations/${conversationId}/messages/${messageId}/replies`,
      ),
    search: (conversationId: string, q: string) =>
      api.get<{
        items: Array<{ id: string; sender_id: string; kind: string; snippet: string; media_kind: string | null; created_at: string }>
        profiles: Record<string, PersonBrief>
        partial: boolean
      }>(`/conversations/${conversationId}/search?q=${encodeURIComponent(q)}`),
    files: (conversationId: string, kind: 'media' | 'files' | 'links') =>
      api.get<{
        items: Array<Partial<MessageBrief> & { id: string; sender_id: string; created_at: string; url?: string }>
        profiles: Record<string, PersonBrief>
      }>(`/conversations/${conversationId}/files?kind=${kind}`),

    // --- polls, events, tasks -----------------------------------------------------
    createPoll: (
      conversationId: string,
      input: { question: string; options: string[]; multiple: boolean; results: GroupPoll['results']; closes_at?: string | null },
    ) => api.post<{ message_id: string; poll: GroupPoll }>(`/conversations/${conversationId}/polls`, input),
    poll: (conversationId: string, pollId: string) => api.get<GroupPoll>(`/conversations/${conversationId}/polls/${pollId}`),
    vote: (conversationId: string, pollId: string, optionIds: number[]) =>
      api.post<GroupPoll>(`/conversations/${conversationId}/polls/${pollId}/vote`, { option_ids: optionIds }),
    closePoll: (conversationId: string, pollId: string) =>
      api.post<GroupPoll>(`/conversations/${conversationId}/polls/${pollId}/close`),
    createEvent: (
      conversationId: string,
      input: {
        title: string
        description?: string
        starts_at: string
        ends_at?: string | null
        timezone: string
        location?: string
        online_url?: string
      },
    ) => api.post<{ message_id: string; event: GroupEvent }>(`/conversations/${conversationId}/events`, input),
    respondEvent: (conversationId: string, eventId: string, response: 'going' | 'maybe' | 'not_going') =>
      api.post<GroupEvent>(`/conversations/${conversationId}/events/${eventId}/response`, { response }),
    cancelEvent: (conversationId: string, eventId: string) =>
      api.post<GroupEvent>(`/conversations/${conversationId}/events/${eventId}/cancel`),
    events: (conversationId: string, upcoming = true) =>
      api.get<{ items: GroupEvent[] }>(`/conversations/${conversationId}/events?upcoming=${upcoming}`),
    createTask: (conversationId: string, input: { title: string; assignee_id?: string | null; due_at?: string | null }) =>
      api.post<{ message_id: string; task: GroupTask }>(`/conversations/${conversationId}/tasks`, input),
    updateTask: (
      conversationId: string,
      taskId: string,
      input: { done?: boolean; title?: string; assignee_id?: string | null; due_at?: string | null },
    ) => api.patch<GroupTask>(`/conversations/${conversationId}/tasks/${taskId}`, input),
    tasks: (conversationId: string, openOnly = false) =>
      api.get<{ items: GroupTask[] }>(`/conversations/${conversationId}/tasks?open_only=${openOnly}`),

    /** Report a message, a member or the group to Kinjy's moderators (not to its admins). */
    report: (
      conversationId: string,
      input: { target_kind: 'message' | 'member' | 'group'; target_id: string; reason: ReportReason; note?: string },
    ) => api.post<{ received: boolean }>(`/conversations/${conversationId}/reports`, input),
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
        category?: string
        language?: string
        ebook?: boolean
      } = {},
    ) => {
      const query = new URLSearchParams()
      if (params.category) query.set('category', params.category)
      if (params.language) query.set('language', params.language)
      if (params.ebook !== undefined) query.set('ebook', String(params.ebook))
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
      status?: 'active' | 'draft'
      ebook?: EbookInput
      guarantee?: ProductGuarantee | null
      insurance?: ProductInsurance | null
      /** Required for a service. */
      service_terms?: string | null
    }) =>
      api.post<{ id: string; status: string; pricing: Record<string, string> }>('/commerce/products', input),

    /** Formats, size limits and categories for an ebook. Public. */
    ebookTerms: () => api.get<EbookTerms>('/commerce/ebooks/terms', { auth: false }),
    /** The buyer's ebooks. */
    library: () => api.get<{ items: LibraryItem[] }>('/commerce/library'),
    /**
     * A short-lived link to an owned ebook. Checked by the server every time.
     * Before the purchase is final the buyer must acknowledge that the first
     * successful download makes it final (428 without it).
     */
    downloadEbook: (orderId: string, acknowledge?: { policy_version: string }) =>
      api.post<EbookDownloadLink>(
        `/commerce/library/${orderId}/download`,
        acknowledge ? { acknowledge_final: true, policy_version: acknowledge.policy_version } : {},
      ),
    /** A new version of the book's file; buyers get it from their library. */
    replaceEbookFile: (productId: string, assetId: string, note?: string) =>
      api.post<{ version: number; unchanged?: boolean }>(`/commerce/products/${productId}/ebook/file`, {
        asset_id: assetId,
        note: note || undefined,
      }),
    ebookVersions: (productId: string) =>
      api.get<{
        items: Array<{ version: number; filename: string; format: string; size_bytes: number; note: string | null; created_at: string; current: boolean }>
      }>(`/commerce/products/${productId}/ebook/files`),
    productSales: (productId: string) => api.get<ProductSales>(`/commerce/products/${productId}/sales`),
    /** Place an order. The buyer acknowledges the marketplace policy (its version) before paying. */
    order: (productId: string, quantity = 1, shipping: OrderShipping | undefined, policyVersion: string) =>
      api.post<{ id: string; status: string; customer_price: string; policy_version: string }>('/commerce/orders', {
        product_id: productId,
        quantity,
        ...(shipping ? { shipping } : {}),
        acknowledge_policy: true,
        policy_version: policyVersion,
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

    /** The marketplace policy the buyer acknowledges. Readable signed out on purpose. */
    marketplacePolicy: () => api.get<MarketplacePolicy>('/commerce/marketplace/policy', { auth: false }),

    /** The seller says it is on its way / ready / the service performed. This never releases the money. */
    markDelivered: (
      orderId: string,
      input: string | { note?: string; carrier?: string; tracking_number?: string; tracking_url?: string } = '',
    ) =>
      api.post<Order>(
        `/commerce/orders/${orderId}/delivered`,
        typeof input === 'string' ? { note: input } : input,
      ),

    /**
     * The buyer confirms receipt: the purchase is final and settlement starts.
     * Only the buyer, after acknowledging what it means; a repeat returns the
     * same acceptance.
     */
    confirmDelivery: (orderId: string, policyVersion: string) =>
      api.post<Order>(`/commerce/orders/${orderId}/confirm-delivery`, {
        acknowledge_final: true,
        policy_version: policyVersion,
      }),

    /** Cancel before the purchase is final. Seller: a paid order; buyer: an unpaid one. */
    cancelOrder: (
      orderId: string,
      reason: 'cannot_supply' | 'refused_at_handover' | 'delivery_failed' | 'unpaid',
      note = '',
    ) => api.post<Order>(`/commerce/orders/${orderId}/cancel`, { reason, note }),

    /** Operational support on an order (not arbitration). */
    openSupport: (orderId: string, kind: SupportKind, message: string) =>
      api.post<SupportCase>(`/commerce/orders/${orderId}/support`, { kind, message }),
    mySupportCases: () => api.get<{ items: SupportCase[] }>('/commerce/support/me'),

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
    /** Paying from the balance: `{ enabled: false }` while the option is switched off. */
    walletQuote: (orderId: string) =>
      api.get<WalletPayQuote>(`/payments/wallet/quote?order_id=${encodeURIComponent(orderId)}`),
    /** The whole order, from the available balance. The server sets the amount. */
    payWithWallet: (orderId: string) => api.post<WalletPayment>('/payments/wallet/pay', { order_id: orderId }),
    settleMockPayment: (paymentId: string) =>
      api.post<{ payment_id: string; status: string; mock: boolean }>(`/payments/${paymentId}/mock-settle`, {}),

    /** Disputes opened before 27/10, read-only history. */
    myDisputes: () => api.get<{ items: Dispute[] }>('/commerce/disputes/me'),
    dispute: (id: string) => api.get<Dispute>(`/commerce/disputes/${id}`),

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
    /** Start paying for `units` units: units x the unit price, set by the server. */
    join: (units = 1) =>
      api.post<{ payment_id: string; rail: string; amount: string; checkout_url?: string; mock?: boolean }>(
        '/payments/referral-pool/join',
        { units },
      ),
  },

  /** Admin console: everything the admin dashboard reads, by service. */
  adminConsole: {
    users: (q = '', offset = 0) =>
      api.get<{
        total: number
        items: Array<AuthUser & { country: string | null; created_at: string; invited_by: string | null }>
      }>(
        `/admin/users?limit=50&offset=${offset}${q ? `&q=${encodeURIComponent(q)}` : ''}`,
      ),
    journals: (kind = '', offset = 0) =>
      api.get<{ total: number; items: AdminJournal[] }>(
        `/admin/journals?limit=50&offset=${offset}${kind ? `&kind=${encodeURIComponent(kind)}` : ''}`,
      ),
    trialBalance: () =>
      api.get<{ accounts: Array<{ account: string; debit: string; credit: string; balance: string }> }>('/admin/trial-balance'),
    reconcile: () =>
      api.get<{ checked: number; drift_count: number; drift: Array<Record<string, unknown>> }>('/admin/reconcile'),
    reverseJournal: (journalId: string, reason: string) =>
      api.post<AdminJournal>(`/admin/journals/${journalId}/reverse?reason=${encodeURIComponent(reason)}`, {}),
    kyc: (status = '') =>
      api.get<{
        year: number
        counts: Record<string, number>
        items: Array<{
          user_id: string
          year: number
          fee_paid: boolean
          fee_payment_id: string | null
          attempts: number
          max_attempts: number
          status: string
          provider_reference: string | null
          fee_payment: AdminPaymentBrief | null
        }>
      }>(`/admin/kyc${status ? `?status=${status}` : ''}`),
    /** Names for account ids shown on admin screens. */
    lookupUsers: (ids: string[]) =>
      api.post<{ users: Record<string, AdminMemberBrief> }>('/admin/users/lookup', { ids }),
    /** Kinjy payment ids with the provider's own id (Kaluta Pay, Sonco Pay). */
    lookupPayments: (ids: string[]) =>
      api.post<{ payments: Record<string, AdminPaymentBrief> }>('/admin/payments/lookup', { ids }),
    fraud: (days = 30) => api.get<FraudSignals>(`/admin/fraud/signals?days=${days}`),
    /** Reports from Messages: group messages, members and groups. */
    messageReports: (status: 'open' | 'closed' = 'open') =>
      api.get<{ items: MessageReportItem[] }>(`/admin/message-reports?status=${status}`),
    closeMessageReport: (reportId: number, resolution: string, removeMessage: boolean) =>
      api.post<{ closed: boolean; removed: boolean }>(`/admin/message-reports/${reportId}/close`, {
        resolution,
        remove_message: removeMessage,
      }),
    contactRisk: () =>
      api.get<{
        window_days: number
        items: Array<{ sender_id: string; distinct_minors_refused: number; total_attempts: number; risk_score: number; note: string }>
      }>('/admin/contact-risk'),
    refunds: () =>
      api.get<{
        items: Array<{
          id: string
          order_id: string
          user_id: string
          amount: string
          status: string
          rail: string
          failure_reason: string | null
          created_at: string
        }>
      }>('/admin/refunds?limit=100'),
    /** Disputes from before 27/10: history only, nothing can be resolved any more. */
    disputes: () => api.get<{ items: Array<Record<string, unknown>> } | Array<Record<string, unknown>>>('/admin/disputes'),
    /** Marketplace orders; `attention`: a settlement or refund pending or waiting for a person. */
    marketOrders: (attention = false) =>
      api.get<{ items: Order[] }>(`/admin/commerce/orders${attention ? '?attention=true' : ''}`),
    retryMarketOrder: (orderId: string) => api.post<Order>(`/admin/commerce/orders/${orderId}/retry`, {}),
    cancelMarketOrder: (orderId: string, note: string) =>
      api.post<Order>(`/admin/commerce/orders/${orderId}/cancel`, { note }),
    supportCases: (status: 'open' | 'closed' | '' = 'open') =>
      api.get<{ items: SupportCase[] }>(`/admin/commerce/support${status ? `?status=${status}` : '?status='}`),
    closeSupportCase: (caseId: string, note: string) =>
      api.post<SupportCase>(`/admin/commerce/support/${caseId}/close`, { note }),
    /** Every allocation of marketplace order money, with what the provider answered. */
    orderFunds: (status?: string) =>
      api.get<{
        items: Array<{
          reference: string
          order_id: string
          purpose: 'settlement' | 'refund'
          rail: string | null
          amount: string
          status: string
          backing: string | null
          provider_refs: string | null
          attempts: number
          error: string | null
          created_at: string
          confirmed_at: string | null
        }>
      }>(`/admin/payments/order-funds${status ? `?status=${status}` : ''}`),
    leadersSnapshot: (period: string) =>
      api.post<Record<string, unknown>>(`/admin/leaders-pool/snapshot?period=${period}`, {}),
    leadersReviewed: (period: string) =>
      api.post<Record<string, unknown>>(`/admin/leaders-pool/${period}/reviewed`, {}),
    leadersPay: (period: string) => api.post<Record<string, unknown>>(`/admin/leaders-pool/pay?period=${period}`, {}),
    services: () =>
      api.get<{ healthy: number; total: number; services: Record<string, { status: string; error?: string }> }>('/status'),
    aiObservability: () => api.get<Record<string, unknown>>('/admin/ai/observability'),
    reconcileSplits: () => api.post<Record<string, unknown>>('/admin/payments/splits/reconcile', {}),
    reconcileWithdrawals: () => api.post<Record<string, unknown>>('/admin/payouts/withdrawals/reconcile', {}),
  },

  /** Admin: the referral pool's purchases, units, rounds and assignments. */
  poolAdmin: {
    overview: () =>
      api.get<ReferralPoolState & { members: number; purchases_under_review: number; unassigned_open: number }>(
        '/admin/referral-pool/overview',
      ),
    members: (offset = 0) =>
      api.get<{ items: PoolAdminMember[]; total: number }>(`/admin/referral-pool/members?offset=${offset}&limit=100`),
    purchases: (status?: string) =>
      api.get<{ items: PoolPurchase[] }>(`/admin/referral-pool/purchases${status ? `?status=${status}` : ''}`),
    /** Units only: for purchases with no Kinjy payment behind them (migrated by hand). */
    reverse: (purchaseId: string, units: number, kind: 'refund' | 'reversal', reason: string) =>
      api.post<PoolPurchase>(`/admin/referral-pool/purchases/${purchaseId}/reverse`, { units, kind, reason }),
    /**
     * The whole refund of units bought with a payment: units off, revenue and
     * commissions mirrored pro rata, the amount owed on the buyer's balance.
     * `requestId` makes a retry finish the same refund instead of a second one.
     */
    refundPayment: (paymentId: string, units: number, kind: 'refund' | 'reversal', reason: string, requestId: string) =>
      api.post<{ refund_id: string; status: string; amount: string }>(`/admin/pool-refunds/${paymentId}`, {
        units,
        kind,
        reason,
        request_id: requestId,
      }),
    review: (purchaseId: string, decision: 'approve' | 'reject', note: string) =>
      api.post<PoolPurchase>(`/admin/referral-pool/purchases/${purchaseId}/review`, { decision, note }),
    rounds: () => api.get<{ items: PoolRound[] }>('/admin/referral-pool/rounds'),
    quotas: (roundId: string) =>
      api.get<{
        items: Array<{
          member_id: string
          handle: string | null
          units_snapshot: number
          quota_total: number
          assigned: number
          remaining: number
          excluded_at: string | null
          excluded_reason: string | null
        }>
      }>(`/admin/referral-pool/rounds/${roundId}/quotas`),
    assignments: (roundNumber?: number) =>
      api.get<{
        items: Array<{
          new_member_id: string
          new_member_handle: string | null
          sponsor_id: string
          sponsor_handle: string | null
          round_number: number | null
          quota_total: number | null
          quota_remaining_before: number | null
          eligible_quota_in_draw: number
          assigned_at: string
        }>
      }>(`/admin/referral-pool/assignments${roundNumber ? `?round_number=${roundNumber}` : ''}`),
    ledger: (kind?: string) =>
      api.get<{
        items: Array<{
          id: number
          member_id: string
          purchase_id: string
          delta: number
          kind: string
          reason: string | null
          actor: string | null
          reference: string | null
          created_at: string
        }>
      }>(`/admin/referral-pool/ledger${kind ? `?kind=${kind}` : ''}`),
    unassigned: () =>
      api.get<{
        items: Array<{ new_member_id: string; reason: string; attempts: number; created_at: string; resolved_at: string | null }>
      }>('/admin/referral-pool/unassigned'),
    retry: (memberId: string) =>
      api.post<{ assigned: boolean; sponsor_id: string | null }>(`/admin/referral-pool/unassigned/${memberId}/retry`, {}),
    setSeatStatus: (seatId: string, status: 'active' | 'suspended' | 'refunded') =>
      api.post<{ seat_id: string; status: string }>(`/admin/referral-pool/seats/${seatId}/status?status=${status}`, {}),
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
      /** The token of a short link the visitor arrived on. */
      referral_link?: string
    }) {
      const result = await api.post<AuthResult>('/auth/register', input, { auth: false, credentials: 'include' })
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
     * Hold an invitation for this browser. The server checks the inviter and
     * keeps it in an HttpOnly cookie for the attribution window; a signed-in
     * member is never captured (their sponsor is settled).
     */
    captureInvitation: (input: { code?: string; link?: string }) =>
      api.post<{
        captured: boolean
        reason?: string
        inviter?: { display_name: string; handle: string }
        expires_at?: string
      }>('/auth/invitation/capture', input, { credentials: 'include' }),
    /** The invitation this browser holds, if any — what sign-up will apply. */
    heldInvitation: () =>
      api.get<{ inviter: { display_name: string; handle: string } | null; expires_at?: string }>(
        '/auth/invitation',
        // The invitation lives in an HttpOnly cookie; sent even where the API
        // is on another origin (development), same-origin in production.
        { auth: false, credentials: 'include' },
      ),

    /**
     * Shorten a Kinjy page (kinjy.com/s/<token>). With `includeInvitation`
     * the link carries the signed-in member's invitation - the server decides
     * whose, never the browser. The same page with the same choice returns
     * the same active link (`created: false`).
     */
    shareLink: (destination: string, includeInvitation = false) =>
      api.post<ShareLinkOut>('/auth/share-links', { destination, include_invitation: includeInvitation }),

    /** The signed-in member's own short links, newest first. */
    myShareLinks: (offset = 0, limit = 50) =>
      api.get<{ items: ShareLinkOut[]; total: number }>(`/auth/share-links?offset=${offset}&limit=${limit}`),

    /** Switch one of the member's links off. It stops opening, for good. */
    disableShareLink: (token: string) =>
      api.post<ShareLinkOut>(`/auth/share-links/${encodeURIComponent(token)}/disable`, {}),

    /** Where a short link goes, and - for an invitation link - the sharer's name. Never their code. */
    resolveShareLink: (token: string) =>
      api.get<{
        token: string
        destination: string
        kind: string
        invitation: boolean
        inviter: { display_name: string } | null
      }>(`/auth/share-links/${encodeURIComponent(token)}`, { auth: false }),

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
    walletHistory: (limit = 30, offset = 0) =>
      api.get<{ total: number; items: WalletHistoryItem[] }>(`/wallet/history?limit=${limit}&offset=${offset}`),
    commissions: (params: { source_kind?: string; limit?: number } = {}) => {
      const query = new URLSearchParams()
      if (params.source_kind) query.set('source_kind', params.source_kind)
      query.set('limit', String(params.limit ?? 20))
      return api.get<CommissionsPage>(`/commissions?${query}`)
    },
    payoutEligibility: () => api.get<PayoutEligibility>('/payments/eligibility'),
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
