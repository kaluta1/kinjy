# Communities — assessment and progress record

Started 2026-10-24. This file is the continuation record: what exists, what each
phase adds, what is done, and what is deliberately disabled. Update it at the end
of every phase.

## 1. Architecture (as found)

| Layer | Where | Notes |
|---|---|---|
| Communities, memberships, forums, threads, replies, knowledge | `backend/community-service` (FastAPI, schema `community`) | 33 endpoints; roles owner/moderator/member; statuses active/pending/banned |
| Community *posts* | `backend/social-service` (`posts.community_id`, `visibility='community'`) | feed, comments (threaded, depth), reactions, reports, moderation decisions & appeals (platform level), age rating |
| Age safety | `community-service/agecommunity.py`, `common/agesafety.py` | every thread/reply/community rated; minors filtered server-side |
| Short links & invitations | `auth-service` `share_links` (`/s/<token>`, optional invitation) | built 24/10; sponsor set only at registration |
| Payments / ledger / payouts | payment-service (Kaluta Pay), ledger-service (journals, wallets) | `economy.revenue_split` = the only commission rule (20 % direct, 5 % Leaders) |
| Creator subscriptions | `creator-service.Subscription` | platform tiers + creator subs; candidate base for paid memberships |
| Marketplace | commerce-service (products product/service/digital, orders, escrow, disputes) | |
| Family tree / memorials | family-service, memorial-service | separate verification rules (death reports) |
| AI | ai-service (summaries, translation, observability) | assistant **off** (`FEATURES.assistant`) |
| Background work | in-process asyncio loops per service (payment reconcilers, age transitions); RabbitMQ events (no durable queue) | |
| Frontend | `app/` React + Vite; community page `components/community/CommunityView.tsx`, dialogs `CommunityDialogs.tsx`, queue `MemberQueue.tsx` | |

Production on 24/10: 9 communities (6 public open, 1 public closed content, 2
private), 48 active memberships, 5 pending, 41 community posts, **0 paid
communities, 0 community forums**.

## 2. Feature matrix

`existing` works today · `incomplete` partly there · `missing` · `blocked` needs a decision or infrastructure.

| Area | Capability | State |
|---|---|---|
| Types | public / private / secret / paid kinds, open-content toggle | existing |
| Types | templates (interest, paid, learning, business, family, memorial, association) | missing → **Phase 1** |
| Spaces | announcements, discussions, articles, directory toggles | missing → **Phase 1** |
| Spaces | courses, events, marketplace, opportunities, chat, family, memorial | missing → later phases (shown as "not available yet") |
| Permissions | owner/moderator/member, join requests, bans, secret invite codes | existing |
| Publishing | posts in a community, comments threaded, mentions, topics, articles, images with alt text, age rating | existing |
| Publishing | pinned posts, bookmarks, scheduled publishing, edit history, accepted answer on a post | missing → **Phase 1** |
| Publishing | chronological / top views in a community | incomplete (chronological only) → **Phase 1** |
| Publishing | galleries & captions, video embeds, audio posts, PDF/ebook preview, series/reading progress, download permissions | incomplete (series_id exists) → Phase 3 |
| Forums | threads, replies, upvotes, accepted answers, AI summary, knowledge entries | existing (no community forums in prod) |
| Moderation | platform reports, decisions, appeals | existing |
| Moderation | community post approval queue, community audit log with reasons, welcome message, invite spam limits | missing → **Phase 1** |
| Moderation | community-level appeals | missing → Phase 3 (platform appeals cover removals today) |
| Invitations | short links with optional invitation, QR codes | existing (24/10) |
| Invitations | community inviter kept apart from account sponsor; link analytics (opens, registrations, joins) | missing → **Phase 1** |
| Paid memberships | tiers, periods, trials, coupons, renewals, grace, receipts | **blocked** on decisions (§4) → Phase 2 |
| Earnings | community revenue lines in the ledger | **blocked** on decisions (§4) → Phase 2 |
| Events, courses, opportunities | | missing → Phases 2–3 |
| Family / memorial communities | links to branches, albums, custodians | missing → Phase 3 |
| Translation | one-click translate exists for posts | incomplete → Phase 3 |
| AI assistant | | blocked: assistant switched off; boundary only |
| Analytics | community dashboards | missing → Phase 2 |

## 3. Phases (adjusted)

* **Phase 1 – foundation (this session).** Templates and spaces; server-enforced
  space permissions; pinned posts, bookmarks, scheduled posts, edit history,
  accepted answers, chronological/top views; community post approval queue,
  audit log with reasons, welcome message, invitation rate limits; community
  inviter attribution + link analytics; settings UI and space tabs.
  *Adjustment:* paid communities exist only as an unusable kind (0 in
  production, joining answers 402), so billing reliability does not need to
  move into Phase 1.
