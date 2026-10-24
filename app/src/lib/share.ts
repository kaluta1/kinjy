/**
 * Links that leave the platform, and the invitation they carry.
 *
 * A shared post is the only piece of Kinjy most people will ever see before
 * deciding whether to join, so the link has two jobs: open the post for
 * somebody with no account, and credit the member who sent it if that person
 * does join.
 *
 * The credit is the member's own referral code — the same one the direct 20%
 * programme pays on — added as `?ref=`. It is added only for a member who is
 * signed in, because a code is something an account has; a visitor sharing a
 * post has nothing to attach and the link is simply a link.
 */

const REF_KEY = 'kinjy.pending_ref'
/** When it arrived, so support can tell a week-old link from this morning's. */
const REF_AT_KEY = 'kinjy.pending_ref_at'
/** Who it belongs to, so the sign-up page can name them. */
const REF_WHO_KEY = 'kinjy.pending_ref_name'
/**
 * A short link's token, held instead of a code: the invitation of whoever
 * shared it, which the server looks up at sign-up. One or the other is held,
 * never both - the newest invitation wins, as it always has.
 */
const LINK_KEY = 'kinjy.pending_link'
/** Where that link was taking them, so sign-up can bring them back there. */
const LINK_DEST_KEY = 'kinjy.pending_link_dest'
const LINK_TOKEN_RE = /^[0-9A-Za-z]{9}$/

/**
 * The shape a code can have: six to eight of A-Z0-9.
 *
 * `_referral_code` in auth-service takes `secrets.token_urlsafe(6)`, drops
 * the two separator characters and uppercases what is left - so eight
 * characters usually, six or seven when a separator was drawn.
 *
 * This is a filter on obvious rubbish, not proof. "WHATSAPP" fits it exactly,
 * which is why ReferralWatch asks the server before letting a code off a link
 * displace one already held.
 */
const REF_RE = /^[A-Z0-9]{6,8}$/

/**
 * Pull an invitation out of a URL.
 *
 * `ref` is the name the invite links use. The others are what people actually
 * paste: a link that went through a mail client, a shortener or somebody's
 * analytics comes back with the code under a different name, and refusing to
 * read it loses the credit the member earned.
 */
export function refFromSearch(search: string): string | null {
  const params = new URLSearchParams(search)
  for (const name of ['ref', 'invite', 'invited_by', 'referral', 'referral_code']) {
    const value = (params.get(name) ?? '').trim().toUpperCase()
    if (REF_RE.test(value)) return value
  }
  return null
}

/** Where this build lives, for links that will be read outside the app. */
export function siteOrigin(): string {
  return window.location.origin
}

/** The public address of one post. */
export function postUrl(postId: string, ref?: string | null): string {
  const url = new URL(`/p/${postId}`, siteOrigin())
  if (ref) url.searchParams.set('ref', ref)
  return url.toString()
}

/**
 * Remember an invitation code that arrived with a link.
 *
 * Someone who opens a shared post and then reads three more pages before
 * signing up must still credit whoever sent them. Without this the code lived
 * only in the URL of the first page and was lost on the first click.
 *
 * Kept per browser and deliberately not merged with anything else: it is a
 * hint about where somebody came from, not an identity.
 */
export function rememberRef(ref: string | null | undefined): void {
  if (!ref) return
  const code = ref.trim().toUpperCase()
  // Anything that is not a code is somebody playing with the query string,
  // and it must not displace a real one that is already here.
  if (!REF_RE.test(code)) return
  try {
    if (localStorage.getItem(REF_KEY) === code) return
    localStorage.setItem(REF_KEY, code)
    localStorage.setItem(REF_AT_KEY, new Date().toISOString())
    localStorage.removeItem(LINK_KEY)
    localStorage.removeItem(LINK_DEST_KEY)
    // A new code is a new inviter. Leaving the old name behind would have the
    // sign-up page welcome somebody on behalf of the wrong person.
    localStorage.removeItem(REF_WHO_KEY)
  } catch {
    /* private window, blocked storage: the sign-up still works, uncredited */
  }
}

/** The code to offer the sign-up form, if one arrived earlier. */
export function pendingRef(): string {
  try {
    return localStorage.getItem(REF_KEY) ?? ''
  } catch {
    return ''
  }
}

/**
 * Remember the short link somebody arrived on: its token and where it went.
 * The newest invitation wins, so it replaces a code held from an older link.
 */
export function rememberLink(token: string, destination: string, inviter: string | null): void {
  if (!LINK_TOKEN_RE.test(token)) return
  try {
    localStorage.setItem(LINK_KEY, token)
    localStorage.setItem(LINK_DEST_KEY, destination)
    localStorage.setItem(REF_AT_KEY, new Date().toISOString())
    localStorage.removeItem(REF_KEY)
    if (inviter) localStorage.setItem(REF_WHO_KEY, inviter)
    else localStorage.removeItem(REF_WHO_KEY)
  } catch {
    /* blocked storage: the sign-up still works, uncredited */
  }
}

/** The short link's token held for sign-up, if one is. */
export function pendingLink(): string {
  try {
    return localStorage.getItem(LINK_KEY) ?? ''
  } catch {
    return ''
  }
}

