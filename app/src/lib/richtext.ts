/**
 * Rich-text safety for article bodies.
 *
 * An article is authored as HTML in a contentEditable surface, stored, then
 * rendered back to other members. That round trip is an XSS vector unless the
 * markup is reduced to a known-safe subset — a `<img onerror>` or a `<script>`
 * pasted into the editor would otherwise execute in every reader's session.
 *
 * The approach is a whitelist rebuild rather than a blacklist strip: parse the
 * markup, walk it, and keep *only* the tags and attributes named here. Anything
 * unknown is dropped, so a tag nobody thought of is safe by default instead of
 * dangerous by default.
 *
 * Sanitising happens on render as well as on save, because the database can
 * contain anything — rows predating this code, or written by another client.
 */

const ALLOWED_TAGS = new Set([
  'P', 'BR', 'B', 'STRONG', 'I', 'EM', 'U', 'S', 'SUB', 'SUP',
  'H2', 'H3', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'A', 'CODE', 'PRE', 'HR',
  'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD',
])
/** Mapped onto what an article has: one title level and one subtitle level. */
const RENAMED: Record<string, string> = {
  H1: 'h2', H4: 'h3', H5: 'h3', H6: 'h3', STRIKE: 's', DEL: 's', INS: 'u',
}
/** Dropped with everything inside them - their text is not the author's words. */
const DROPPED = new Set([
  'SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT', 'IFRAME', 'OBJECT', 'EMBED', 'SVG', 'MATH', 'HEAD',
  'TITLE', 'META', 'LINK', 'TEXTAREA', 'SELECT', 'BUTTON', 'FORM', 'INPUT', 'CANVAS', 'IMG',
  'VIDEO', 'AUDIO', 'PICTURE', 'SOURCE',
])

/** Only href on links, and only to schemes that cannot execute anything. */
const SAFE_SCHEMES = ['http:', 'https:', 'mailto:']

function safeHref(value: string): string | null {
  try {
    // Resolve against the current origin so "/foo" and "page" stay valid.
    const url = new URL(value, window.location.origin)
    return SAFE_SCHEMES.includes(url.protocol) ? url.href : null
  } catch {
    return null
  }
}

/**
 * A bare address typed into a body, matched so it can be made clickable.
 *
 * Deliberately narrow: only http(s), and the address stops at whitespace or at
 * a character that cannot appear unescaped in one. Trailing sentence
 * punctuation is handled by the caller, since "see https://example.com." ends
 * with a full stop that belongs to the sentence and not to the address.
 */
const BARE_URL_RE = /https?:\/\/[^\s<>"']+/g

/**
 * Wrap bare addresses in a text node in real links.
 *
 * An author who types an address into an article gets text, not a link: the
 * editor only creates an <a> when somebody uses the link button. For a post
 * that also carries a photo there is no unfurled card either - that is
 * suppressed on purpose, so two pictures do not compete for the same glance -
 * and the result was an address the reader could see and could not follow.
 *
 * So the address is detected here instead. No preview, just a link.
 *
 * Never called for text inside an existing <a>: nested anchors are invalid and
 * would silently drop the inner one.
 */
function linkifyInto(text: string, out: Node, doc: Document): void {
  BARE_URL_RE.lastIndex = 0
  let last = 0
  let match: RegExpExecArray | null
  while ((match = BARE_URL_RE.exec(text)) !== null) {
    // Trailing punctuation belongs to the sentence, not to the address.
    const trailing = match[0].match(/[.,;:!?)\]]+$/)?.[0] ?? ''
    const raw = trailing ? match[0].slice(0, -trailing.length) : match[0]
    const href = safeHref(raw)
    if (!href) continue

    if (match.index > last) {
      out.appendChild(doc.createTextNode(text.slice(last, match.index)))
    }
    const anchor = doc.createElement('a')
    anchor.setAttribute('href', href)
    // Same hardening as an authored link: noreferrer so the destination does
    // not learn which Kinjy page the reader came from.
    anchor.setAttribute('rel', 'noopener noreferrer nofollow')
    anchor.setAttribute('target', '_blank')
    anchor.appendChild(doc.createTextNode(raw))
    out.appendChild(anchor)
    if (trailing) out.appendChild(doc.createTextNode(trailing))
    last = match.index + match[0].length
  }
  if (last < text.length) out.appendChild(doc.createTextNode(text.slice(last)))
}