* **Phase 2 – commerce and operations.** Paid memberships on creator-service
  subscriptions + Kaluta Pay, entitlements separate from payment state;
  events (capacity, waitlists, tickets); marketplace listings per community;
  analytics; earnings lines.
* **Phase 3 – advanced.** Courses; family/memorial spaces through
  family-service and memorial-service; rich media (galleries, embeds, PDF);
  translation; automation digests; AI boundary.

## 4. Decisions needed before money moves (Phase 2)

1. Platform fee on community memberships, courses and tickets: Kinjy's agent
   model adds 20 % on top of the seller's price for products and private clubs.
   Does the same markup apply to community memberships, or a different fee?
2. Whether direct-referral commission (20 % of Kinjy's revenue) applies to
   membership revenue the same way it applies to marketplace revenue.
3. Renewals: Kaluta Pay has no stored-card recurring charge, so renewals are
   manual (reminder + pay link). Confirm grace period length (configurable).
4. Who receives a community's revenue: the owner's wallet only, or splits with
   co-owners/instructors.

Until answered, paid features stay behind a disabled flag.

## 5. Progress

(Per-phase delivery reports below.)

### Phase 1 — delivered locally 2026-10-24 (not committed, not deployed)

**1. Functionality**
- Templates: interest, membership, learning, business, private family (starts
  secret), memorial (posts approved first), association. Defaults only; changing
  a template later changes no access and no space.
- Spaces per community, owner-switched, server-enforced: announcements (stewards
  post), discussions, articles & resources, member directory. Courses, events,
  marketplace, opportunities, chat, family, memorial are listed as "not
  available yet" and cannot be switched on. A space switched off hides its posts
  (kept, not deleted) and refuses new ones; directory off hides the member list
  from non-stewards.
- Publishing: space chosen in the composer; pinned posts (max 5, stewards);
  bookmarks (private, lost with access) and a Saved page; scheduled posts
  (1 min – 90 days, published once by an atomic claim, notifications at
  publication); edit history kept on every edit; accepted answer on a community
  discussion (asker or steward); Latest / Most engaged views.
- Moderation: post approval (never / new members' first week / always),
  approval queue, approve / refuse / remove with reasons, community activity log
  (members, posts, settings, spaces, privacy), welcome message sent once per
  membership, removal with reason, invitation rate limit (20/hour).
- Invitations: joining through a member's invitation short link records the
  community inviter on the membership; the account sponsor is never touched.
  Link analytics kept apart: opens (people only, a counter), registrations,
  community joins. QR codes: the existing downloadable QR on share links.
- Fix: the community feed was always requested without the session, so the
  server could not answer "may take part", queued posts or saved state for the
  reader (and a private community's feed answered 404 to its own members).

**2. Files**
- `backend/community-service/spaces.py` (new): templates, spaces, rules.
- `backend/community-service/models.py`, `main.py`: template / approval /
  welcome / rules columns, `community_spaces`, `community_audit`, membership
  inviter; endpoints `/community-templates`, `PATCH /communities/{id}/spaces`,
  `PATCH /communities/{id}/settings`, `GET /communities/{id}/audit`, internal
  `post-policy` and `audit`; join `?via=`; member action `remove` + reasons.
- `backend/social-service/models.py`, `main.py`: post space / pin / schedule /
  accepted answer, `post_revisions`, `post_bookmarks`; endpoints pin, moderate,
  accept-answer, bookmark(s), revisions, pending queue; scheduler loop.
- `backend/auth-service/models.py`, `main.py`: `share_links.opens`,
  `share_link_conversions`, internal link lookup and conversions.
- `backend/gateway/main.py`: routes `/api/community-templates`, `/api/bookmarks`.
- `app/src/...`: `CommunityView`, `CommunityDialogs`, new
  `CommunitySpacesSettings`, `CommunityModeration`, `Composer`, `PostCard`,
  `Comments`, `Communities` (template picker), new `Saved` page, `ShortLinks`
  (link stats), `ProfileCard` (Saved link), `App`, `Layout`, `lib/api.ts`.
- `backend/tests/e2e_community_phase1.py` (new).

**3. Shared code touched and consumers**
- social-service `create_post`: community posts now go through
  `_community_policy` (same refusals as before plus spaces/approval); every
  other post unchanged except the optional `publish_at`. `_readable_post` now
  also hides pending/scheduled/withheld posts from everyone but their author —
  consumers: every route taking a post id (open, react, comment, share).
- `PostCard`, `Composer`, `Comments` are used by every feed: new controls are
  additive (Save for signed-in readers, Edited history when edited; pin/remove
  and answers only inside a community for its stewards).
- auth-service `/s/<token>` now increments a counter; registration records a
  conversion row. Attribution rules unchanged.

**4. Migrations / configuration** — all additive, run at service start:
community: 4 columns on communities, 3 on memberships (+index), tables
`community_spaces`, `community_audit`. social: 4 columns on posts, 2 indexes,
tables `post_revisions`, `post_bookmarks`. auth: `share_links.opens`, table
`share_link_conversions`. Optional env: `COMMUNITY_NEW_MEMBER_DAYS` (7),
`COMMUNITY_INVITES_PER_HOUR` (20), `COMMUNITY_MAX_PINNED` (5).
Existing communities have no space rows and read as interest groups with
their current access; nothing is made public.

**5. Tests**
- `e2e_community_phase1.py`: 62/62 (templates, spaces, approval, schedule,
  pins, bookmarks, history, answers, cross-community isolation, inviter vs
  sponsor, link analytics, welcome once, invitation limit).
- Existing suites: `e2e_community_post_notify` OK. `e2e_open_community` (3
  failures), `e2e_community_posts` (1), `e2e_community_settings` (1) fail
  **identically on the previous commit** (verified by stashing this work):
  they expect 404 where the code now answers 403 naming the community, and the
  image test needs media-service, which is not running locally. Pre-existing.
- `tsc` and `eslint` clean on touched files. Browser: created a community from
  a template, posted an announcement, pinned, saved, settings spaces, Saved page,
  375 px dark mode, no horizontal scroll, no console errors after reload.

**6. Gaps / disabled**
- Not built in Phase 1: galleries & captions, video embeds, audio posts, PDF /
  ebook previews, series & reading progress, download permissions, per-space
  notification preferences and digests, community-level appeals (platform
  appeals cover removals), multilingual rules.
- Mentions, tags, threaded replies were already there and unchanged.
- Steward notifications for pending posts go to up to 50 stewards.

**7. Rollback**
- Code: revert the Phase 1 commit. Schema: all additions are ignored by the
  previous code (new nullable/defaulted columns and new tables); nothing to
  drop. Posts left in `pending` / `scheduled` by a rollback stay hidden from
  feeds (the old code shows only `published`) — publish or remove them with
  `update social.posts set status='published' where status in ('pending','scheduled')`
  if wanted.

**8. Manual review checklist**
- Create a community from each template; a family one must be secret.
- As owner: switch articles off, try to post an article as a member (refused).
- Set approval to "Always", post as a member, approve from "Posts to approve",
  check the Activity log shows it with the reason.
- Schedule a post 2 minutes ahead; it appears by itself after its time.
- Share an invitation link to the community, join with another account; the
  community's member list is unchanged in sponsor, "My Short Links" shows
  opens / sign-ups / joins.

### Decisions taken 2026-10-25 (Phase 2)

- Kinjy adds a **markup on top of the owner's price** for memberships and
  tickets: 20 % by default, adjustable by an admin (`community_markup_pct`).
- The buyer's sponsor earns the **direct commission on Kinjy's markup**, with
  the Leaders share, exactly as on every Kinjy revenue (ledger `revenue_split`).
- **Grace period** after a missed (manual) renewal: 7 days by default,
  adjustable by an admin (`community_grace_days`).
- A community's revenue goes **to its owner only**; a change of administrator
  transfers no financial right.
- Paid features ship **switched off** (`community_paid_enabled`); an admin
  switches them on in Admin → Communities.

### Phase 2 — delivered locally 2026-10-25 (not committed, not deployed)

**1. Functionality**
- Access model per community: free, paid (joining needs a membership), mixed
  (free to join, chosen spaces for paying members). Members already in keep
  their place when a community becomes paid.
- Tiers (monthly, annual, one-time; optional trial and first-period price),
  codes (% off a first payment), server-made quotes (30 min), checkout through
  the existing payment rail at the quote's total only.
- Entitlements kept apart from payments: trial / active / grace / expired /
  cancelled / refunded. Renewals by hand continue the period; cancellation at
  period end; reminder 3 days before; grace then expiry and lapse; return by
  paying. One reminder per period, claimed atomically.
- Refunds by the owner (or an admin), full or partial, idempotent per request,
  bounded by what is left: the ledger mirrors the payment pro rata (owner's
  share, Kinjy's fee, sponsor commission, Leaders) and owes the amount on the
  buyer's Kinjy balance. A full refund ends the access or the ticket.
- Events: online / in person / hybrid, UTC + IANA zone (shown in the reader's
  local time with the event's own), capacity enforced under a row lock,
  waitlist with promotion, link for registered people only, recording consent,
  reminders a day before, cancellation (paid tickets refunded), paid tickets
  (a payment arriving after a sell-out is refunded in full automatically).
- Community marketplace: members show their own active marketplace listings;
  checkout, delivery, reviews and disputes stay the marketplace's.
- Opportunities: a post space (jobs, mentoring, collaborations, quotes).
- Analytics per community over 7/30/90 days with a definition per metric;
  money (sales, renewals, cancellations, refunds) for the owner only.
- Earnings for the owner: members paid, Kinjy fees, owner gross, refunds,
  owner net, payment list with refunds.
- Member dashboard: Memberships tab. Admin dashboard: Communities section.

**2. Files**
- community-service: `paid.py`, `events.py`, `insights.py` (new routers),
  `models.py` (settings, tiers, coupons, quotes, entitlements, payments,
  refunds, events, registrations, listings; `access_model`, `paid_only`),
  `main.py` (routers, paid join, background loop, paid spaces in policy and
  reader endpoint), `spaces.py` (events, marketplace, opportunities available).
- ledger-service: `posting.py` (refund: one owed line per payment, seller
  proceeds clawed back), `main.py` (`/internal/post/community-sale`),
  `models.py` (`revenue:community_markup`).
- payment-service: purpose `community`, `/payments/community/checkout`,
  settlement and reconciler, escrow lines.
- social-service: opportunities space, paid spaces in feed and direct link
  (`community_paid_only` answer), `/internal/community-stats`.
- commerce-service: `/internal/products/batch`.
- gateway: `/api/events`, `/api/me/community-memberships`,
  `/api/admin/community-settings`.
- app: `QuotePayment`, `CommunityMembership`, `CommunityEvents`,
  `CommunityMarket`, `CommunityOwnerTools`, `MyMemberships`, `CommunityView`,
  `AdminDashboard` + `adminDash/sections`, `Dashboard`, `lib/api.ts`.
- `backend/tests/e2e_community_phase2.py` (new).

**3. Shared code touched**
- ledger `refund_to_balance`: one "owed to payer" line for all cash lines of a
  journal, and vendor-payable lines clawed back from the seller's wallet.
  Consumers: pool refunds (one cash line: unchanged, re-tested) and community
  refunds.
- payment-service `checkout`: purpose `community` validated against its quote;
  other purposes unchanged. Reconciler now also retries community payments.
- social `_readable_post`: posts in a mixed community's paid spaces open for
  paying members only. Consumers: every route taking a post id.
- commerce: one additive internal endpoint.

**4. Migrations / configuration** (additive, at service start)
community: `communities.access_model`, `community_spaces.paid_only`, tables
`platform_settings`, `community_tiers`, `community_coupons`,
`community_quotes`, `community_entitlements`, `community_payments`,
`community_refunds`, `community_events`, `event_registrations`,
`community_listings`. Settings live in `platform_settings` (admin page).

**5. Tests**
- `e2e_community_phase2.py`: 68/68 (settings and permissions, tiers, codes,
  quote-only checkout, double settlement, late and foreign credits, ledger
  split and balance, renewal continuity, cancellation, grace and expiry,
  lapse and return, full / partial / repeated / excessive refunds with
  clawback, trial once and without grace, paid spaces in feed / link /
  posting, 6 concurrent registrations for 3 places, waitlist promotion,
  hidden link, paid tickets and the late-payment refund, event cancellation
  refunds, listings, analytics money for the owner only, one reminder per
  period, markup change on new quotes).
- Regression: `e2e_community_phase1` OK, `e2e_pool_rules` OK (refund
  function shared).
- Found and fixed by the tests: rows re-read under a lock were served from the
  session cache (capacity oversold 6/3); now `populate_existing`.
- `tsc` / `eslint` clean on touched files; browser: admin Communities section,
  owner's Paid access (tier $10 → members pay $12.00), events at 375 px dark.

**6. Gaps / disabled**
- Paid features are off until an admin switches them on.
- No automatic renewal (the rails cannot charge again): manual by design.
- Refunds go to the member's Kinjy balance, not back through the rail.
- Courses (Phase 3) not built; the Courses space stays unavailable.
- Event attendance is registrations only (no check-in); polls / Q&A during
  events and recordings' transcripts are not built.
- Analytics retention cohort not built (activation and activity are).
- The sponsor commission rate itself is the platform setting
  (`SPONSOR_COMMISSION_PCT`), not adjustable from the admin page yet.

**7. Rollback**
- Code: revert the Phase 2 commit; the new tables and columns are ignored by
  the previous code. Before reverting with paid communities live, switch paid
  off in Admin → Communities so no new payment is taken.

**8. Manual review checklist**
- Admin → Communities: switch paid on, check markup 20 % and grace 7 days.
- As an owner: Paid access → "Paid membership", add a $10 monthly tier; the
  page says members pay $12.00.
- With another account: open the community, choose the tier, pay; check the
  receipt (community $10 + Kinjy $2) and the Memberships tab.
- Owner → Paid access → Earnings → refund $12: access ends, balance shows it.
- Events: switch the Events space on, create a 2-place event, register 3
  people: the third is on the waitlist and moves up when one leaves.
