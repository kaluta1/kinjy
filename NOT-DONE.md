# Kaluta — what is not built yet

A living list of everything promised by the blueprint (or by a UI affordance)
that does **not** work today. Kept so nothing quietly passes for finished.

Rules for this file:

- If a screen looks complete but is not wired, it belongs here.
- If something was verified working, it comes **off** this list.
- Anything that cannot be verified in the current environment is marked
  **unverified** rather than assumed good.

Last reviewed: 2026-10-18 (open communities that publish publicly)

---

## 1. Missing infrastructure — cannot be faked

| Item | State | What it needs |
|---|---|---|
| **Live video broadcasting** | Chat is real, camera preview is local-only. **Nothing is transmitted.** `/live` says so on screen. | An ingest server, a transcoder and a CDN — or WebRTC peer-to-peer plus a TURN server (viewer-count limited). |
| **End-to-end encrypted messages** | Messages and chat attachments are **encrypted at rest** (`MESSAGES_ENCRYPTION_KEY`), so a stolen database or backup reveals nothing — but the server holds the key and can read them. No key exchange exists, so nothing is end-to-end; the thread header says “Encrypted on our servers”, not “end-to-end”. | Per-device keys, a key agreement (Signal/MLS via a vetted library), multi-device and key backup. |
| **Voice and video calls** | *Signalling* is built: offer/answer/ICE relay over the existing socket, payload never inspected. **No TURN server**, so peers behind symmetric NATs will fail to connect, and **no group calls** (an offer to 3+ is refused rather than left to fail mysteriously). | A TURN relay, and an SFU for groups. |
| **Real payment rails** | **Sonco Pay** takes every payment and pays withdrawals once `SONCOPAY_API_KEY` is set (18/10, see the section at the end). Without it, the old rails stay in mock mode. NowPayments and Mangopay themselves are still not wired. | Sonco Pay: the server IP declared in its dashboard, and a funded Sonco Pay balance for withdrawals. |
| **WebAuthn off localhost** | Passkeys work on `http://localhost`. From another machine over plain HTTP the ceremony **will fail**. | HTTPS. |

## 2. Rules stored but not enforced

These are the dangerous ones: the setting exists, the API returns it, and a
member could reasonably believe it protects them.

| Item | State |
|---|---|
| `reduced_motion` | Saved, but no component reads it — animations run regardless. |
| `translation_auto` | Saved; translation is still one click per post, never automatic. |

`data_saver`, `age_mode`, `wellbeing_enabled` / `daily_limit_minutes`,
`autoplay_media` and `interest_topics` **are** now enforced, and verified on
2026-08-17 against the running stack:

- `age_mode` filters **in the feed query** — 40 posts as an adult, 39 as a
  child, and a direct link to an adult post answers 404 for a minor.
- `data_saver` withholds heavy URLs server-side (`url: null, deferred: true`)
  and trims to one attachment; `GET /posts/{id}/media` is the "load it anyway"
  door, which does *not* relax the age rule.
- Wellbeing time is counted server-side and clamped to the wall clock: three
  beats claiming 5 minutes each credited 5.0 → 5.0 → 5.1, and a first beat
  claiming the schema maximum credited 1.0.
- `interest_topics` adds a named `interest` factor (+0.80) that appears in
  "Why am I seeing this?" — verified absent, then present, on the same post.

`who_can_message`, `who_can_add_family` and `who_can_add_community` **are**
enforced: messaging-service, family-service and community-service all call
`common/permissions.py`, which asks user-service and fails closed. Verified
2026-08-17 — each returns 403 with the reason, and allows the action once the
target opens the setting or the connection is accepted.

## 3. Built as a mock, still a mock

- **Marketing pages**: `/platform`, `/feeds`, `/family`, `/memorials`,
  `/commerce`, `/payments`, `/safety`, `/developers`, `/app` — designed content,
  no live data. Intentional; they are the public showcase.
- **Admin console** (`/admin`) — the designed mock. The real admin endpoints
  exist (ledger trial balance, reconcile, leaders-pool snapshot, KYC, moderation)
  but nothing calls them.

## 3b. Real-time — works, with one deployment limit

Live updates run over **one** WebSocket per tab, multiplexed by topic
(`user:<id>`, `post:<id>`, `feed`, `shorts`). Verified 2026-08-17: a comment and
a like from a second account moved the counters on an open page with no reload.

- **Single replica only.** The hub keeps its subscriptions in process, so a
  second messaging-service replica would serve half the sockets and each would
  only see its own half. Redis pub/sub between replicas is the fix; it is not
  written. The code says so where the registry is declared.
- **Not yet live:** follows, connection invitations, family and community
  changes, wallet and commission movements. The hub carries them the moment
  those services call `/internal/broadcast` — nothing in the transport is
  missing, the calls are.

## 3c. Deployment

Everything is prepared and exercised locally for **kinjy.com**, but
**nothing is deployed**: the VPS refuses every key on this machine, and the root
password cannot be used from here. One command from the operator
(`bash bootstrap.sh`) installs the key and deploys.

DNS is already correct — `kinjy.com` and `www` both resolve to
<your-server>, verified 2026-08-19, so certificate issuance will work.

- `docker-compose.prod.yml` — no `--reload`, no source mount, and only the web
  container published. Verified with `docker compose config`: 1 published port
  instead of 17, 0 bind mounts, 0 reloaders.
- `app/Dockerfile` + `app/nginx.conf` — the SPA built once and served by nginx,
  which also proxies `/api`, `/media` and the WebSocket, so the browser sees one
  origin. Verified locally against the running gateway: index 200, deep route
  200, `/api/status` 13/13, and the socket handshake returning **101** with the
  `ready` frame.
- `deploy.sh` — sync, build, restart, then ask the stack for its own health.
- `.env.production.example` — every secret that must not carry over from dev.

- `Caddyfile` + a `caddy` service — automatic Let's Encrypt, automatic renewal,
  HSTS, and `www` redirected to the bare domain (a passkey registered on `www`
  would be rejected on the apex, so the redirect is correctness, not polish).
  Only 80 and 443 are published; the app itself has no host port at all.
- `bootstrap.sh` — authorises the key, installs Docker if absent, ships the
  secrets as mode 600, opens the firewall, warns if something already holds
  80/443, then deploys.
- `.env.production` — generated for the domain with fresh Postgres, RabbitMQ and
  JWT secrets. Gitignored.

**Unverified until it runs on the server**: the certificate has never been
issued, Caddy has never started, and no image has been built on that machine.
The deploy script checks all of it from outside afterwards.

Still to do once it is live: a backup for the Postgres volume, and rotating the
root password that was shared in chat.

## 3d. Shipped since — verified on production

Each of these was checked against https://kinjy.com, not only locally.

- **Notifications, end to end.** The store, its endpoints and the model had
  existed from the start with *nothing calling them* — the bell was a link to
  /messages. Services now call `common/notify.py` on comments, replies, follows,
  invitations, acceptances, forum replies and community decisions. Stored first,
  pushed second: an offline member finds it waiting, an online one sees it
  arrive. Verified with two accounts — 2 unread, correct links.
- **Community governance.** Private communities collected `pending` rows nobody
  could answer; joining one was a request into a void. Queue, admit, decline,
  ban, promote are now real, and a *secret* community answers 404 rather than
  403 to a non-member — confirming an id exists would leak what the tier hides.
- **One-to-Many.** `/creators/publish` returned 202 and created empty rows on the
  promise that "the AI layer fills them in"; nothing did. Translations,
  newsletters and articles are produced for real; short video, long video, audio
  and carousel come back `unsupported` **with the reason**, because no media
  pipeline exists. Verified: 8 outputs, 6 produced.
- **Disappearing messages.** The timer belongs to the room, not the sender, and
  everyone is told when it changes. Expiry deletes on the read and write paths
  rather than from a sweeper, so there is no window where a "disappeared"
  message is still served. Verified: 0 rows left in the database.
- **Thread summaries.** Refuses threads under 8 replies — a digest of six is a
  worse version of scrolling — and names its provider, including when it is the
  mock.
- **Graveyard browsing.** `GET /memorials` was a 405: a memorial could be
  created and reached by QR and never found again. Search and listing added,
  public on purpose.
- **Media, twice broken.** Uploads pointed at `http://localhost:8200` in
  production because `MEDIA_PUBLIC_BASE` was missing from the server `.env`; and
  the production overlay's blanket `volumes: !override []` had stripped
  media-service's **data volume**, so every deploy destroyed the uploads. Both
  fixed; persistence verified across a restart and a full container recreation.

## 3e. What /family promises, measured against what it does

*Checked 20/08. Partly superseded on 06/10: see "Family tree (06/10)" at the end,
which records what was found still open and what changed.*

Checked on production 2026-08-20, promise by promise.

- **"Family-only by default. Trees are private."** — was **false, and it was a
  privacy defect, not a missing feature**: `/family/search` and `/family/tree`
  took no token, so anyone on the internet could search a name and read a whole
  family — real given names, real relationships, the dead included. Now: no
  token → 401; a stranger's token → **404, not 403**, because confirming a
  person id exists leaks the family they were looking for; a stranger's search
  returns nothing. Verified, and verified that the legitimate owner still reads
  their own tree, search and paths.
- **Sharing per branch** ("your maternal line never sees the paternal side") —
  **not built**. The rule enforced is coarser: you may read a tree you belong
  to. Splitting visibility by branch is a finer rule that does not exist yet.