/** Where the held short link was going - only a path of our own. */
export function pendingLinkDestination(): string | null {
  try {
    const value = localStorage.getItem(LINK_DEST_KEY)
    return value && value.startsWith('/') && !value.startsWith('//') ? value : null
  } catch {
    return null
  }
}

/** Once it has been used, it should not follow them into a second account. */
export function clearPendingRef(): void {
  try {
    localStorage.removeItem(LINK_KEY)
    localStorage.removeItem(LINK_DEST_KEY)
    localStorage.removeItem(REF_KEY)
    localStorage.removeItem(REF_AT_KEY)
    localStorage.removeItem(REF_WHO_KEY)
  } catch {
    /* nothing to clear */
  }
}

/** The name on the stored invitation, when the server told us one. */
export function pendingInviter(): string {
  try {
    return localStorage.getItem(REF_WHO_KEY) ?? ''
  } catch {
    return ''
  }
}

/** Remember who the stored code belongs to. */
export function rememberInviter(name: string | null | undefined): void {
  try {
    if (name) localStorage.setItem(REF_WHO_KEY, name)
    else localStorage.removeItem(REF_WHO_KEY)
  } catch {
    /* the code still works without a name to put on it */
  }
}

/** When the stored invitation arrived, if one has. */
export function pendingRefAt(): string | null {
  try {
    return localStorage.getItem(REF_AT_KEY)
  } catch {
    return null
  }
}

/**
 * Hand a link to whatever the device uses for sharing, falling back to the
 * clipboard.
 *
 * Returns how it was shared so the caller can say so. `navigator.share` throws
 * AbortError when somebody closes the sheet, which is a choice rather than a
 * failure and is reported as such.
 */
/**
 * Put the link on the clipboard. Nothing else.
 *
 * Deliberately not navigator.share: that hands the link to the operating
 * system's own share sheet, which on Windows is a full window that covers the
 * page. That was the right thing when a button labelled "Share" was the only
 * way out of a post, but the share dialog now lists the destinations itself -
 * so the sheet is a second share UI stacked on the first, and the member who
 * pressed "Copy link" asked for the clipboard, not for a chooser.
 */
export async function copyLink(url: string): Promise<'copied' | 'failed'> {
  try {
    await navigator.clipboard.writeText(url)
    return 'copied'
  } catch {
    return 'failed'
  }
}

export async function shareLink(
  url: string,
  title: string,
): Promise<'shared' | 'copied' | 'cancelled' | 'failed'> {
  if (navigator.share) {
    try {
      await navigator.share({ title, url })
      return 'shared'
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return 'cancelled'
      // Any other failure falls through to the clipboard rather than leaving
      // the member with nothing.
    }
  }
  try {
    await navigator.clipboard.writeText(url)
    return 'copied'
  } catch {
    return 'failed'
  }
}

/**
 * Where a post can be sent, and the address that sends it there.
 *
 * Each network takes the link as a query parameter on a share endpoint, so
 * there is no SDK and no script to load - which is the point. Embedding a
 * share button from each of these means embedding their tracking into every
 * page that shows a post, and a URL does the same job with nobody watching.
 *
 * WhatsApp first because of where most of this audience is. Email and SMS last
 * because they are the fallbacks that always work.
 */
export type ShareTarget = {
  id: string
  label: string
  /** Build the address that opens this network's share screen. */
  href: (url: string, text: string) => string
  /** Brand colour, for the button's ring. */
  tint: string
}

export const SHARE_TARGETS: ShareTarget[] = [
  {
    id: 'whatsapp',
    label: 'WhatsApp',
    tint: '#25D366',
    href: (url, text) => `https://wa.me/?text=${encodeURIComponent(`${text} ${url}`)}`,
  },
  {
    id: 'facebook',
    label: 'Facebook',
    tint: '#1877F2',
    href: (url) => `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(url)}`,
  },
  {
    id: 'x',
    label: 'X',
    // Black, not the near-white of their dark site: the tint is drawn on the
    // dialog, which is light, and #E7E9EA there is a blank circle.
    tint: '#0F1419',
    href: (url, text) =>
      `https://twitter.com/intent/tweet?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`,
  },
  {
    id: 'telegram',
    label: 'Telegram',
    tint: '#2AABEE',
    href: (url, text) =>
      `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`,
  },
  {
    id: 'linkedin',
    label: 'LinkedIn',
    tint: '#0A66C2',
    href: (url) => `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(url)}`,
  },
  {
    id: 'reddit',
    label: 'Reddit',
    tint: '#FF4500',
    href: (url, text) =>
      `https://reddit.com/submit?url=${encodeURIComponent(url)}&title=${encodeURIComponent(text)}`,
  },
  {
    id: 'email',
    label: 'Email',
    tint: '#A7ACBF',
    href: (url, text) =>
      `mailto:?subject=${encodeURIComponent(text)}&body=${encodeURIComponent(`${text}\n\n${url}`)}`,
  },
  {
    id: 'sms',
    label: 'SMS',
    tint: '#8FB8E8',
    href: (url, text) => `sms:?&body=${encodeURIComponent(`${text} ${url}`)}`,
  },
]
