/**
 * An article pasted from elsewhere, turned into an article here.
 *
 * What a browser puts on the clipboard when somebody copies a page, a Google
 * Doc or a Word document is that source's own HTML: its headings and lists,
 * but also its styling carried on spans, its wrappers, and its pictures as
 * addresses on its own servers - or, from Word, as files on the writer's disk.
 *
 * This module does the translating, and only that:
 *
 * - formatting carried as style (Google Docs, Word) becomes the tags an
 *   article has: bold, italic, underline, strikethrough, sub and superscript;
 * - Word's numbered paragraphs become real, nested lists;
 * - every picture becomes a *pending* figure, keeping its place, its
 *   description, its caption and its alignment - the editor then brings each
 *   one in (upload, or a server-side copy) and fills the figure;
 * - a YouTube or Vimeo player, or a lone link to one, becomes an embed;
 * - any other video or sound becomes a link to it: a file elsewhere is not
 *   played inside an article.
 *
 * The result then goes through the same whitelist as everything else
 * (sanitizeHtml), so nothing here has to be trusted to be safe.
 */
import { embedFromUrl, sanitizeHtml } from './richtext'

export interface PendingPicture {
  /** Names the figure in the editor until the picture is in. */
  ref: string
  /** Where it comes from: http(s), data:, blob:, or file: (a disk). */
  src: string
  /** The picture itself, when the clipboard carried the file. */
  file?: File
  alt: string
  caption: string
  align: string
}