- **"Legacy contacts"** — **not built**. No model, no endpoint. Nobody can be
  named as steward of a tree.
- **"Export anytime · GEDCOM-friendly data portability"** — **not built**. No
  export endpoint of any kind. The claim "your family data is yours — take it
  wherever you go" is currently unbacked.
- **Heritage AI** (archive, timeline, documentary, biographies) — endpoints
  exist (`/family/heritage`, `/family/timeline`), no screen calls them.
- Working as described: the person/edge graph, derived relationships, the Level
  model, re-rooting, the path finder with per-hop relation labels, and
  corroboration with the three-close-relatives rule for the deceased.

## 4. Not started

- **Creator Studio / One-to-Many publishing** (one idea → article, video, audio,
  newsletter). Backend service exists; no UI, no transformation pipeline.
- **Advertising platform UI** — commerce-service has campaigns and the rate card;
  no screen to buy or manage one.
- **Forum → knowledge base** distillation with citations.
- **Kinjy Leaders payout to an actual rail** — the monthly snapshot, the
  commission ranking, the fraud-review gate and `/admin/leaders-pool/pay` all
  work and credit member wallets. Getting that wallet balance out of the
  building is now a member withdrawal through Sonco Pay (18/10); the old
  payout batch refuses to run while Sonco Pay is on.
- **Referral-pool refund when the pool fills mid-payment** — if the last seat is
  sold between checkout and settlement, the payment is flagged `refund_due` and
  logged loudly, but nothing refunds it automatically yet.
- **Dispute evidence uploads** — the API accepts a list of media URLs per
  message and stores them; the dispute screen has no file picker, so evidence
  can only be described in words today.
- **Admin arbitration screen** — `/admin/disputes` and
  `/admin/disputes/{id}/resolve` exist and work; there is no console page that
  calls them, so arbitration is an API call right now.
- **Family Heritage AI** — `/family/heritage` and `/family/timeline` exist and
  answer; no screen calls them. The marketing page promises an archive, an
  interactive timeline, a narrated documentary and biographies. None of that
  has a UI.
- **3-relative corroboration for deceased persons** — built with the family
  tree, which is open in the app since 06/10 (see "Family tree" below). The memorial death verification itself (UNCONFIRMED →
  REPORTED → UNDER REVIEW → VERIFIED) now has its UI; see Graveyard below.
- **Smoke test leaves data behind** — `smoke-test.sh` registers two members and
  posts several items on every run, and never cleans up. Fine locally, wrong
  against anything shared.
- **In-browser recording and trimming for shorts.** Publishing works — pick a
  clip, see the real 9:16 frame, caption it, upload with progress — and on a
  phone the picker offers the camera. But there is no MediaRecorder capture, no
  trim handles, no cover-frame picker and no sound library, which is what
  TikTok's *creation* side actually is. What ships is upload-and-publish.
- **Shorts discovery is chronological.** The reel is newest-first with paging;
  it does not run through the ranker, so there is no For You / Following split
  on this surface yet.
- **Quote reposts in the composer** — the API takes a body (`POST
  /posts/{id}/repost`), and a quote renders correctly if one exists, but the
  card's repost button only sends a plain share. There is no "add your thoughts"
  box yet.
- **Signed-out view counts** — views are recorded per member, so a visitor
  reading a public post is not counted. Deliberate: there is nothing to
  deduplicate against, and a per-session identity would inflate the figure.

## 5. Known gaps in things that *do* work

- **Assistant knowledge coverage**: keyword lists are mostly English, so some
  reasonable French/Swahili questions find nothing even though the localized
  answer exists (e.g. *"Comment mon paiement est-il calculé ?"* — the French
  answer says "revenus", never "paiement"). The answer-text fallback catches
  most, not all.
- **Language detection** separates Arabic and Chinese by script and
  French/Swahili/English by function words. Short or ambiguous input falls back
  to the caller's hint.
- **Translation** runs through the mock provider; output is `[fr] original`.
  Labelled as such in the UI.
- **Family tree**: relationship labels stop at "cousin (degree N), M removed" and
  otherwise say "related through N steps" rather than inventing a term.
- **Open communities**: somebody reading one they have not joined is stopped
  before reacting — the button says "Join this community to take part" instead
  of acting. The *comment box inside the post dialog* is still offered and
  refused by the server after they type, with the same sentence. Correct, and
  one step later than it should be. Reading, the share preview and every
  server-side refusal are verified in `backend/tests/e2e_open_community.py`.

## 6. Unverified in this environment

The browser pane does not composite frames here, so these were built but never
seen running:

- **Composer collapse on scroll** and its floating button — the logic is there;
  a hidden tab does not scroll, so it could not be exercised.
- **Hidden scrollbars** — the CSS is in place and `scrollbar-width` computes to
  `none`, but whether every scroll area still *feels* right is a visual call.
- **Dialog close animation** — Radix waits for `animationend`, which never fires
  in a hidden tab. Expected to be fine on a visible tab; worth one look.
- **The footer flicker, on screen.** The constellation canvas now draws nothing
  while off screen (measured: 0 frames), runs at 30fps instead of 60, and no
  longer allocates a gradient per arc per frame. That the result *looks* smooth
  is unverified — a background tab pauses rAF, so the on-screen behaviour could
  not be measured here.
- **Heading leading, on screen.** Display headings sat below Fraunces' ink
  height (1.02–1.10 against a measured 1.12–1.13em), so any heading that
  wrapped had line 1's descenders overlapping line 2's ascenders. Raised above
  the floor and re-measured; that the new rhythm *looks* right is a visual call
  nobody has made yet.
- **Light mode, beyond contrast.** Every text/background pair on the feed now
  clears WCAG AA (66 checked, worst 5.3:1) and the dark theme still does (15
  checked). Whether the paper palette *looks* like Kaluta — the gold on cream in
  particular — is a judgement nobody has made by eye.
- **The floating stack, seen rather than measured.** The orb and the feed's
  composer button now take numbered slots (`lib/floating.ts`) and were verified
  not to overlap at 1280×720 and 375×812 — but the composer button only mounts
  past 220px of scroll, which a hidden tab will not do, so the check was made
  with probe elements carrying the same classes rather than the real button.
- **Every screenshot-level judgement** — spacing, contrast, whether a design
  actually reads well. All visual claims in this project rest on DOM measurements,
  not on seeing the page.

## Graveyard (02/10)

Visibility, tribute moderation, editing, the public QR page, the life timeline,
the grave location, administrators and succession, anniversary reminders and
death verification are built, and covered by `backend/tests/e2e_graveyard.py`
(88 checks) plus a browser pass of `/memorial/:code` and `/graveyard` (visitor,
family and member; WCAG AA in dark and light; 390px). A death on 29 February
used to make the memorial impossible to create; it no longer does.

A full audit followed (`backend/tests/e2e_graveyard_audit.py`, 39 checks). It
found and fixed: any administrator could remove the first and then delete the
memorial; naming an administrator bypassed the contact and age rules; an
administrator's own message waited for their own approval; the reminder series
ended after an outage; a "captured at the grave" claim was taken on trust; a
published tribute could not be taken down from the screen; candles buried the
guest book; and the public `/memorials` page showed a dead button, a candle that
was never sent anywhere, a made-up legacy contact and a paid tier that does not
exist.

**Gallery of photos and videos (06/10)** — built (`info.md` §8, "photos/videos/voice"),
covered by `backend/tests/e2e_graveyard_gallery.py` (66 checks) and a browser
pass of the grid, the viewer, the family's screen, light mode and 390px (55
checks, WCAG AA). The family adds, captions, orders and removes up to 60 photos
and videos (500 MB in all); each carries an origin label; some can be marked
sensitive; an approved visitor photo can be promoted into it.

Not built — in the blueprint (`info.md` §8):

- **Paid flowers and candles** ("free + paid"). `paid` and `amount` exist on
  tributes; nothing charges, and the public pages no longer show a paid tier.
  The age rule is already settled — every checkout passes the `payments` gate
  (18+, AGE-SAFETY.md §24–25). The blueprint gives no price and no split.
- **Digital legacy contacts** — named in the blueprint, nothing more: who they
  are, what they may do, and what triggers it are not written down.

Deferred by the blueprint itself:

- **AR memorials** — "future AR memorials".
- **"Family" visibility** is accepted and treated as private (administrators
  only): the family is whoever the family tree says. The tree is open in the app
  (`FEATURES.familyTreeApp`, 06/10); the public `/family` page stays hidden
  (`FEATURES.familyTree`).

Known limits of the gallery:

- **No thumbnails.** There is no ffmpeg in media-service, so the grid loads the
  pictures themselves (25 MB at most each, twelve before "Show all"), and a
  video's first frame stands in for a poster.
- **A video must be playable where it is watched.** MP4 and WebM only; an iPhone
  `.mov` is refused with what to do. An MP4 encoded as HEVC passes the checks but
  Chrome and Firefox cannot play it — the viewer offers the download. Nothing is
  converted.
- **50 MB per video** because the gateway holds a whole upload in memory
  (`await request.body()`). Raising it means streaming the body through the
  gateway first.
- **Media is not age-rated.** The classifier cannot look at a picture, and posts
  with media stay adult-only until a person reviews them. A memorial gallery is
  shown to everyone — signed-out visitors, who are the people at the grave, and
  minors included — and the family's "sensitive" mark (blurred until shown) is
  the only guard. A decision, not an oversight; AGE-SAFETY.md says so.
- **Only the gallery's own files are deleted** with their item or their
  memorial. The portrait, cover, voice and tribute photos still stay in
  media-service, because the same bytes can be shared with a post.