/**
 * Pictures and videos inside an article.
 *
 * Stored as an empty marker - `<figure data-media-id data-kind>` - never as an
 * address. The file itself is one of the post's own attachments, so it went
 * through the ownership check, the age classifier (a post with a picture is
 * never released without a human) and the signed-link gate like any other.
 * The address a reader gets is signed for them at read time and put into the
 * marker here, on the way to the screen.
 *
 * A video from elsewhere is a provider and an id, rebuilt into a known embed
 * address on display. A raw `<iframe src>` from a paste is never kept.
 */
const MEDIA_ID_RE = /^mda_[A-Za-z0-9]{6,60}$/
const FIGURE_KINDS = new Set(['image', 'video'])
export const FIGURE_ALIGNS = ['left', 'center', 'right', 'wide'] as const
/** A picture still being brought in by the editor; never saved. */
const PENDING_RE = /^[a-z0-9-]{1,40}$/
const EMBED_ID: Record<string, RegExp> = {
  youtube: /^[A-Za-z0-9_-]{11}$/,
  vimeo: /^\d{6,12}$/,
}

/** The provider and id of a YouTube or Vimeo address, or null. */
export function embedFromUrl(raw: string): { provider: string; id: string } | null {
  let url: URL
  try {
    url = new URL(raw.trim())
  } catch {
    return null
  }
  const host = url.hostname.replace(/^www\.|^m\./, '')
  let provider = ''
  let id = ''
  if (host === 'youtu.be') {
    provider = 'youtube'
    id = url.pathname.slice(1)
  } else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
    provider = 'youtube'
    id = url.searchParams.get('v') ?? url.pathname.match(/^\/(?:embed|shorts|live)\/([^/?#]+)/)?.[1] ?? ''
  } else if (host === 'vimeo.com' || host === 'player.vimeo.com') {
    provider = 'vimeo'
    id = url.pathname.match(/(\d{6,12})/)?.[1] ?? ''
  }
  return provider && EMBED_ID[provider].test(id) ? { provider, id } : null
}

/** A caption or a description: one line, bounded - the server keeps 300. */
function shortText(value: string | null): string {
  return (value ?? '').replace(/\s+/g, ' ').trim().slice(0, 300)
}

/** What a figure says about itself besides what it shows. */
function copyFigureDetails(from: Element, to: Element): void {
  for (const key of ['data-caption', 'data-alt']) {
    const text = shortText(from.getAttribute(key))
    if (text) to.setAttribute(key, text)
  }
  const align = from.getAttribute('data-align') ?? ''
  if ((FIGURE_ALIGNS as readonly string[]).includes(align)) to.setAttribute('data-align', align)
}

/** A clean, empty marker for a figure, or null when it is not one we know. */
function cleanFigure(element: Element, doc: Document, allowPending = false): Element | null {
  const mediaId = element.getAttribute('data-media-id') ?? ''
  const kind = element.getAttribute('data-kind') ?? ''
  const provider = element.getAttribute('data-embed') ?? ''
  const id = element.getAttribute('data-id') ?? ''
  const pending = element.getAttribute('data-pending-ref') ?? ''
  const figure = doc.createElement('figure')
  if (MEDIA_ID_RE.test(mediaId) && FIGURE_KINDS.has(kind)) {
    figure.setAttribute('data-media-id', mediaId)
    figure.setAttribute('data-kind', kind)
  } else if (EMBED_ID[provider]?.test(id)) {
    figure.setAttribute('data-embed', provider)
    figure.setAttribute('data-id', id)
  } else if (allowPending && PENDING_RE.test(pending)) {
    figure.setAttribute('data-pending-ref', pending)
  } else {
    return null
  }
  copyFigureDetails(element, figure)
  return figure
}

/** The media ids an article places in its text, in order. */
export function articleMediaIds(html: string): string[] {
  return Array.from(html.matchAll(/data-media-id="(mda_[A-Za-z0-9]{6,60})"/g), (m) => m[1])
}

export interface InlineMedia {
  url: string | null
  kind: string
  alt_text?: string | null
  /** A video's cover. */
  poster_url?: string | null
}

/**
 * Fill an article's figures for display.
 *
 * Takes HTML that already went through sanitizeHtml, so every figure is an
 * empty, validated marker; what goes inside is built here from those
 * attributes and the post's own (signed) media. A marker whose media is not
 * on the post shows nothing - a body cannot name somebody else's file.
 */
export function hydrateArticle(
  html: string,
  media: (id: string) => InlineMedia | null,
  { mountVideos = false }: { mountVideos?: boolean } = {},
): string {
  if (!html.includes('<figure')) return html
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  for (const figure of Array.from(doc.body.querySelectorAll('figure'))) {
    // The editor fills these in itself, as each picture arrives.
    if (figure.hasAttribute('data-pending-ref')) continue
    figure.className = 'article-figure'
    figure.setAttribute('contenteditable', 'false')
    hydrateOne(figure, doc, media, mountVideos)
  }
  return doc.body.innerHTML
}

/** A caption under a figure, from its marker. */
function addCaption(figure: Element, doc: Document): void {
  const caption = figure.getAttribute('data-caption')
  if (!caption) return
  const node = doc.createElement('figcaption')
  node.textContent = caption
  figure.appendChild(node)
}

function hydrateOne(
  figure: Element,
  doc: Document,
  media: (id: string) => InlineMedia | null,
  mountVideos: boolean,
): void {
  const provider = figure.getAttribute('data-embed')
  const embedId = figure.getAttribute('data-id') ?? ''
  if (provider && EMBED_ID[provider]?.test(embedId)) {
    const frame = doc.createElement('iframe')
    frame.setAttribute(
      'src',
      provider === 'youtube'
        ? `https://www.youtube-nocookie.com/embed/${embedId}`
        : `https://player.vimeo.com/video/${embedId}`,
    )
    frame.setAttribute('title', provider === 'youtube' ? 'YouTube video' : 'Vimeo video')
    frame.setAttribute('loading', 'lazy')
    frame.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin')
    frame.setAttribute('allow', 'encrypted-media; picture-in-picture; fullscreen')
    frame.setAttribute('allowfullscreen', '')
    frame.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation allow-popups')
    figure.appendChild(frame)
    addCaption(figure, doc)
    return
  }
  const id = figure.getAttribute('data-media-id') ?? ''
  const item = MEDIA_ID_RE.test(id) ? media(id) : null
  if (!item) {
    figure.remove()
    return
  }
  if (!item.url) {
    const note = doc.createElement('p')
    note.className = 'article-figure-note'
    note.textContent =
      item.kind === 'video' ? 'Video held back to save data.' : 'Picture held back to save data.'
    figure.appendChild(note)
    addCaption(figure, doc)
    return
  }
  if (item.kind === 'video' && mountVideos) {
    // A slot for Kinjy's own player, which the post card mounts into it:
    // HTML cannot carry a React component, and the browser's controls are
    // not what a member should get.
    const slot = doc.createElement('div')
    slot.className = 'article-video'
    slot.setAttribute('data-src', item.url)
    if (item.poster_url) slot.setAttribute('data-poster', item.poster_url)
    figure.appendChild(slot)
  } else if (item.kind === 'video') {
    // In the editor: a still preview, no controls - it is being placed,
    // not watched.
    const video = doc.createElement('video')
    video.setAttribute('src', item.url)
    video.setAttribute('preload', 'metadata')
    video.setAttribute('muted', '')
    video.setAttribute('playsinline', '')
    if (item.poster_url) video.setAttribute('poster', item.poster_url)
    figure.appendChild(video)
  } else {
    const img = doc.createElement('img')
    img.setAttribute('src', item.url)
    // The description written for this article first, the file's own next.
    img.setAttribute('alt', figure.getAttribute('data-alt') || item.alt_text || '')
    img.setAttribute('loading', 'lazy')
    figure.appendChild(img)
  }
  addCaption(figure, doc)
}

/** Build one figure in place from its marker - the editor's way in. */
export function hydrateFigure(figure: Element, media: InlineMedia | null): void {
  figure.className = 'article-figure'
  figure.setAttribute('contenteditable', 'false')
  hydrateOne(figure, figure.ownerDocument, () => media, false)
}

function clean(node: Node, out: Node, doc: Document, inAnchor = false, allowPending = false): void {
  for (const child of Array.from(node.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      const text = child.textContent ?? ''
      if (inAnchor) out.appendChild(doc.createTextNode(text))
      else linkifyInto(text, out, doc)
      continue
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue

    const element = child as Element
    if (element.tagName === 'FIGURE') {
      // Its contents are a preview; only the validated marker is kept.
      const figure = inAnchor ? null : cleanFigure(element, doc, allowPending)
      if (figure) out.appendChild(figure)
      continue
    }
    if (DROPPED.has(element.tagName)) continue
    const tag = RENAMED[element.tagName] ?? element.tagName.toLowerCase()
    if (!ALLOWED_TAGS.has(tag.toUpperCase())) {
      // Keep the words, drop the wrapper: unwrapping a <div> or a <span> loses
      // styling but never loses the author's text.
      clean(element, out, doc, inAnchor, allowPending)
      continue
    }

    const copy = doc.createElement(tag)
    if (tag === 'a') {
      const href = safeHref(element.getAttribute('href') ?? '')
      if (!href || inAnchor) {
        // The wrapper goes but its text stays, and that text is now loose in
        // the document - so it is linkified like any other loose text.
        clean(element, out, doc, inAnchor, allowPending)
        continue
      }
      copy.setAttribute('href', href)
      copy.setAttribute('rel', 'noopener noreferrer nofollow')
      copy.setAttribute('target', '_blank')
    }
    if (tag === 'th' || tag === 'td') {
      for (const key of ['colspan', 'rowspan']) {
        const value = Number(element.getAttribute(key))
        if (Number.isInteger(value) && value > 1 && value <= 20) copy.setAttribute(key, String(value))
      }
    }
    clean(element, copy, doc, inAnchor || tag === 'a', allowPending)
    out.appendChild(copy)
  }
}

/** A run of empty paragraphs: one is spacing, five are a word processor's. */
function collapseEmptyRuns(container: Element): void {
  const isEmpty = (node: Element | null) =>
    !!node && node.tagName === 'P' && !node.textContent?.replace(/\u00a0/g, '').trim() && !node.querySelector('figure')
  for (const p of Array.from(container.querySelectorAll('p'))) {
    if (!p.isConnected || !isEmpty(p)) continue
    while (isEmpty(p.nextElementSibling)) p.nextElementSibling!.remove()
  }
}

export function sanitizeHtml(html: string, { allowPending = false }: { allowPending?: boolean } = {}): string {
  if (!html) return ''
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  const container = doc.createElement('div')
  clean(doc.body, container, doc, false, allowPending)
  collapseEmptyRuns(container)
  return container.innerHTML
}

/** Plain text for previews, search and the feed's non-article surfaces. */
export function htmlToText(html: string): string {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
  return (doc.body.textContent ?? '').replace(/\s+/g, ' ').trim()
}

/** Does this body actually contain markup, or is it plain text? */
export function looksLikeHtml(value: string): boolean {
  return /<(p|br|h[1-6]|ul|ol|li|blockquote|strong|b|em|i|u|s|a|code|pre|figure|table|hr)\b/i.test(value)
}

/**
 * A body that is not an article, made readable.
 *
 * Plain-text posts are not always plain: bodies arrive carrying `<br>`, `&gt;`
 * and the occasional `<p>` - from an importer, from a paste out of a rich
 * editor, from a client that escaped once too often. Rendered as text those
 * show literally, so a post reads "...Limited time!&gt;<br>Join as Founding
 * Member", which is what the member wrote mangled into what the member did
 * not.
 *
 * So: breaks become newlines, the rest of the tags go, and entities are
 * decoded once by the parser. Single newlines survive, unlike `htmlToText`,
 * which flattens everything to one line - that is right for feeding a
 * translator and wrong for showing somebody their own paragraphs.
 *
 * Decoding happens via textContent, so nothing here can reintroduce markup:
 * the output is text and is rendered as text.
 */
export function readableText(value: string): string {
  if (!/[<&]/.test(value)) return value
  const withBreaks = value
    .replace(/<\s*br\s*\/?\s*>/gi, '\n')
    .replace(/<\s*\/\s*(p|div|li|h[1-6]|blockquote)\s*>/gi, '\n')
    .replace(/<[^>]+>/g, '')
  const doc = new DOMParser().parseFromString(`<body>${withBreaks}</body>`, 'text/html')
  return (doc.body.textContent ?? '')
    // Three or more blank lines is somebody's stray markup, not their spacing.
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ \t]+\n/g, '\n')
    .trim()
}