const BLOCKS_TO_SPLIT = new Set(['P', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6'])

function unwrap(element: Element): void {
  element.replaceWith(...Array.from(element.childNodes))
}

/** What a span's style says, in tags. */
function styleTags(element: Element): string[] {
  const style = (element.getAttribute('style') ?? '').toLowerCase().replace(/\s+/g, '')
  if (!style) return []
  const tags: string[] = []
  const weight = style.match(/font-weight:(bold|bolder|[6-9]00)/)
  if (weight) tags.push('b')
  if (/font-style:italic/.test(style)) tags.push('i')
  const decoration = style.match(/text-decoration(?:-line)?:([^;]+)/)?.[1] ?? ''
  // A link is underlined by its own style; that is not the author's emphasis.
  if (decoration.includes('underline') && !element.closest('a')) tags.push('u')
  if (decoration.includes('line-through')) tags.push('s')
  const vertical = style.match(/vertical-align:(super|sub)/)?.[1]
  if (vertical === 'super') tags.push('sup')
  if (vertical === 'sub') tags.push('sub')
  return tags
}

/** Google Docs and Word say "bold" with a style; an article says it with a tag. */
function styleToTags(doc: Document): void {
  // Google Docs wraps the whole paste in a <b> that is explicitly not bold.
  for (const element of Array.from(doc.body.querySelectorAll('b, strong'))) {
    const style = (element.getAttribute('style') ?? '').toLowerCase().replace(/\s+/g, '')
    if (element.id.startsWith('docs-internal-guid') || /font-weight:(normal|[1-4]00)/.test(style)) unwrap(element)
  }
  for (const span of Array.from(doc.body.querySelectorAll('span[style], font[style]'))) {
    const tags = styleTags(span)
    if (!tags.length) continue
    let outer: Element | null = null
    let inner: Element | null = null
    for (const tag of tags) {
      const node = doc.createElement(tag)
      if (inner) inner.appendChild(node)
      else outer = node
      inner = node
    }
    inner!.append(...Array.from(span.childNodes))
    span.replaceWith(outer!)
  }
}

/** Word's lists are paragraphs with a bullet drawn in; they become lists. */
function wordLists(doc: Document): void {
  const isItem = (node: Element | null): node is HTMLElement =>
    !!node &&
    node.tagName === 'P' &&
    (/mso-list:\s*l\d+\s+level\d+/i.test(node.getAttribute('style') ?? '') || /^MsoListParagraph/.test(node.className))
  for (const title of Array.from(doc.body.querySelectorAll('p.MsoTitle'))) {
    const heading = doc.createElement('h1')
    heading.append(...Array.from(title.childNodes))
    title.replaceWith(heading)
  }
  for (const subtitle of Array.from(doc.body.querySelectorAll('p.MsoSubtitle'))) {
    const heading = doc.createElement('h3')
    heading.append(...Array.from(subtitle.childNodes))
    subtitle.replaceWith(heading)
  }

  for (const first of Array.from(doc.body.querySelectorAll('p'))) {
    if (!first.isConnected || !isItem(first) || isItem(first.previousElementSibling)) continue
    const group: HTMLElement[] = []
    for (let node: Element | null = first; isItem(node); node = node.nextElementSibling) group.push(node)

    const stack: HTMLElement[] = []
    for (const paragraph of group) {
      const level = Number((paragraph.getAttribute('style') ?? '').match(/level(\d+)/i)?.[1] ?? 1)
      const bullet = paragraph.querySelector('[style*="mso-list:Ignore" i], [style*="mso-list: Ignore" i]')
      // "1.", "a)", "iv." number the items; a bullet character does not.
      const ordered = /^\s*(\d+|[a-z]|[ivxlcdm]+)[.)]/i.test(bullet?.textContent ?? '')
      bullet?.remove()
      while (stack.length > level) stack.pop()
      while (stack.length < level) {
        const list = doc.createElement(ordered ? 'ol' : 'ul')
        if (!stack.length) {
          paragraph.before(list)
        } else {
          const parent = stack[stack.length - 1]
          let last = parent.lastElementChild
          if (!last) {
            last = doc.createElement('li')
            parent.appendChild(last)
          }
          last.appendChild(list)
        }
        stack.push(list)
      }
      const item = doc.createElement('li')
      item.append(...Array.from(paragraph.childNodes))
      stack[stack.length - 1].appendChild(item)
      paragraph.remove()
    }
  }
}

/** The best address for a picture, past the lazy-loading placeholders. */
function pictureSource(img: HTMLImageElement): string {
  const lazy = img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original')
  const src = img.getAttribute('src') ?? ''
  const srcset = img.getAttribute('srcset') || img.getAttribute('data-srcset') || ''
  // The largest candidate in a srcset, if that is all there is.
  const fromSet = () => {
    let best = ''
    let width = -1
    for (const candidate of srcset.split(',')) {
      const [address, size] = candidate.trim().split(/\s+/)
      const value = parseFloat(size ?? '1') || 1
      if (address && value > width) {
        best = address
        width = value
      }
    }
    return best
  }
  // A few hundred bytes of data: is a lazy-loading placeholder, not the picture.
  const placeholder = !src || (src.startsWith('data:') && src.length < 400)
  const chosen = lazy || (placeholder ? fromSet() || src : src)
  try {
    return chosen.startsWith('data:') || chosen.startsWith('blob:') ? chosen : new URL(chosen, document.baseURI).href
  } catch {
    return ''
  }
}

/** Where the source put a picture: to one side, centred, or across. */
function pictureAlign(img: Element, holder: Element): string {
  const classes = `${holder.className} ${img.className} ${img.parentElement?.className ?? ''}`.toLowerCase()
  if (/\balign(wide|full)\b/.test(classes)) return 'wide'
  if (/\balignleft\b/.test(classes)) return 'left'
  if (/\balignright\b/.test(classes)) return 'right'
  if (/\baligncenter\b/.test(classes)) return 'center'
  const style = `${img.getAttribute('style') ?? ''};${holder.getAttribute('style') ?? ''}`.toLowerCase().replace(/\s+/g, '')
  if (/float:left/.test(style)) return 'left'
  if (/float:right/.test(style)) return 'right'
  const align = (img.getAttribute('align') ?? '').toLowerCase()
  if (align === 'left' || align === 'right') return align
  const parentStyle = (img.closest('p, div, figure')?.getAttribute('style') ?? '').toLowerCase().replace(/\s+/g, '')
  if (/text-align:center/.test(parentStyle) || /margin(-left)?:\s*auto/.test(style)) return 'center'
  return ''
}

/** A figure in a paragraph is invalid; the paragraph is split around it. */
function liftOutOfParagraph(figure: Element): void {
  let block: Element | null = figure.parentElement
  while (block && !BLOCKS_TO_SPLIT.has(block.tagName)) {
    if (block === figure.ownerDocument.body || block.tagName === 'LI' || block.tagName === 'TD' || block.tagName === 'TH')
      return
    block = block.parentElement
  }
  if (!block) return
  const range = figure.ownerDocument.createRange()
  range.setStartAfter(figure)
  range.setEnd(block, block.childNodes.length)
  const tail = range.extractContents()
  const rest = block.cloneNode(false) as Element
  rest.appendChild(tail)
  block.after(figure, rest)
  for (const node of [block, rest]) {
    if (!node.textContent?.trim() && !node.querySelector('figure, br')) node.remove()
  }
}

const BOXES = 'div, section, article, header, footer, main, aside, center, address, dd, dt'
const BLOCK_INSIDE = 'p, div, section, article, header, footer, main, aside, center, address, h1, h2, h3, h4, h5, h6, ul, ol, table, pre, blockquote, figure, hr, dl, dd, dt'

/**
 * A box holding only words is a paragraph. Pages and Notion write their
 * paragraphs as <div>s; unwrapped as unknown tags, their lines would run
 * into each other.
 */
function boxesToParagraphs(doc: Document): void {
  for (const box of Array.from(doc.body.querySelectorAll(BOXES)).reverse()) {
    if (box.querySelector(BLOCK_INSIDE) || !(box.textContent ?? '').trim()) continue
    const paragraph = doc.createElement('p')
    paragraph.append(...Array.from(box.childNodes))
    box.replaceWith(paragraph)
  }
}

/** A link standing alone on its line is the thing itself: a YouTube link plays. */
function loneEmbedLinks(doc: Document): void {
  for (const paragraph of Array.from(doc.body.querySelectorAll('p, div'))) {
    if (paragraph.querySelector('p, div, img, figure')) continue
    const text = (paragraph.textContent ?? '').trim()
    if (!text || /\s/.test(text)) continue
    const links = paragraph.querySelectorAll('a')
    const href = links.length === 1 ? links[0].getAttribute('href') ?? '' : links.length === 0 ? text : ''
    const embed = href ? embedFromUrl(href) : null
    if (!embed) continue
    const figure = doc.createElement('figure')
    figure.setAttribute('data-embed', embed.provider)
    figure.setAttribute('data-id', embed.id)
    paragraph.replaceWith(figure)
  }
}

/** A player from YouTube or Vimeo becomes an embed; any other clip, a link. */
function players(doc: Document): void {
  for (const frame of Array.from(doc.body.querySelectorAll('iframe'))) {
    const embed = embedFromUrl(frame.getAttribute('src') || frame.getAttribute('data-src') || '')
    const holder = frame.closest('figure') ?? frame
    if (!embed) {
      holder.remove()
      continue
    }
    const figure = doc.createElement('figure')
    figure.setAttribute('data-embed', embed.provider)
    figure.setAttribute('data-id', embed.id)
    const caption = holder.querySelector('figcaption')?.textContent?.trim()
    if (caption) figure.setAttribute('data-caption', caption)
    holder.replaceWith(figure)
  }
  for (const media of Array.from(doc.body.querySelectorAll('video, audio'))) {
    const holder = media.closest('figure') ?? media
    const raw = media.getAttribute('src') || media.querySelector('source[src]')?.getAttribute('src') || ''
    let href = ''
    try {
      const url = new URL(raw, document.baseURI)
      if (url.protocol === 'http:' || url.protocol === 'https:') href = url.href
    } catch {
      /* no usable address: the clip is left out */
    }
    if (!href) {
      holder.remove()
      continue
    }
    const embed = embedFromUrl(href)
    if (embed) {
      const figure = doc.createElement('figure')
      figure.setAttribute('data-embed', embed.provider)
      figure.setAttribute('data-id', embed.id)
      holder.replaceWith(figure)
      continue
    }
    const paragraph = doc.createElement('p')
    const link = doc.createElement('a')
    link.setAttribute('href', href)
    const name = decodeURIComponent(new URL(href).pathname.split('/').pop() || '') || href
    link.textContent = `${media.tagName === 'AUDIO' ? 'Audio' : 'Video'}: ${name}`
    paragraph.appendChild(link)
    holder.replaceWith(paragraph)
  }
}

/**
 * Convert what was pasted. Returns the article HTML, with a pending figure
 * where each picture goes, and the pictures still to bring in.
 */
export function convertPastedHtml(
  html: string,
  files: File[],
  nextRef: () => string,
): { html: string; pending: PendingPicture[] } {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  styleToTags(doc)
  wordLists(doc)
  players(doc)
  boxesToParagraphs(doc)

  const pending: PendingPicture[] = []
  // Pictures the clipboard carried as files go to the pictures the HTML
  // could only point at on a disk, in order.
  const spareFiles = files.filter((file) => file.type.startsWith('image/'))
  for (const img of Array.from(doc.body.querySelectorAll('img'))) {
    const width = Number(img.getAttribute('width'))
    const height = Number(img.getAttribute('height'))
    // A tracking pixel or a spacer, not a picture.
    if ((width && width <= 2) || (height && height <= 2)) {
      img.remove()
      continue
    }
    const src = pictureSource(img)
    let file: File | undefined
    if (!/^(https?:|data:image\/|blob:)/i.test(src)) {
      file = spareFiles.shift()
      if (!file && !src.startsWith('file:')) {
        img.remove()
        continue
      }
    }
    const outer = img.closest('figure')
    const anchor = img.closest('a')
    const picture = img.closest('picture')
    // What the picture replaces: its figure, the link around it when the link
    // is only the picture, or itself.
    let holder: Element = outer ?? picture ?? img
    if (!outer && anchor && !(anchor.textContent ?? '').trim() && anchor.querySelectorAll('img').length === 1) holder = anchor

    const figure = doc.createElement('figure')
    const ref = nextRef()
    figure.setAttribute('data-pending-ref', ref)
    const alt = (img.getAttribute('alt') ?? '').trim()
    const caption = (outer?.querySelector('figcaption')?.textContent ?? '').replace(/\s+/g, ' ').trim()
    const align = pictureAlign(img, holder)
    if (alt) figure.setAttribute('data-alt', alt)
    if (caption) figure.setAttribute('data-caption', caption)
    if (align) figure.setAttribute('data-align', align)

    if (holder !== anchor && anchor && holder !== outer) {
      // A picture inside a link that also has words: the words stay linked,
      // the picture comes right after (a figure cannot sit inside a link).
      img.remove()
      anchor.after(figure)
    } else {
      holder.replaceWith(figure)
    }
    pending.push({ ref, src: file ? '' : src, file, alt, caption, align })
  }
  // A paste from a file manager or a screenshot tool: files and no pictures.
  for (const file of pending.length ? [] : spareFiles) {
    const figure = doc.createElement('figure')
    const ref = nextRef()
    figure.setAttribute('data-pending-ref', ref)
    doc.body.appendChild(figure)
    pending.push({ ref, src: '', file, alt: '', caption: '', align: '' })
  }

  loneEmbedLinks(doc)
  for (const figure of Array.from(doc.body.querySelectorAll('figure'))) liftOutOfParagraph(figure)

  const clean = sanitizeHtml(doc.body.innerHTML, { allowPending: true })
  // Only the pictures whose place survived the whitelist are brought in.
  const kept = new Set(Array.from(clean.matchAll(/data-pending-ref="([^"]+)"/g), (m) => m[1]))
  return { html: clean, pending: pending.filter((item) => kept.has(item.ref)) }
}

/** Plain text, as paragraphs - for "Paste as plain text". */
export function plainTextToHtml(text: string): string {
  const escape = (value: string) =>
    value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return text
    .replace(/\r\n?/g, '\n')
    .split(/\n{2,}/)
    .map((block) => `<p>${escape(block).replace(/\n/g, '<br>')}</p>`)
    .join('')
}