- **A video's link lasts an hour, a picture's five minutes.** A picture whose
  link ran out asks for fresh ones and a second failure within ten seconds is
  believed. The page does not renew links while it is untouched.
- **Error red on the dark theme is 4.3:1.** The theme's `--danger` is `#DE5C5C`;
  small text needs 4.5:1. The gallery shows problems in normal text with a red
  icon; the other 17 uses in Circles and Graveyard (51 in the app) are a token
  change away from passing.

Known limits:

- **"Captured at the grave" is the device's word.** The location is marked
  confirmed when the device reports a position within 50 m; nothing proves the
  phone was at the grave, and a client that lies about its accuracy is
  believed.
- **No rate limit on visitors** — nor anywhere on the platform. A visitor
  without an account can light any number of candles; an administrator can take
  them down one by one, but not in bulk. Their messages always wait for the
  family, so nothing they write appears unreviewed.
- **A tribute's author cannot withdraw it.** Only an administrator can take it
  down.
- **Deleting a memorial leaves its portrait, cover, voice and tribute photos** in
  media-service, restricted and owned by whoever uploaded them (the gallery's
  files are deleted).
- **The reporter of a death is not told the outcome** unless they administer
  the memorial; the family is.
- **Reminders fall at midnight UTC** on the anniversary, not at the family's
  local midnight.
- **Staff are not notified** of a new death report; they see the queue on
  `/graveyard` when they open it.
- Verified locally only, not on kinjy.com.

## Circles (02/10)

Membership, smart circles and the audience rule are built and covered by
`backend/tests/e2e_circles.py` (80 checks) plus browser passes of `/circles`
(functional flow, live updates, WCAG contrast in dark, light and cloud, 390px).

**Audit of 02/10, fixed:** reply notifications reached members removed from the
circle; any holder of a post id could subscribe to its live topic; blank names,
non-letter countries and blank cities were accepted; two simultaneous adds
answered 500; circles and members had no upper bound; warnings and errors on
`/circles` were unreadable in light mode and several texts sat under 4.5:1.

What the tests do not cover:

- **Verified locally only**, not on kinjy.com.
- **Smart circles trust the profile.** "Followers in Kigoma" means followers
  whose own profile says Kigoma; nothing verifies where anyone lives.
- **Members are not notified** of a circle post; it simply appears in their feed
  and in the Circles mode. Deliberate for now — a notification per post would be
  noisy — but not decided.
- **Community-only posts are readable by their author alone**, by id as well as
  in the feed. That was already the feed's behaviour; nothing in the app reads a
  community's posts yet, so nothing visible changed.
- **Fixed on the way, worth knowing:** every signed-in member could read every
  followers-only post on the platform, and the Following feed was always empty
  (social-service called `/users/me/following` with an empty token). Both now go
  through the same audience lookup as circles.
- **A live subscription is checked when it is made**, not again while it is
  open: a member removed while their page is open keeps receiving counter
  updates for that post until the page reloads or the socket reconnects. The
  updates carry counts and ids, never text.
- **Shared app chrome under 4.5:1**, outside `/circles` itself: the `text-low`
  token is 3.7:1 in dark and 2.6:1 in cloud (profile card labels, handles), and
  the gold-gradient primary style is 3.5:1 in light (active nav chip,
  notification badge, buttons on other pages). `/circles` avoids both; fixing
  them is a design-token change for the whole app.
- **`e2e_messaging_age.py` fails** at "could not open messages: 403" — the
  parental tier lock from 25/09 refuses the teen account it registers. Unrelated
  to circles; the test predates the lock and needs a supervised teen.

## Parental supervision (25/09)

- **The teenager's side of the page has not been seen.** The parent's side was
  loaded as a signed-in parent at 1280px and at 375px, with a live supervision
  link and a pending request, and light mode measured clean (34 text/background
  pairs, none under 4.5:1). The teen's view — the accept/decline buttons on an
  invitation, and the wording of a request that was declined — was exercised
  only through the API.
- **`/earn` has the same layout bug this page shipped with.** It is not in
  `APP_ROUTES` in `Layout.tsx`, so the marketing navbar still renders above its
  app shell and the page scrolls with a dead band at the top. `/supervision` was
  added to that list; `/earn` was left alone because fixing it means checking a
  page that is not part of this change.
- **A parent's own supervised state is not shown on the teen's side.** Either
  party sees the link and the requests, but neither sees the other's display
  name — only ids come back from `/supervision`. Workable, unfriendly.
- **No email or push when an invitation arrives**, only the in-app bell. A
  parent who is invited and does not open Kinjy never learns of it.
- **Time limits are set but not enforced by this feature.** The number is
  written to `preferences.daily_limit_minutes`, which the existing wellbeing
  counter already reads; that the enforcement path honours a parent-set value
  exactly as it honours a self-set one has not been re-verified since.

## Moderation: reports, decisions, appeals (28/09)

- ~~The member side has no screen.~~ Built: `/moderation` shows what was
  restricted, in plain words rather than column values, and takes the appeal.
- **No refusal is appealable, and that is not a bug today.** Every
  `block_publication` in the classifier also sets `escalate_child_safety`, so
  every refusal is a child-safety escalation and correctly leaves the ordinary
  appeals path. It does mean the `refused_publication` appeal branch is
  currently unreachable. `e2e_moderation.py` asserts this explicitly, so the
  day the classifier blocks for some other reason, that check fails and says
  so rather than the branch quietly coming alive untested.
- **Overturning clears the graded levels wholesale.** A reviewer who thinks a
  post was rated 18+ wrongly cannot say "it is violence 1, not sexual 2" — the
  appeal zeroes every category. Fine for the common case, crude for a genuinely
  borderline one.
- **No appeal against an account-level action**, because there are no
  account-level actions yet: no suspensions, no strikes, no rate limits imposed
  by moderation. When those arrive they need their own decision records.
- **Nothing expires.** Decisions and reports accumulate forever. A retention
  policy matters here more than most tables, since `body_snapshot` holds text
  that was refused publication.
- **The SLA is decorative.** Appeals get a `due_at` and overdue ones sort
  first, but nothing escalates, nobody is paged, and no appeal is ever granted
  by default for going unanswered.
- **`social-service/main.py:140` has an invalid escape sequence** (`\w` in a
  non-raw string) that Python warns about on every import and will eventually
  make an error. Pre-existing, one line, untouched here because it is in the
  hashtag regex rather than in anything this change covers.

## Trust & Safety console (28/09)

- **Seen at 1280px and 375px in both themes, with live queues.** The console's
  own text passes WCAG AA in light and dark (570 elements, size-aware
  thresholds). Overturning an appeal from the page was exercised end to end and
  the counters moved (review queue 131 → 130, appeals 5 → 4).
- **Three app-chrome contrast failures are pre-existing and untouched**: the
  avatar initial (1.12:1), the mobile bottom-nav labels in light mode (2.25:1),
  and `AppShell`'s subtitle in dark (3.96:1). Confirmed by running the same
  audit on `/circles`, which fails on exactly the same elements. They belong to
  components every app page shares, so they are not this change's to fix.
- **My first two contrast audits were wrong and would have sent me fixing
  nothing.** The first compared against a non-composited ancestor background,
  so a 10%-alpha overlay read as opaque near-white and a dark-mode button came
  back at 1.0:1. The second applied 4.5:1 to every element, including 24px
  semibold numerals whose AA threshold is 3:1. Only the third measurement —
  alpha-composited, size-aware — is the one quoted above.
- **No pagination.** The queue shows the first 50 of however many; with 131
  pending there is no way to reach the rest from the page.
- **No filtering or sorting**, so a reviewer cannot say "media only" or "most
  reported first", which is how this queue will actually want to be worked.
- **Rating is one-click and total.** The buttons set `age_rating` only; the
  graded category levels are shown but cannot be edited, so a reviewer cannot
  record "violence 1, not sexual 2" — the same bluntness the appeal overturn
  has.
- **Nothing refreshes on its own** and there is no optimistic update: every
  action refetches all three queries, which is fine at this size and will not
  be at ten times it.
- **The rest of `/admin` is still a mockup.** Ledger, KYC, fraud, leaders pool
  and AI watch are all static markup with invented figures, exactly as the
  moderation section was until today.

## Member moderation screen and reporting (28/09)

- **Reporting had no entry point at all until now.** The whole reports
  pipeline — three-from-distinct-accounts, the author's own report not
  counting, urgent routing for child-safety — shipped with no way for a member
  to file one. It is on the post card now. Comments can be reported through the
  API but have no control in the UI, so half the pipeline is still unreachable
  by hand.
- **A report that fails to send used to claim it had been filed.** The first
  version set "Thanks — a reviewer will look at this" optimistically and
  swallowed the error; the first time the local gateway was down it said
  exactly that while the request had been refused. Somebody reporting a child
  at risk has to be able to believe that message, so it now waits for the
  server and offers a retry on failure. What is still withheld is the outcome —
  whether the report moved a rating — because that is what makes reporting a
  way to probe the threshold.
- **No way to see or withdraw a report you filed.** Once sent it is gone from
  the member's view entirely, and the button just reads "Reported" until the
  page is reloaded, after which it offers to report again (the server
  deduplicates, so nothing doubles, but the UI does not know that).
- **The appeal box has no character counter** against its 2000-character limit,
  and a member who writes past it gets a silently truncated field.
- ~~The member page's "we could not check" state has not been seen.~~ Checked
  on 28/09 by stopping `social-service` so the decisions call fails while auth
  stays up: the page shows "We could not check this right now — this is not the
  same as nothing being restricted" with a retry, does not fall back to the
  reassuring empty state, and recovers through the retry button once the
  service is back. Patching `fetch` in the page never worked for this, because
  the navigation needed to remount the route discards the patch; stopping the
  service is both simpler and closer to the real failure.

## Deploying leaves deleted files behind (02/10)

- **`deploy.sh` copies but never deletes, and it finally cost a deploy.** The
  tree ships as a tar extracted over the top of the last one, so a file removed
  from the repository lives on the server forever. Merging develop deleted four
  unused shadcn components whose packages had also been dropped from
  `package.json`; the stale copies on the server were still in the build
  context, so `npm run build` type-checked files that no longer exist here and
  failed on four modules nobody imports. Nine stale files were found in all,
  dating back to 16 August, including `backend/social-service/classifier.py` -
  moved to `common/` on 25/09 and still sitting in production a week later.
  They have been removed by hand. The fix belongs in `deploy.sh`: extract into
  a fresh directory and swap, or carry a manifest and delete what is not in it.
  Until then every deploy inherits whatever the last one left.
- **Nothing was lost by it this time** - the deploy failed at the build step,
  before production was touched, which is the behaviour you want. But the
  failure pointed at files that do not exist in the repository, which is a
  confusing place to start debugging.


## The imported members have not signed in yet (05/10)

- **Nobody has verified that DSM's password hashes authenticate here.** The
  library half is settled: passlib's bcrypt accepts a PHP-style `$2y$` hash
  with the same cost and salt, verifies the right password and rejects a wrong
  one, and the `error reading bcrypt version` line in the logs is trapped and
  harmless. An earlier run that raised `UnknownHashError` proved nothing - the
  test string had been mangled by shell quoting, not by passlib.
- **What is still unknown is the shape of the 14,791 hashes now in production.**
  All 14,791 rows have *a* hash, but their prefixes were never counted. If any
  of them are legacy MD5 or SHA rather than bcrypt, those members cannot sign
  in and will get "wrong password" rather than anything that explains itself.
  The check is one query - the prefix and length distribution of
  `auth.users.password_hash` - and it was not run because reading production
  was refused at the time. **Run it before telling anyone their account is
  ready.** A non-bcrypt hash is not a disaster: it means a forced password
  reset for those members, which is a decision to take knowingly rather than
  discover from support messages.
- **No imported member has actually logged in end to end.** Hash shape aside,
  the sign-in path for these accounts - no `users.profiles` row until it is
  materialised on demand, no age profile for the 7,484 without a usable birth
  date - has not been exercised once against production.

## A 200 MB limit nobody could reach (05/10)

- ~~Large photo and video posts fail against UploadCenter.~~ Fixed and deployed
  on 05/10: the client that sends the file had a flat 15-second timeout, which
  httpx also applies to the write phase, so that was the budget for pushing the
  whole body to R2 on a service advertising a 200 MB limit. The send now has
  its own budget scaled to the file.
- **The suite had been green through all of it.** Its large-file check uploaded
  an mp4 header followed by 8 MB of random bytes; local storage never looked
  inside a file, so it passed, and UploadCenter - which decodes what it is
  given - refused it for being a fake, hiding the timeout underneath. A test
  fixture that no real storage would accept is not a test of storage.
- **The deploy restarted postgres and rabbitmq, not only media-service.**
  `up -d --build media-service` recreated its dependencies too, so the whole
  platform took a short database blip for a one-service fix. Everything came
  back healthy (13/13), but a targeted restart is not as targeted as it looks.

## Changing a password is not recovering one (05/10)

- ~~There is still no "forgot password" flow.~~ Built on 05/10: ask by email,
  a one-hour single-use link, a new password, every session signed out. It is
  also what answers a legacy hash - a member whose imported hash cannot verify
  can get back in without anybody editing the database by hand.
- ~~But nothing can be sent until SMTP is configured.~~ Configured on 05/10:
  Resend over SMTP, from no-reply@kinjy.com, on a key whose account has
  kinjy.com verified. The flow is live on kinjy.com.
- ~~No mail has ever been sent from production.~~ One real message was sent on
  05/10 to the owner's own Gmail address and Resend reported it `delivered`. So
  the path works end to end: DNS, DKIM and the handoff. What that single send
  does *not* prove is which folder it landed in, or how other providers treat
  it - one delivered message to one Gmail account is a working path, not a
  reputation.
- **The sending account is shared with Digital Shopping Mall.** Its reset mail
  goes out on the same Resend key, so a bounce or spam complaint earned there
  costs Kinjy reputation too, and one address was already in a suppressed state
  on that account. Worth separating if either platform starts sending volume.
- **No Kinjy address has ever been verified.** `email_verified` is on the model
  and is never set to true anywhere, so a reset link goes to whatever address
  was typed at registration or imported from DSM. That is the usual model and is
  not wrong, but it is worth knowing it is unchecked.
- **An access token outlives the change by up to 30 minutes.** Revoking the
  sessions stops renewal, but a token already issued keeps working until it
  expires, so somebody who had the account is out within the half hour rather
  than at the moment the member presses the button. Closing that properly means
  checking `password_changed_at` against the token's `iat` on each request.
- **Nobody is told their password changed.** There is no email or notification,
  so a change made by somebody else passes unannounced. The endpoint publishes
  `user.password_changed` for a notifier that does not exist yet.
- **The new panel is in English only**, like the rest of the security screen.
  It joins the pages still waiting on the translation pass.

## Communities became groups (05/10)

- ~~A post addressed to a community was visible to nobody.~~ Fixed and deployed
  on 05/10: members post into a community, its posts reach its members' feeds
  and its own page, membership is checked before posting, and a member can
  leave. Communities have shareable URLs (`/communities/<slug>`), and the
  destination survives the sign-in door.
- ~~A public community is readable by the API without signing in, but not in
  the browser.~~ Opened on 05/10: a visitor reads a public community and its
  posts with the marketing chrome rather than the member shell, and the join
  button sends them to sign in and back. Private shows its name and description
  but not its posts, so it can be asked to join; secret stays a 404.
- **Nothing notifies a community when somebody posts in it.** Joining a group
  and then having to go and look at it is most of the way back to a forum. The
  notify helper is already used for join requests and could carry this.
- **The community page is English**, like the rest of the app pages, while the
  landing, sign-in and reset pages are in five languages.
- **No pagination on a community's posts.** The endpoint takes limit and offset
  and the page asks for the first twenty, so a busy group silently stops at
  twenty until somebody wires the rest.

## Feed, video and link previews (06/10)

- ~~A post could not be opened without leaving the feed, video used the
  browser's own player, and there was no scroll-to-load.~~ Built on 06/10:
  post dialog with comments, "people you follow who were here", scroll-to-load,
  a custom video player that follows the viewport, and link unfurling.
- **Scroll-to-load and video play-on-scroll were never seen working in a
  browser.** Both depend on IntersectionObserver, which delivers no callbacks
  while the page is hidden — and the test pane runs hidden, confirmed by
  `document.hidden === true` and by a hand-made observer on the same node also
  never firing. The data path underneath was checked instead (six pages, 120
  posts, no overlap), and the attach bug that *was* found came from reading the
  code, not from the browser. Somebody should scroll a real feed before this is
  called done.
- **A photo post previews as the generic Kinjy card until it is classified.**
  The share card only describes a post a signed-out visitor could read, and a
  freshly posted image is UNCLASSIFIED, which fails closed. That is the right
  call for safety and the wrong experience for somebody sharing a photo the
  moment they post it. Measured in the local stack only — nobody has timed how
  long classification takes in production.
- **The unfurler list in the Caddyfile is a list.** An unfurler not on it gets
  the app and therefore the generic card. That is the safe failure, but it does
  mean the list needs revisiting when a messenger matters enough.
- **Only the first link in a post is unfurled**, and only when the post has no
  media of its own.
- **Nothing refreshes a link card.** A page that changes its title keeps the
  old one for seven days, and a dead link keeps its card until then too.

## Family tree (06/10)

The tree was hidden behind one switch since 28/09. It is open **in the app**
(`FEATURES.familyTreeApp`: `/tree`, its privacy settings, the family search in
Explore). The public site's `/family` page and every line of copy advertising it
stay hidden (`FEATURES.familyTree`) on purpose, because that copy still promises
what is not built (below).

**Found and fixed, each reproduced against the running service before the change:**

- ~~"Trees are private" was still false after 20/08.~~ `GET /family/persons`
  listed **every person on the platform to anyone, with no token** (added after
  the earlier fix, to fill the screen's picker), and `/persons/{id}`, `/timeline`,
  `/duplicates`, `/heritage` and `/disputes` answered anyone too. All now answer
  the family only; a stranger gets 404, never 403.
- ~~Anyone with an account could write into anyone's tree.~~ Linking two people
  who were not members, confirming, and **disputing** (which marks the person
  `disputed`) needed no place in the tree. Writing now needs being in it; a
  member's node also needs that member's `who_can_add_family`.
- ~~The model accepted what cannot be true:~~ the same marriage written both ways,
  a spouse who is also a parent, two parent edges for one pair, a parent born
  after the child, dates in the future or out of order. Refused by the service,
  and backed by two partial unique indexes.
- ~~`PATCH` could not clear a field and could reassign `user_id` (a 500).~~
- ~~No way to remove a person.~~ `DELETE /family/persons/{id}`.
- ~~Siblings declared without a shared parent never appeared in the tree.~~
- ~~Every read loaded the whole relationships table.~~ Now only the family's
  connected component, and a tree response is capped at 250 people.

**Still not built (and stated, not hidden):**

- **Links are never verified.** A relationship is created `pending` and nothing
  moves it to `verified`; only *people* are corroborated. The screen therefore
  shows no verified/pending mark on lines, and "how are we related" reports 0
  verified links.
- **A member does not confirm a claim made about them.** The privacy setting only
  decides who may link their node; there is no "X says you are their cousin —
  accept?". (The setting's hint used to say there was; it no longer does.)
- **A dispute has no resolution path.** `disputes.status` can be `resolved` but no
  endpoint resolves one, so a disputed person stays disputed.
- **A deleted account leaves its node behind.** `user.deletion_requested` has no
  consumer; the node keeps a `user_id` that no longer exists and stays readable.
  `memorial.person_id` can likewise point at a person who was removed (services do
  not read each other's tables).
- **No age rule.** Nothing here treats a minor's node differently; the only
  protection is the owner's `who_can_see_family` / `who_can_add_family`
  settings, which parental supervision already constrains.
- **No cap on parents.** A person can have any number of `parent_of` edges; the
  graph assumes at most two for "full sibling" but does not enforce it. A product
  decision, not an invention to make here.
- **Per-branch sharing, legacy contacts, GEDCOM export, Heritage AI screens:**
  unchanged from 3e, and the reason the public page stays hidden.
- **The unique indexes need a clean table.** They are created by the service's
  migrations; on a database that already holds duplicate spouse or parent rows
  the migration fails (logged, not fatal) and only the service-level check
  applies. Look for `migration failed (family-service)` after deploying.
- Relation labels still stop at "cousin (degree N)" and otherwise say "related
  through N steps" (a step-parent shows that way), rather than invent a term.
- The tree and list views were exercised in a headless browser at 1440 and 390 px
  in both light and dark; not on a real phone, and not with a screen reader.

## Family tree against `todo copy.md` (07/10)

The product reference for the family tree is Module 5 of `todo copy.md` (untracked;
there is no `todo.md`). Compared with what was built on 06/10, before the second pass:

| `todo copy.md` says | State before this pass | Decision |
|---|---|---|
| Primitive edges only (`parent_of`, `spouse_of`, `adoptive_parent_of`, `guardian_of`, `sibling_of`); half-sibling and every complex relation computed, never stored | Matches. Checked on a blended family: full and half siblings were derived correctly from shared parents | Kept. A father/mother `role` is an *attribute of a parent edge*, not a new edge kind |
| Level model: root 0, ancestors positive, descendants negative | Matches | Kept |
| Trees strictly private, 404 for a stranger; 3 close confirmations for a deceased person | Matches (hardened on 06/10) | Untouched |
| Mobile: `Vue Générations` with **expandable cards Parents / Moi & Conjoint / Enfants** | A list by generation, **not expandable**, no such grouping | Rebuilt as expandable sections |
| Mobile: pinch-to-zoom and pan on the graph | Missing (the graph only scrolled inside its box) | Added, with zoom buttons |
| Graph view by default on desktop, generations view by default on mobile | Matches | Kept |
| Phase 2: invite a relative by link and claim the node on sign-up | Not built | Out of this pass |
| Phase 3: record oral histories on a person | Not built | Out of this pass |
| Phase 4: set `FEATURES.familyTree: true` | **Not done on purpose.** The app side is open through `FEATURES.familyTreeApp`; `familyTree` keeps hiding the public `/family` page while its copy promises what is not built | Deliberate gap, unchanged |
| *(silent)* gender, father/mother, how unions are drawn, blended families, layout rules | Gender was optional free text; no father/mother; "add a sibling" wrote a `sibling_of` edge; lines ran parent-to-child one by one and crossed cards | Decided with the product owner, below |
| *(outdated)* names `FamilyGraph.tsx` and the branch `feat/family-tree-mobile-ux` | `FamilyGraph.tsx` was replaced on 06/10; work is on `feat/family-tree` | Noted |

**Decisions where the todo is silent** (validated 07/10):

- `relationships.role` is nullable (`father`, `mother`, or unknown) and only on
  `parent_of` / `adoptive_parent_of`. `gender` belongs to the person; `role` to the
  relationship; **neither is ever derived from the other**.
- No "one father, one mother" limit. Biological and adoptive parents must coexist,
  and a rule across filiation types is not in the todo. The integrity checks that
  already exist (no loop, no duplicate pair, no parent born after the child) stay.
  The narrow rule *"within `parent_of` only, a child has at most one `father` and
  one `mother`"* is **proposed, not introduced**; it needs a product decision.
- Adding a sibling asks which parents are shared and writes real parent edges;
  `sibling_of` remains only for "parents unknown". Half-sibling stays derived.
- A union is a drawing device (the parents of a child), not a stored entity.
- End of a union (`relationships.until`) stays unexposed: the todo does not ask.
- Labels: Father / Mother when the role is known, otherwise Parent. No attempt
  yet at grandfather, uncle or cousin beyond what the graph already derives.

**Done in this pass (07/10), and what it still does not do:**

- Father / mother as a role of the parent link, gender as a choice on the person; a
  parent is added by saying father, mother or "parent, role not given". Existing
  links have no role and read "Parent" until it is set (the Links list lets the
  member who made a link set or clear it). Gender entered earlier as free text is
  shown as entered and drawn neutral unless it reads as woman or man.
- A brother or sister is added by ticking the parents they share; real parent links
  are written and half/full is derived. A child can name the other parent, so a
  person with several partners can have children from each union.
- The tree is drawn as a genealogy: generations in rows, one stem and bar per set of
  parents, partners side by side, nothing drawn across a card (measured on the
  rendered page for a simple family, many siblings, half-siblings by the mother and by
  the father, two unions, three generations, on a wide screen and at 390 px).
- Phone: Parents / the person and partners / Children as expandable sections; the
  graph zooms (pinch, Ctrl+wheel, buttons) and pans (drag).

Still open, stated rather than hidden:

- **"One father, one mother" is not enforced** and is proposed only: *within
  `parent_of`, a child has at most one `father` and one `mother`*. Adoptive links are
  excluded on purpose. It needs a product decision before it becomes a rule.
- **Parents on two different generations** (a person reached by two paths at different
  levels) can make a stem run across a card in between. Not seen in any family built
  here; the layout does not detect it.
- **More than two partners in a row**: a person can touch only two of them; the others
  are joined by a line under the cards (a person with three unions in a row is placed
  without overlap or crossing, but one of the partners is not adjacent).
- **Pinch and drag were exercised with emulated touch**, not on a real phone.
- Phases 2 (invite a relative by link, claim the node on sign-up), 3 (oral histories)
  and 4 (`FEATURES.familyTree`) of the todo are untouched.

### Layout engine rewritten (07/10, third pass)

The positions used to come from a relaxation (order by the parents' average, then a
least-squares compaction), so nothing guaranteed that a union's children were centred
or that a subtree stayed together. They now come from `arrange.ts`, a pure function
of the family structure: unions (the parents of a child) → chains (partners and
co-parents side by side) → one block per chain with a group of children per union →
bottom-up packing by contour → each group centred exactly under the point between its
parents (parents move apart rather than the children together) → top-level families
packed the same way. Ties are broken by birth date then name, never by the order the
server lists things in, and the person you are viewing is not the origin.
`layout.ts` only turns the positions into cards and connectors.

- Data model: unchanged. The one server change: adding a brother or sister with exactly
  one shared parent can name their *other* parent (someone in the tree who is not a
  parent of the person added to), so a half-sibling is a child of the other union and is
  never drawn under the first person's other parent. Parents unknown stays declared siblings.
- Tests: `app/scripts/test-family-layout.mjs` runs the algorithm on synthetic families
  (no browser): same generation = same Y, no overlap, no connector through a card,
  parent above child, each child drawn under its real parents only, children centred
  on their union to the pixel, half-sibling in the right group, unions not interleaved,
  same positions for the same data in any input order, and a new grandchild not
  reordering the rows above. Run: `docker compose exec web node scripts/test-family-layout.mjs`.
- Honest limits: two families joined by a marriage (a person who is a child of two
  trees) are drawn as two trees side by side; the second one's parents stand as near as
  they can to their child and the bar runs longer (`Layout.crossLinked`), it is not
  centred. A mother between two unions with wide descendants gets a long partner line.
  A tree this wide shows small cards when fitted on a desktop (zoom floor lowered to 25%).
- The line between two parents of the same child belongs to the union and is computed
  from the final positions; a `spouse_of` link is not needed to draw it (and none is
  created). When the pair are also recorded spouses, the spouse line is the one drawn.

## Layout, cards and the black /hub (06/10)

- ~~`/hub` went black on refresh.~~ Fixed on 06/10: three paging hooks sat
  after SocialHub's `if (authLoading)` return, so the first render ran fewer
  hooks than the second — React error #310, unmounted tree. It only ever showed
  on a *fresh* load, because that is the only time auth starts in a loading
  state; reaching /hub from inside the app had auth already resolved and looked
  fine. Verified on production by hard-refreshing /hub signed in.
- ~~The feed card was see-through and the columns wasted a third of the
  screen.~~ Fixed: `--cloud` is opaque `#1C1F2D` under `[data-theme='cloud']`,
  the shell is `max-w-app` (1600px), and the right rail scrolls on its own.
- **The opaque card depends on `.force-dark` / `.twilight-field` being on every
  section that paints its own background.** Those classes redeclare `--cloud`,
  which is the only reason the marketing heroes keep their glass — the
  `[data-theme]` selector scopes nothing, since AppThemeProvider wraps the
  whole tree from main.tsx. A new dark section without one of those classes
  will get opaque cards against its background and look wrong. Verified on
  /creators, not on every marketing page.
- **The layout, dialog and push-toggle work was verified on production, not
  locally.** `app/node_modules` was empty for part of this session, so the
  local dev server could not be trusted; it has since been reinstalled (253
  packages) and `tsc -b` is clean.
- **`demo.diag.fbirbgei@example.com` is a real account on production**, created
  for these checks. It should be removed.
- **The imported `$2y$` hash prefixes still have not been verified against a
  real production row.** One query; it has been blocked twice.

## Mobile (06/10)

- ~~The site could be dragged sideways on a phone.~~ Fixed: `min-w-0` on the
  nav-demo column (/platform was 1535px wide on a 375px screen), and
  `overflow-x-clip` on the page shell for the scroll-reveal sections that sit
  at a translateX until they animate in.
- ~~Text bottomed out at 8px and icon buttons at 36px.~~ Floors added: 0.72rem
  (11.5px) under the phone breakpoint, 44px targets on a coarse pointer.
- **This is not a mobile-first redesign.** What was done is remedial: no
  sideways scroll, a legible type floor, thumb-sized targets - measured on
  every route at 375px. The layouts themselves are still desktop layouts that
  collapse to one column, and several pages deserve a phone-first pass in their
  own right rather than a `lg:` prefix removed here and there.
- **The floors are blanket CSS overrides, not corrected call sites.** ~470
  usages of the small tiers still say `text-[0.6rem]` in the markup and only
  look right because of the media query in index.css. Somebody changing the
  type scale needs to know that rule is there.
- **Verified by measurement, not by eye.** Screenshots timed out for most of
  this session because the browser pane was hidden, so the evidence is
  geometry: `scrollWidth`, bounding boxes, computed font sizes. Nobody has
  actually looked at these pages on a phone.
- **`pointer: coarse` was exercised in emulation only.** A real touch device
  may match differently.

## Sync of main into develop (08/10)

What came from where: replies, the composer's emoji/sticker panel, the redesign and the voice-note player are develop's; reactions (one emoji per person, counts not names), edit, delete, the sticker catalogue (glyphs, packs, age tier), push notifications, the arrival sound and the message toast are main's. A sticker reaction (BART's) no longer exists.

- **Anything that deletes a message leaves its attachment behind.** `DELETE /conversations/{id}/messages/{id}` clears the text and the reference, but media-service keeps the file, and a signed link issued before stays valid for up to its lifetime. Both `/internal/media/*/discard` endpoints exclude chat attachments on purpose. It belongs with the chat-attachment lifecycle work (`e2e_chat_attachment_expiry.py`), not with a patch on delete.
- **Deleting is allowed to a blocked member, and nothing keeps what was deleted.** Retracting your own words is a feature, but on a platform that takes child safety seriously it also means a message can be wiped after it was reported. A decision, not an oversight: either keep a short-lived evidence copy for reported messages, or accept it.
- **`/internal/*` authenticates nobody.** The gateway not routing them is the only protection, which is why one server-side request forgery (the push endpoint, fixed in this sync) reached order funding and age review. A shared-secret header on internal calls would make the next one harmless.
- **A push endpoint known to somebody else can be re-registered under their account** (the row is reassigned by design, so a browser that changes account keeps working). The endpoint is a long unguessable URL, so it takes a leak first; not changed.
- **`/forums` and `/tree` have the redesign's layout only partly**: both were rewritten on develop while the redesign restyled the old versions, so develop's pages were kept whole.
- **Explore's empty state** shows main's discovery lists (people to connect with, spaces to join) instead of the redesign's "Start typing to explore" card.
- **Legacy sticker reactions** are kept in `messaging.message_reactions_legacy` after the migration renames them; nothing reads them. Drop the table once nobody needs them.

## The develop merge (11/10)

- ~~main was 117 commits behind develop.~~ Fast-forwarded and deployed: 282
  files, 8 backend services, 6 additive migrations, verified on production.
- **Three branches are still outside develop** and were deliberately not
  merged - they are other people's in-flight work, and integrating them is
  their call, not a deploy step:
  - `design/all-refonte` (17 commits) - reels, per-column scrolling, age
    declaration, video in comments
  - `feat/community-forum-access-v2` (2 commits) - paid seats, secret entry,
    forum governance, knowledge base
  - `feat/voice-note-player-and-adaptive-timestamp` (2 commits) - waveform
    player, inline timestamps
- **The feed is 722px again, not 560.** The redesign replaced AppShell with a
  1320px container and 220/1fr/300 columns, which dropped both the reading cap
  and the `wide` prop Messages used to opt out of it. Messages is fine - it
  gets the full column now - but the LinkedIn width that was asked for on
  06/10 is gone. Restoring it means capping the feed page rather than the
  shell, so the redesign's own layout is left alone.
- **`eslint .` reports 54 problems on develop** (49 errors, mostly
  react-hooks/set-state-in-effect). They are develop's own state, not the
  merge's: the merge added no commits. `tsc -b` and `vite build` both pass, and
  lint is not in the build, so nothing is blocked - but the gate is red.
- `app/src/components/family/FamilyGraph.tsx` was deleted upstream. deploy by
  tar never removes files, so the stale copy was moved to
  /root/removed-FamilyGraph.tsx.bak on the server. Nothing imports it.
- Pre-merge source backup: /root/kaluta-pre-merge-20261011-1053.tar.gz

## Group privacy and share links (16/10)

- ~~A group's privacy was fixed at creation.~~ The owner or a moderator now
  switches it between public, private and secret from the group page
  (`PATCH /communities/{id}/privacy`). Opening a private group admits everyone
  who was waiting and tells them; closing a public one keeps its members. A
  paid group stays paid (409): turning it free would change what members
  bought. Verified locally, `e2e_group_privacy.py`, 38/38.
- ~~A group had no link to send.~~ Every group page has "Share link", whatever
  its privacy. Public and private open from their plain address; a secret one
  gets `?access=<code>`, which only a member can obtain, opens the name and
  description (never the posts) and lets the visitor *ask* to join.
- **Found while doing it, and closed:** `POST /communities/{id}/join` let
  anybody who knew a secret group's id walk straight in as an active member.
  It is now a 404 without the access code, and a request with it.
- **The access code cannot be rotated.** Once a secret link has leaked there
  is no way to kill it: switching the group to private and back to secret
  keeps the same code. A "reset link" action is needed. (A leaked link still
  only lets people ask; a moderator decides.)
- **A privacy change leaves no trace for the members.** Only those admitted by
  an opening are notified; nobody sees "X made this group private".
- Deployed on kinjy.com 16/10 15:10 (community-service + web only; backups
  `/opt/kaluta/backups/pre-group-privacy-20261016-1508.sql.gz` and
  `/root/kaluta-pre-group-privacy-20261016-1508.tar.gz`). Checked from outside:
  13/13 healthy, the new routes answer, `invite_code` exists. The e2e test was
  run locally only, not against production, to keep test accounts out of it.

## Pinned groups, the KYC fee, and Follow (16/10)

- ~~The Follow button on a profile forgot it had been pressed.~~ The profile
  page never asked whether the viewer already followed; it started at
  "Follow" every time. Notifications (browser and in-app) and Connections all
  lead to that page, which is why it looked tied to where the first click
  happened. `/connections/with/{id}` now returns `following` and
  `follows_you`; the button starts from the server's answer, says "Follow
  back" when they follow you, and a second press unfollows. The server never
  double-counted (it answered "already"), so no follower count needs repair.
- ~~Groups could not be pinned.~~ A member pins a group from its page; pinned
  groups show in the left rail. Three are free; a fourth answers 402 until
  this year's KYC fee is paid. The pin lives on the membership: leaving drops
  it, a ban hides it.
- ~~The KYC fee could be paid for one cent.~~ The checkout took the amount
  from the client for `kyc_fee`. It is now the configured fee or a 400, and a
  second payment in the same year is a 409.
- ~~The Verification tab said "Pay the fee" with nothing to press.~~ It has a
  pay button now.
- **In production the fee is not real money yet.** No NowPayments or
  Mangopay key is set on kinjy.com, so payments run in test mode and "Complete
  the test payment" marks the fee paid. Until a rail is configured, the
  fourth pin is effectively free - as are marketplace orders.
- **No reordering of pins**, oldest pin first. **The fee is per calendar
  year**: on 1 January pins above three stay, but no new one can be added
  until the new year's fee is paid.
- Verified locally: `e2e_pins_follow.py` (37 checks) and the neighbouring
  suites. Deployed on kinjy.com 16/10 15:42 (community, payment, user, web;
  backups `pre-pins-follow-20261016-1540`). Checked from outside only: the
  routes answer, `pinned_at` exists, `/internal/kyc` is not reachable from
  the internet. Not clicked through in production with a real account.

## Chat groups: privacy, links, pins; Video Only; a safety hole (16/10)

- ~~"Video Only" showed text and photo posts.~~ The date-ordered modes (New,
  Following) dropped the algorithm entirely, its filter included, and said
  "The current mode ignores it by design". An algorithm both *filters* and
  *ranks*; those modes now keep its filter and refuse only the ranking. The
  filter is also pushed into the query in ranked modes, so a Video Only feed
  no longer runs dry when the 500 newest posts hold few videos. A short counts
  as a video.
- ~~Chat groups had no privacy and no link.~~ Admins switch a group between
  public and private; any member gets its link (`/messages/join/<code>`, a
  random code, never the id). Public lets the holder in; private makes a
  request admins answer from the group panel. Members can pin groups to the
  top of their list: three free, more with this year's KYC fee. Verified
  locally, `e2e_chat_group_access.py` (47 checks).
- **Links and minors, by design:** nobody under 18 (or of unconfirmed age)
  joins by link, and no adult joins by link a room with a minor in it. Same
  refusal sentence for both, so it reveals nothing. An admin can still add a
  teen they are connected to, through the existing per-person checks.
- ~~Every adult counted as "connected" to every minor.~~ `agecheck.is_connected`
  counted *any* accepted conversation the two shared, and the welcome group
  (13/10) puts everybody in one. So the adult-to-minor rule on private
  messages was off for the whole platform. Only a direct conversation counts
  now. In production `contact_attempts` shows no adult-to-minor contact
  allowed since, so it was not used. `e2e_messaging_age.py` was failing on it
  and nobody had noticed.
- **Still failing, not fixed:** `e2e_messaging_age.py` expects a teen added
  to a group to be "unaccepted" until they reply, so media waits. Since 13/10
  group members are accepted on arrival, by design, so a teen added to a
  group by somebody they may be reached by gets images at once. Decide.
- **Still failing, not fixed:** `e2e_unread_count.py` - a new member starts
  with hundreds of unread messages: every arrival writes a system line into
  the welcome group, and system lines count as unread.
- **The welcome group itself mixes adults and minors** in one room anybody
  can write in. The age rule protects private messages, not that room.
- The share link of a chat group cannot be reset yet.
- Deployed on kinjy.com 16/10 16:30 (messaging, social, web; backups
  `pre-chat-groups-20261016-1629`). Checked from outside: 13/13 healthy, New +
  Video Only returns only video, the age fix is in the running container.
  Not clicked through in production with a real account.

## Teenagers added to a group, and requests that every deploy accepted (16/10)

- ~~A teen added to a group received photos at once.~~ Since 13/10 everybody
  put into a group was marked accepted. Now a minor (or anyone whose age
  cannot be read) arrives unaccepted - on creation, when added, and in the
  welcome group. They read the text; attachments from others are **withheld
  from them** (no signed link, on the read path and in the live frame) until
  they press Accept, or reply. The sender is not refused: one teen who has not
  accepted would otherwise stop every photo in the room, the welcome group's
  included. Adults added to a group are accepted at once, as before.
- ~~Message requests had no Accept button.~~ A banner in the thread now offers
  it, for groups and for direct requests.
- ~~Every deploy accepted every pending request.~~ A migration meant as a
  one-off backfill ran on every boot (`UPDATE participants SET accepted_at =
  joined_at WHERE accepted_at IS NULL`), so each restart opened every request
  and unlocked attachments to people who had never answered. It is bounded to
  rows from before 25/09 now. **In production it already ran on every deploy:
  0 of 253 participants were pending on 16/10, and which ones had been pending
  cannot be recovered.** Existing teens in groups therefore stay accepted;
  only new arrivals wait.
- Tests: `e2e_teen_group_media.py` (11 checks, including a restart; it fails
  against the old migration), and `e2e_messaging_age.py` rewritten for the
  group case - its old photo was never a real attachment, so it proved
  nothing. 22/22.
- Deployed on kinjy.com 16/10 16:58 (messaging, web; backups
  `pre-teen-media-20261016-1657`). Checked from outside and in the running
  containers; not clicked through in production with a real teen account.

## A new member's hundreds of unread messages (16/10)

- ~~A new account opened Messages on hundreds of unread messages.~~ Two causes
  in the one query the badge and the list share: the welcome group's history
  from before the member arrived counted, and so did the room's own lines -
  "X joined Kinjy" is written once per registration, sender empty, which is
  "not me". Now neither counts: unread starts at your arrival, and system
  lines are never unread. A real message in the welcome group still counts.
  The thread's "new messages" divider skips system lines too.
  `e2e_unread_count.py` 17/17 (11 were failing since 13/10),
  `e2e_welcome_unread.py` 8/8.
- Deployed on kinjy.com 16/10 18:38 (messaging, web; backups `pre-unread-20261016-1752`).

## Group panel dialog, chat mentions, media inside articles (16/10)

- **Share, privacy and pin** moved out of the group panel into its own dialog
  (rendered on `document.body`, so the panel's overflow cannot clip it). The
  panel keeps a one-line summary: public/private, pinned, and how many people
  wait to join, so an admin still notices the queue.
- ~~No @mentions in chat.~~ Typing `@` and two letters offers the people in
  the room (the other person in a direct conversation, the members of a
  group, a platform search in the welcome group, where everybody is a member).
  `@handle` in a bubble links to the profile. The person named is notified -
  bell, live and push, never quoting the message - **only if they are in the
  conversation**, and even in the welcome group, where ordinary traffic
  notifies nobody. End-to-end encrypted messages carry no plaintext, so their
  mentions notify nobody. `e2e_chat_mentions.py` 10/10.
- ~~Articles could not carry pictures or video in their text.~~ The editor
  inserts a picture or a clip where the caret is (toolbar, paste, drag and
  drop), and embeds YouTube or Vimeo from a pasted address or the toolbar. A
  placeholder holds the spot while the file uploads, so typing on does not
  move it. **Inline files are real attachments of the post**: ownership
  checked, classified (a post with a picture still waits for a human), signed
  per reader. The text stores only `<figure data-media-id data-kind>`; the
  signed address is filled in at display, and a marker naming a file that is
  not on the post shows nothing. Embeds are stored as provider + id and
  rebuilt into a youtube-nocookie / player.vimeo.com iframe; a pasted
  `<iframe>` is never kept. Inline media are left out of the grid under the
  post, so nothing appears twice.
- **Not done:** no caption or alt text on an inline picture yet; no way to
  move a figure once placed other than cutting and re-inserting it. Uploads
  dropped from the text before publishing stay as orphan files in
  media-service. (Editing an article's media: done 17/10, below.)
- Deployed on kinjy.com 16/10 19:12 (messaging, social, web; backups
  `pre-mentions-articles-20261016-1910`). Checked from outside and in the
  running containers; not clicked through in production with a real account.

## The registered office on every page (17/10)

- ~~The address was on the old footer only.~~ `lib/company.ts` has carried
  "77 High Street, #10-12B High Street Plaza, Singapore 179433" since 06/10,
  but the home page and the redesigned public pages use `LandingFooter`,
  which did not show it, and a signed-in member has no footer at all. It is
  now under the copyright on every public page, and at the foot of the
  "Spaces & help" card in the app's left rail. One source, `COMPANY`.

## Editing a post: a dialog, and articles that could not be edited (17/10)

- ~~An article could not be edited.~~ The card always drew the article, so
  the in-place textarea never appeared; and its draft was the article
  stripped to plain text, so saving would have wiped every heading, list and
  picture. **Every post is now edited in a dialog** (`EditPostDialog`): an
  article with its headline and the same rich editor it was written in -
  pictures and clips shown, added, taken out - and any other post as text.
- Server side, an article edit that sends `media` syncs its attachments with
  its text: a newly placed file is checked like at creation (own, finished
  post media; somebody else's is refused), a file taken out of the text is
  detached, and the grid under the post is left alone. A plain correction
  (no `media`) touches no attachment. A picture added by an edit puts the
  post back to UNCLASSIFIED / pending human review (`edit:heuristic:
  media_opaque`), verified. `e2e_article_edit.py` 14/14.
- Still 24 hours to edit, as before. A detached picture's file is not
  deleted from media-service.

## Links on highlighted words, mentions with usernames, the group dialog (17/10)

- ~~A link could not be put on highlighted words in an article~~ (reported by
  a member: "Open the Kinjy Family Tree" -> kinjy.com/tree landed at the start
  of the article). `window.prompt` took the focus, the selection went with
  it, and `createLink` ran on an empty caret at the top. The address is now
  asked for in a field under the toolbar; the selection is saved before and
  restored after. Nothing highlighted inserts the address itself where the
  caret was. A bare `kinjy.com/tree` becomes `https://kinjy.com/tree`. The
  video-embed address uses the same field. (That member also could not edit
  the article: fixed by the edit dialog, not yet deployed when reported.)
- ~~Mentions showed a name or a username, never both.~~ Posts, comments and
  chat now draw "Name @username" (`MentionLink`). Chat pages and live frames
  carry `mentions` (handle -> name), resolved in one call per page.
- ~~Group information replaced the conversation as a side panel.~~ It is a
  dialog over the thread: cover across the top, the group photo over its
  edge, name, public/private and member count, description, then cards for
  sharing and privacy and for members. The large photo/cover pickers only
  appear in edit mode. Checked in dark and light themes.

## Share previews, video covers, the player, long posts in previews (17/10)

- ~~A shared post showed Kinjy's generic card.~~ Crawlers do not run the app,
  so they only ever read index.html. nginx now answers `/p/<id>` from
  social-service (`/og/p/<id>`): the same page, its head carrying the post's
  title (article headline, or "<name> on Kinjy"), description (its text, tags
  dropped, 200 chars; "A photo/video by <name>" when there is none) and
  picture (`/api/og/image/<id>`: the first picture or the video's cover, as a
  JPEG <= 1200 px; else the author's avatar; else og-image.png). **Only what a
  signed-out visitor may see**, judged by the same two checks as the post's
  page: a followers-only post, a missing one, or one still waiting for review
  gets the generic card with no author and no text. A post with a picture is
  therefore previewed with it only after a human has reviewed it.
  `e2e_share_preview.py` 19/19.
- ~~Videos had no cover.~~ media-service now has ffmpeg (its image only) and
  takes a JPEG a second into every video at upload, never for a sealed chat
  attachment. Served behind the video's own ticket (`?poster=1`); posts carry
  `poster_url`. `/internal/media/backfill-posters` covers older videos.
- ~~The player gave no sign of loading, and articles and chat used the
  browser's controls.~~ `VideoPlayer` rewritten: the cover (or a gradient)
  until the first frame, a spinner from the press of play and on every stall,
  a seek track showing what has loaded (drag to seek), speed, sound, real
  fullscreen, keyboard (space/K, arrows, M, F), a retry on error. Used in the
  feed, the viewer, inside articles (mounted into the article's video slots)
  and in chat.
- ~~Long posts read badly in previews.~~ The post dialog shows the text whole
  (no second "see more") with a sticky close button; beside a picture the
  text folds after nine lines so the comments stay in reach, the column is
  wider, and an article keeps its formatting there.
- **Pre-existing, not fixed:** `e2e_media_preview.py` fails "a small picture
  is served as it is" (gets WebP) - the same with the code before this change.
- Deployed on kinjy.com 17/10 13:02 with the three commits before it (address,
  edit dialog, links/mentions/group dialog): media (now with ffmpeg), social,
  gateway, messaging, web; backups `pre-share-video-20261017-1300`. Video
  cover backfill run: 20/20 built. `docker-compose.prod.yml` gives
  social-service its own `OG_INDEX_URL=http://web/index.html` (the base file
  points it at Vite for dev).
- **In production every public post with a picture or video is still waiting
  for a human review (46 unclassified, 6 rated adult), so none of them can be
  previewed with its picture yet** - they share with the generic card. Text
  posts show their own title and description. Clearing the review queue in
  the Trust & Safety console is what turns their previews on.

## Edits that lost the pictures added to them (17/10)

- ~~A picture added while editing was lost.~~ Two causes. (1) Saving while a
  picture was still uploading saved without it: the editor puts a
  placeholder down at once, and a placeholder has no media id, so it is not
  part of what is saved. Save (edit dialog) and Post (composer, articles
  included) now wait - the button reads "Uploading..." and is disabled until
  every file is in. (2) An ordinary post's edit dialog had a text box only, so
  a picture could not be added at all and a pasted one vanished.
- **Ordinary posts can now change their pictures and clips when edited**: add
  (button or paste), take off, up to 4. The edit sends the list the post
  should carry; files already there are kept, new ones are checked as at
  creation (somebody else's refused), the rest detached, and the format
  follows (a post with a video becomes a video post; nothing left, text). A
  new picture sends the post back to human review. `e2e_post_media_edit.py`
  14/14.
- The edit answer was signed for an anonymous visitor rather than the author;
  it is signed for the author now.
- Deployed on kinjy.com 17/10 17:28 (social, web; backups `pre-edit-media-20261017-1727`).

## Mentions show the name only (17/10)

- A mention now reads as the person's name everywhere (posts, comments,
  chat), linked to their profile; the @username is the link's tooltip and is
  still shown in the picker while typing. An unresolved handle stays as
  typed. (Asked for on 17/10, replacing "Name @username".)
- Reported in the same screenshot: a shared article's preview showed Kinjy's
  card instead of its first picture. By design for now: an article with a
  picture is previewed with it only after a human review, and that article is
  still in the queue.

## Shared links still showed Kinjy's default card (17/10)

Three causes, all fixed:
- **Crawlers never reached the new preview.** Caddy sends WhatsApp, Facebook,
  X, LinkedIn... (by User-Agent) to `/api/share/p/<id>`, an older second
  implementation that only previewed reviewed posts; nginx's `/og/p/<id>`
  only ever served browsers. The old route now returns the new one's page.
- **A link inside Kinjy (chat, posts) was fetched as a stranger and cached
  for a week.** Every Kinjy link became the generic card - a post with a
  picture is restricted until review, and a followers' post is invisible to
  a stranger - and stayed so for 7 days. Links to a Kinjy post or group
  invitation are now answered from the inside, for the member looking: the
  post's words and picture (signed for them) if they may see it, "You cannot
  see this post." otherwise (a minor, a non-follower); a group link shows the
  group's name, description and photo. Never cached across members.
- **Outside, a post waiting for review showed nothing of itself.** If its
  text alone rates for everyone, the preview now shows its title, its words
  and its author's picture; the post's own picture joins after the human
  review (still the rule: an unreviewed picture is not shown to whoever a
  link reaches, children included).
- Previews already cached by WhatsApp or Facebook for links shared before
  this stay as they were; Facebook's Sharing Debugger re-reads a link.
- `e2e_own_link_preview.py` 18/18, `e2e_share_preview.py` 22/22.
- Deployed on kinjy.com 17/10 22:15 (social, messaging, web; backups
  `pre-share-links-20261017-2214`). Checked with WhatsApp, Facebook and
  browser User-Agents: the same title, description and picture for each.

## The staff team and its roles (17/10)

- ~~Nobody could reach the console~~: no account on kinjy.com had a staff
  role, and there was no way to give one. Three staff roles now:
  **moderator** (post review, appeals, memorial death reports, contact risk,
  age review - no money, no accounts), **admin** (the whole console),
  **superadmin** (also manages the team). Moderation routes moved from
  `AdminUser` to `ModeratorUser`; money, KYC, disputes, ads and the ledger
  stay admin-only.
- `/team` (left rail "Team" and "Review console" for staff): a superadmin adds
  somebody by email or @username with a role, changes a role, or takes them
  off (back to member). Nobody changes their own role and Kinjy always keeps
  one superadmin. A change reaches the person at their next token refresh,
  without signing out. `e2e_team.py` 21/21.
- godsonferdinand7@gmail.com (@ferdinand7) is the first superadmin, set by
  hand in the database on 17/10 at the owner's request.
- **Not done:** no audit trail beyond the service log line; a demoted
  member's current access token keeps its old role until it expires (minutes).
  `e2e_graveyard.py` mints its admin token with the local JWT secret, so it
  only runs inside a container - pre-existing.
- Deployed on kinjy.com 17/10 22:51 (auth, social, messaging, memorial,
  gateway, web; backups `pre-team-20261017-2250`), with the mention change.

## Sonco Pay payments, Kaluta KYC, payout wallet and withdrawals (18/10)

- **Every payment goes through Sonco Pay** when `SONCOPAY_API_KEY` is set:
  checkout creates a Sonco Pay payment (`order_id` = Kinjy payment id) and the
  member pays on Sonco Pay's page, then comes back. Settled only when the
  payment read back from Sonco Pay is paid, USD, for the exact amount -
  webhook signature checked (`/api/payments/webhook/soncopay`), and the mock
  shortcut is off.
- **KinjyKYC = Kaluta KYC** after the fee: `/kyc/start` opens a session with
  name, date of birth and country; reopening it costs no attempt; an expired
  session gives the attempt back. The Kaluta webhook (`/api/kyc/webhook/kaluta`)
  is unsigned, so it is a doorbell: the state is read back from Kaluta.
- **Payout wallet** (USDT on BSC only): the member saves it, confirms it from
  an e-mail link (24 h, one use, works signed out), then an admin approves it
  on `/admin/wallets`; the admin's id and name are kept and shown. An admin
  cannot approve their own. A new address starts again and blocks withdrawals.
- **Withdraw** (Earnings tab): the whole available balance, automatic. The
  ledger reserves it under the withdrawal id first (`payout_holds`, locked
  wallet), one withdrawal in flight per member (database index), Sonco Pay
  payout `reference` = the same id. Refusal or failed payout gives the money
  back once; a timeout keeps it reserved and a reconciler (every 5 min, or
  `/admin/payouts/withdrawals/reconcile`) finishes it.
- Minimum withdrawal = Sonco Pay's `min_payout_usd` ($1 live, 18/10) plus the
  network fee, because the **fee is paid by the member**
  (`PAYOUT_FEE_PAID_BY=member`; `platform` makes Kinjy pay it). Withdrawals
  are paid **from Kinjy's own Sonco Pay balance**: it must hold USDT BSC.
- Tests: `e2e_soncopay.py` 55/55 against `fake_sonco_kaluta.py`
  (`compose.sonco-test.yml`); ledger reconcile drift 0; older payment tests
  pass in mock mode.
- **Not done:**
  - **subscriptions and ad credits still have no purchase screen**, so
    nothing calls checkout for them;
  - no screen for the admin withdrawal list (the route exists);
  - the old `/payments/destinations` and payout batch are refused while
    Sonco Pay is on, not removed;
  - no real payment, KYC session or withdrawal made yet: the first real
    ones are the test (a Kaluta session is billed, so none was opened to try).
- **Deployed on kinjy.com 18/10** (ledger, auth, payment, web; backup
  `backups/pre-soncopay-*`). Live key checked from the server with read-only
  calls: USDT:BSC fee $0.23, Sonco Pay minimum $1, so a $1.23 minimum
  withdrawal. **The Sonco Pay balance was empty**: withdrawals are refused
  (and the amount given back to the member) until it holds USDT BSC.
