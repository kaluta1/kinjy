import { useEffect, useRef, useState } from 'react'
import {
  Bold,
  Code,
  Heading2,
  Heading3,
  Italic,
  Link2,
  List,
  ListOrdered,
  Quote,
  Strikethrough,
  ImagePlus,
  Clapperboard,
  Youtube,
  Trash2,
  ClipboardType,
  AlignLeft,
  AlignCenter,
  AlignRight,
  StretchHorizontal,
  Captions,
  ScanText,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { embedFromUrl, hydrateArticle, hydrateFigure, sanitizeHtml, type InlineMedia } from '@/lib/richtext'
import { convertPastedHtml, plainTextToHtml, type PendingPicture } from '@/lib/articlePaste'
import { ApiError, kaluta } from '@/lib/api'
import FloatingPanel from './FloatingPanel'
import { cn } from '@/lib/utils'

interface Tool {
  id: string
  icon: LucideIcon
  label: string
  command: string
  value?: string
}

const TOOLS: Array<Tool | 'divider'> = [
  { id: 'bold', icon: Bold, label: 'Bold', command: 'bold' },
  { id: 'italic', icon: Italic, label: 'Italic', command: 'italic' },
  { id: 'strike', icon: Strikethrough, label: 'Strikethrough', command: 'strikeThrough' },
  'divider',
  { id: 'h2', icon: Heading2, label: 'Heading', command: 'formatBlock', value: 'h2' },
  { id: 'h3', icon: Heading3, label: 'Subheading', command: 'formatBlock', value: 'h3' },
  { id: 'quote', icon: Quote, label: 'Quote', command: 'formatBlock', value: 'blockquote' },
  { id: 'code', icon: Code, label: 'Code', command: 'formatBlock', value: 'pre' },
  'divider',
  { id: 'ul', icon: List, label: 'Bullet list', command: 'insertUnorderedList' },
  { id: 'ol', icon: ListOrdered, label: 'Numbered list', command: 'insertOrderedList' },
]

/** The server's limit on pictures and clips placed in one article. */
const MAX_ARTICLE_MEDIA = 30
/** Pictures brought in at the same time from one paste. */
const PARALLEL_IMPORTS = 3

type Pending = PendingPicture & { status: 'waiting' | 'working' | 'failed'; error?: string }
type Brought = { id: string; url: string; kind: string }

/**
 * A small WYSIWYG for article bodies.
 *
 * Built on contentEditable and `document.execCommand`. That API is formally
 * deprecated, but every browser still implements it and the alternative — a
 * full editor engine — is a dependency this project does not need for bold,
 * lists and links. Whatever it produces is passed through the whitelist
 * sanitiser before it leaves this component, so a paste from Word or a crafted
 * clipboard payload cannot smuggle markup through.
 */
export default function RichTextEditor({
  value,
  onChange,
  placeholder = 'Write your article…',
  minHeight = 220,
  onUploadMedia,
  mediaFor,
  onUploadingChange,
  onAdoptMedia,
}: {
  value: string
  onChange: (html: string) => void
  placeholder?: string
  minHeight?: number
  /**
   * Upload a picture or a clip to place in the text. Without it the editor
   * has no media buttons and pasted files are ignored.
   */
  onUploadMedia?: (file: File) => Promise<{ id: string; url: string; kind: string }>
  /** What a figure already in `value` shows while it is edited. */
  mediaFor?: (id: string) => InlineMedia | null
  /**
   * Told when an upload starts and ends, so the form around can hold its
   * Save or Post until the file is in: saved mid-upload, the picture was
   * lost - its placeholder has no id and is not part of what is saved.
   */
  onUploadingChange?: (uploading: boolean) => void
  /**
   * Told when an article copied from Kinjy is pasted in and brings the
   * writer's own pictures with it. They are files that already exist, so
   * nothing is uploaded - but the form around has to attach them to the post
   * when it is saved, exactly as it does the ones it uploaded itself, or the
   * text would point at pictures the post does not carry and readers would
   * see nothing where they were.
   */
  onAdoptMedia?: (items: Array<{ id: string; kind: string; url: string }>) => void
}) {
  const { tok } = useAppTheme()
  const editorRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [active, setActive] = useState<Set<string>>(new Set())
  const [empty, setEmpty] = useState(!value)
  const [uploading, setUploading] = useState(false)
  const [mediaError, setMediaError] = useState<string | null>(null)
  // Where the caret was last seen inside the text. A toolbar click or a file
  // dialog takes the focus away; the media goes where the writer was, not to
  // the end of the article.
  const savedRange = useRef<Range | null>(null)
  // Uploaded in this session, so a figure keeps its preview when the text is
  // seeded again or re-rendered.
  const uploaded = useRef(new Map<string, InlineMedia>())
  // The picture the writer clicked, and whether its menu is open. A figure is
  // not text - the caret cannot go into it - so without this the only way to
  // delete one was to put the caret just after it and press Backspace, which
  // nobody discovers.
  const selected = useRef<HTMLElement | null>(null)
  const [figureMenu, setFigureMenu] = useState(false)

  // Pictures from a pasted article that are still being brought in, by the
  // ref on their placeholder. The placeholder is in the text, where the
  // picture goes; this is what the editor knows about each one.
  const pending = useRef(new Map<string, Pending>())
  // One upload per source, however many times a paste shows it.
  const bySource = useRef(new Map<string, Promise<Brought>>())
  const refCounter = useRef(0)
  // Placeholders still in the text (waiting, working or failed). While there
  // are any, the form around holds Post and Save: the article is not whole.
  const [unresolved, setUnresolved] = useState(0)
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const replaceTarget = useRef<HTMLElement | null>(null)
  // Ctrl+Shift+V was pressed: the paste that follows is words only.
  const plainNext = useRef(false)
  useEffect(
    () => onUploadingChange?.(uploading || unresolved > 0),
    [uploading, unresolved, onUploadingChange],
  )

  // Seed the DOM once. Re-writing innerHTML on every keystroke would move the
  // caret to the start on each character.
  useEffect(() => {
    if (editorRef.current && !editorRef.current.innerHTML && value) {
      editorRef.current.innerHTML = hydrateArticle(
        sanitizeHtml(value),
        (id) => uploaded.current.get(id) ?? mediaFor?.(id) ?? null,
      )
      setEmpty(false)
    }
  }, [value, mediaFor])

  const emit = () => {
    const el = editorRef.current
    const html = el?.innerHTML ?? ''
    setEmpty(!el?.textContent?.trim() && !el?.querySelector('figure'))
    // Undo, Remove or a deleted paragraph can take placeholders out; what is
    // left in the text is what still holds the form.
    setUnresolved(el?.querySelectorAll('figure[data-pending-ref]').length ?? 0)
    onChange(sanitizeHtml(html))
  }

  const saveSelection = () => {
    const selection = window.getSelection()
    const el = editorRef.current
    if (selection && selection.rangeCount && el?.contains(selection.anchorNode)) {
      savedRange.current = selection.getRangeAt(0).cloneRange()
    }
  }

  /** A figure as the reader will see it, from its validated marker. */
  const buildFigure =(attrs: Record<string, string>, preview: InlineMedia | null): Element | null => {
    const marker = document.createElement('figure')
    for (const [key, val] of Object.entries(attrs)) marker.setAttribute(key, val)
    const holder = document.createElement('div')
    holder.innerHTML = hydrateArticle(sanitizeHtml(marker.outerHTML), () => preview)
    return holder.firstElementChild
  }

  const insertFigure = (attrs: Record<string, string>, preview: InlineMedia | null) => {
    const figure = buildFigure(attrs, preview)
    if (figure) placeAt(figure)
  }

  /**
   * Put a figure where the writer's caret was: after the paragraph it sits
   * in, with an empty paragraph under it so writing can carry on below.
   */
  const placeAt = (figure: Element) => {
    const el = editorRef.current
    if (!el) return
    const range = savedRange.current && el.contains(savedRange.current.startContainer) ? savedRange.current : null
    let block: Node | null = range ? range.startContainer : null
    while (block && block.parentNode !== el) block = block.parentNode
    const after = document.createElement('p')
    after.appendChild(document.createElement('br'))
    el.insertBefore(figure, block ? block.nextSibling : null)
    el.insertBefore(after, figure.nextSibling)

    const caret = document.createRange()
    caret.setStart(after, 0)
    caret.collapse(true)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(caret)
    savedRange.current = caret
    el.focus()
    emit()
  }

  const addFiles = async (files: File[]) => {
    if (!onUploadMedia) return
    const wanted = files.filter((f) => f.type.startsWith('image/') || f.type.startsWith('video/'))
    if (!wanted.length) return
    setUploading(true)
    setMediaError(null)
    // A placeholder for each file, put down now where the writer pasted or
    // dropped: the upload takes a moment and they may go on typing, and the
    // picture belongs where they put it, not wherever the caret is by then.
    // It has no media id, so the sanitiser drops it from what is saved.
    const holders = wanted.map((file) => {
      const holder = document.createElement('figure')
      holder.className = 'article-figure'
      holder.setAttribute('contenteditable', 'false')
      const note = document.createElement('p')
      note.className = 'article-figure-note'
      note.textContent = `Uploading ${file.type.startsWith('video/') ? 'video' : 'picture'}…`
      holder.appendChild(note)
      placeAt(holder)
      return holder
    })
    let done = 0
    try {
      for (const [at, file] of wanted.entries()) {
        const asset = await onUploadMedia(file)
        const kind = asset.kind === 'video' || file.type.startsWith('video/') ? 'video' : 'image'
        const preview = { url: asset.url, kind, alt_text: null }
        uploaded.current.set(asset.id, preview)
        const figure = buildFigure({ 'data-media-id': asset.id, 'data-kind': kind }, preview)
        // Removed by the writer while it uploaded: they no longer want it there.
        if (figure && holders[at].isConnected) holders[at].replaceWith(figure)
        done = at + 1
        emit()
      }
    } catch (err) {
      for (const holder of holders.slice(done)) holder.remove()
      emit()
      setMediaError(err instanceof Error ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const pickFile = (accept: string) => {
    saveSelection()
    if (!fileRef.current) return
    fileRef.current.accept = accept
    fileRef.current.click()
  }

  /**
   * Asking for an address, inside the editor rather than in window.prompt.
   *
   * The prompt took the focus out of the page, and with it the selection: the
   * words a writer had highlighted to turn into a link were gone by the time
   * the address came back, so the link landed at the start of the article.
   * The selection is saved before asking and put back before acting.
   */
  const [asking, setAsking] = useState<null | 'link' | 'embed' | 'caption' | 'alt'>(null)
  const [askValue, setAskValue] = useState('')

  const ask = (what: 'link' | 'embed') => {
    saveSelection()
    setAskValue('')
    setMediaError(null)
    setAsking(what)
  }

  const restoreSelection = (): Range | null => {
    const el = editorRef.current
    const range = savedRange.current
    if (!el || !range || !el.contains(range.startContainer)) return null
    el.focus()
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    return range
  }

  /** Ask for a caption or a description of the chosen picture, prefilled. */
  const askAbout = (what: 'caption' | 'alt') => {
    const figure = selected.current
    if (!figure) return
    setFigureMenu(false)
    setAskValue(figure.getAttribute(what === 'caption' ? 'data-caption' : 'data-alt') ?? '')
    setMediaError(null)
    setAsking(what)
  }

  /** Change what a figure says about itself, and draw it again. */
  const setFigureDetail = (figure: HTMLElement, key: 'data-caption' | 'data-alt' | 'data-align', value: string) => {
    if (value) figure.setAttribute(key, value)
    else figure.removeAttribute(key)
    const id = figure.getAttribute('data-media-id') ?? ''
    if (key === 'data-caption') {
      figure.querySelector('figcaption')?.remove()
      if (value) {
        const caption = document.createElement('figcaption')
        caption.textContent = value
        figure.appendChild(caption)
      }
    }
    if (key === 'data-alt') figure.querySelector('img')?.setAttribute('alt', value || uploaded.current.get(id)?.alt_text || '')
    emit()
  }

  const submitAsk = () => {
    const raw = askValue.trim()
    if (asking === 'caption' || asking === 'alt') {
      const figure = selected.current
      setAsking(null)
      if (figure) setFigureDetail(figure, asking === 'caption' ? 'data-caption' : 'data-alt', raw.slice(0, 300))
      return
    }
    if (!raw) return
    if (asking === 'embed') {
      const found = embedFromUrl(raw)
      if (!found) {
        setMediaError('That address is not a YouTube or Vimeo video.')
        return
      }
      setAsking(null)
      insertFigure({ 'data-embed': found.provider, 'data-id': found.id }, null)
      return
    }
    // A bare "kinjy.com/tree" is meant as a web address, not a relative path.
    const href = /^(https?:|mailto:)/i.test(raw) ? raw : `https://${raw}`
    setAsking(null)
    const range = restoreSelection()
    if (range && !range.collapsed) {
      // Words were highlighted: they become the link.
      document.execCommand('createLink', false, href)
    } else {
      // Nothing highlighted: the address itself goes in, where the caret was.
      const anchor = document.createElement('a')
      anchor.href = href
      anchor.textContent = raw
      if (range) {
        range.insertNode(anchor)
        range.setStartAfter(anchor)
        range.collapse(true)
      } else {
        editorRef.current?.appendChild(anchor)
      }
    }
    emit()
  }

  const refreshActive = () => {
    const next = new Set<string>()
    for (const tool of TOOLS) {
      if (tool === 'divider') continue
      try {
        if (tool.value) {
          if (document.queryCommandValue('formatBlock').toLowerCase() === tool.value) next.add(tool.id)
        } else if (document.queryCommandState(tool.command)) {
          next.add(tool.id)
        }
      } catch {
        /* queryCommandState throws on some blocks; the toolbar just shows nothing */
      }
    }
    setActive(next)
  }

  const run = (tool: Tool) => {
    editorRef.current?.focus()
    document.execCommand(tool.command, false, tool.value)
    refreshActive()
    emit()
  }

  /** Mark one figure as chosen, or none. */
  const selectFigure = (figure: HTMLElement | null) => {
    if (selected.current && selected.current !== figure) {
      selected.current.removeAttribute('data-selected')
      selected.current.removeAttribute('id')
    }
    selected.current = figure
    if (figure) {
      figure.setAttribute('data-selected', '')
      // An id so the menu can stay over the picture while the page scrolls.
      // Only in the live editor: what is saved is rebuilt from clean markers.
      figure.id = 'rte-selected-figure'
    } else {
      setFigureMenu(false)
    }
  }

  /** Take the chosen picture out of the article. */
  const deleteSelected = () => {
    const figure = selected.current
    if (!figure) return
    // Leave the caret where the picture was, so writing can carry on there.
    const next = figure.nextElementSibling
    selectFigure(null)
    figure.remove()
    if (next && editorRef.current?.contains(next)) {
      const caret = document.createRange()
      caret.setStart(next, 0)
      caret.collapse(true)
      const selection = window.getSelection()
      selection?.removeAllRanges()
      selection?.addRange(caret)
      savedRange.current = caret
    }
    editorRef.current?.focus()
    emit()
  }

  /** The figure under a click or a right-click, if it is one of ours. */
  const figureAt = (target: EventTarget | null): HTMLElement | null => {
    const node = target instanceof Element ? target.closest('figure') : null
    return node instanceof HTMLElement && editorRef.current?.contains(node) ? node : null
  }

  const addLink = () => ask('link')
  const embedVideo = () => ask('embed')

  /**
   * Paste as plain text: keeping source formatting is how junk markup arrives.
   * A pasted picture (a screenshot, an image copied from a page) is uploaded
   * and placed where the caret is; a pasted YouTube or Vimeo address on its
   * own becomes the video.
   */
  /**
   * Paste an article copied from Kinjy, pictures included.
   *
   * What travels on the clipboard is the article's HTML, and its pictures are
   * markers naming files - not the files. A post may only carry its author's
   * own post media, so each named file is asked about first: the writer's own
   * come along as they are, with nothing uploaded twice, and anybody else's
   * are left out rather than turning Save into an error. Videos from YouTube
   * and Vimeo are a provider and an id, and come along regardless.
   *
   * The preview is the address the picture had where it was copied from,
   * which is one the writer was looking at a moment ago. What is saved is the
   * marker; the reader's address is signed for them when they open the post.
   */
  const pasteArticle = async (html: string) => {
    const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html')
    const previews = new Map<string, InlineMedia>()
    for (const figure of Array.from(doc.body.querySelectorAll('figure[data-media-id]'))) {
      const id = figure.getAttribute('data-media-id') ?? ''
      const media = figure.querySelector('img, video')
      // The reading view mounts its own video player into a slot and keeps
      // the address on it rather than on a <video>.
      const src =
        media?.getAttribute('src') || figure.querySelector('[data-src]')?.getAttribute('data-src') || ''
      const kind = figure.getAttribute('data-kind') || (media?.tagName === 'VIDEO' ? 'video' : 'image')
      previews.set(id, { url: src, kind, alt_text: media?.getAttribute('alt') ?? null })
    }

    let mine = new Set<string>()
    if (previews.size) {
      try {
        const answer = await kaluta.media.attachable([...previews.keys()])
        mine = new Set(answer.attachable.map((item) => item.id))
      } catch {
        // Not confirmed is not allowed: leaving a picture out costs the
        // writer a re-upload, keeping one that is not theirs costs them the
        // whole post at Save.
        mine = new Set()
      }
    }

    let left = 0
    for (const figure of Array.from(doc.body.querySelectorAll('figure[data-media-id]'))) {
      if (!mine.has(figure.getAttribute('data-media-id') ?? '')) {
        figure.remove()
        left += 1
      }
    }
    for (const id of mine) {
      const preview = previews.get(id)
      if (preview) uploaded.current.set(id, preview)
    }

    const clean = hydrateArticle(
      sanitizeHtml(doc.body.innerHTML),
      (id) => uploaded.current.get(id) ?? mediaFor?.(id) ?? null,
    )
    const el = editorRef.current
    if (!el) return
    el.focus()
    // Back where the writer pasted: asking the server took a moment, and the
    // article belongs there, not wherever the caret has drifted to since.
    const selection = window.getSelection()
    if (savedRange.current && el.contains(savedRange.current.startContainer)) {
      selection?.removeAllRanges()
      selection?.addRange(savedRange.current)
    }
    document.execCommand('insertHTML', false, clean)
    emit()

    if (mine.size) {
      onAdoptMedia?.(
        [...mine].map((id) => ({
          id,
          kind: previews.get(id)?.kind ?? 'image',
          url: previews.get(id)?.url ?? '',
        })),
      )
    }
    setMediaError(
      left
        ? `${left} ${left === 1 ? 'picture belongs' : 'pictures belong'} to somebody else and ${left === 1 ? 'was' : 'were'} left out.`
        : null,
    )
  }

  /** A placeholder, drawn for what is known about its picture now. */
  const drawPending = (figure: HTMLElement, item: Pending) => {
    figure.className = 'article-figure article-pending'
    figure.setAttribute('contenteditable', 'false')
    figure.setAttribute('data-status', item.status)
    const note = document.createElement('p')
    note.className = 'article-figure-note'
    if (item.status === 'failed') {
      note.textContent = item.error ?? 'This picture could not be brought in.'
      const actions = document.createElement('span')
      actions.className = 'article-pending-actions'
      for (const [action, label] of [
        ['retry', 'Retry'],
        ['replace', 'Upload a replacement'],
        ['remove', 'Remove'],
      ] as const) {
        const button = document.createElement('button')
        button.type = 'button'
        button.setAttribute('data-rte-action', action)
        button.textContent = label
        actions.appendChild(button)
      }
      figure.replaceChildren(note, actions)
    } else {
      note.textContent = item.status === 'working' ? 'Bringing the picture in…' : 'Waiting to bring the picture in…'
      figure.replaceChildren(note)
    }
  }

  const placeholderFor = (ref: string): HTMLElement | null =>
    editorRef.current?.querySelector<HTMLElement>(`figure[data-pending-ref="${ref}"]`) ?? null

  /** Turn a placeholder into its picture, in place - so Undo still finds it. */
  const fillPending = (figure: HTMLElement, brought: Brought) => {
    const kind = brought.kind === 'video' ? 'video' : 'image'
    const preview: InlineMedia = { url: brought.url, kind, alt_text: figure.getAttribute('data-alt') }
    uploaded.current.set(brought.id, preview)
    for (const name of figure.getAttributeNames()) {
      if (!['data-alt', 'data-caption', 'data-align'].includes(name)) figure.removeAttribute(name)
    }
    figure.setAttribute('data-media-id', brought.id)
    figure.setAttribute('data-kind', kind)
    figure.replaceChildren()
    hydrateFigure(figure, preview)
  }

  /** Where a picture comes from, made into a file of ours. */
  const fetchPicture = async (item: Pending): Promise<Brought> => {
    if (!onUploadMedia) throw new Error('Pictures cannot be added here.')
    if (item.file) return onUploadMedia(item.file)
    const src = item.src
    if (src.startsWith('data:') || src.startsWith('blob:')) {
      let blob: Blob
      try {
        blob = await (await fetch(src)).blob()
      } catch {
        throw new Error('This picture only existed in the page it was copied from. Upload it instead.')
      }
      if (!blob.type.startsWith('image/')) throw new Error('That is not a picture.')
      const extension = blob.type.split('/')[1]?.replace('jpeg', 'jpg') ?? 'png'
      return onUploadMedia(new File([blob], `pasted.${extension}`, { type: blob.type }))
    }
    if (src.startsWith('file:')) {
      throw new Error('This picture is a file on your computer that was not copied with the text. Upload it instead.')
    }
    try {
      const asset = await kaluta.media.importUrl(src, item.alt)
      const brought = { id: asset.id, url: asset.preview_url || asset.url, kind: asset.kind || 'image' }
      // A file of the writer's now, attached on Post like one uploaded here.
      onAdoptMedia?.([brought])
      return brought
    } catch (err) {
      throw new Error(err instanceof ApiError ? err.message : 'The picture could not be copied from its site.')
    }
  }

  /** Bring one picture in, sharing the work with any other copy of it. */
  const bringIn = async (item: Pending) => {
    const figure = placeholderFor(item.ref)
    if (!figure) return
    item.status = 'working'
    item.error = undefined
    drawPending(figure, item)
    const key = item.file ? '' : item.src
    let job = key ? bySource.current.get(key) : undefined
    if (!job) {
      job = fetchPicture(item)
      if (key) {
        bySource.current.set(key, job)
        // A failure is not remembered: Retry tries again.
        job.catch(() => bySource.current.delete(key))
      }
    }
    try {
      const brought = await job
      // Taken out (Remove, Undo, a deleted paragraph) while it came in.
      const now = placeholderFor(item.ref)
      if (now) fillPending(now, brought)
      pending.current.delete(item.ref)
    } catch (err) {
      item.status = 'failed'
      item.error = err instanceof Error ? err.message : 'This picture could not be brought in.'
      const now = placeholderFor(item.ref)
      if (now) drawPending(now, item)
    }
    emit()
  }

  /** Bring in a paste's pictures, a few at a time, counting as they land. */
  const bringAll = async (items: Pending[]) => {
    if (!items.length) return
    let done = 0
    setProgress({ done, total: items.length })
    const queue = [...items]
    const worker = async () => {
      for (let item = queue.shift(); item; item = queue.shift()) {
        await bringIn(item)
        done += 1
        setProgress({ done, total: items.length })
      }
    }
    await Promise.all(Array.from({ length: Math.min(PARALLEL_IMPORTS, items.length) }, worker))
    setProgress(null)
  }

  /** Put HTML where the writer was, and select nothing in particular. */
  const insertAtCaret = (html: string) => {
    const el = editorRef.current
    if (!el) return
    el.focus()
    const selection = window.getSelection()
    if (savedRange.current && el.contains(savedRange.current.startContainer)) {
      selection?.removeAllRanges()
      selection?.addRange(savedRange.current)
    }
    document.execCommand('insertHTML', false, html)
  }

  /**
   * An article copied from anywhere else: a page, a Google Doc, Word.
   *
   * Its structure is converted (lib/articlePaste) and put in as one step, so
   * one Undo takes the whole paste back out. Each picture arrives as a
   * placeholder in its place and is then brought in - uploaded when the
   * clipboard carried it, copied by the server when it is an address - and
   * the placeholder becomes the picture. One that cannot be brought in stays
   * as a placeholder saying why, with Retry, Upload a replacement and Remove;
   * until none are left, Post and Save wait.
   */
  const pasteRich = (html: string, files: File[]) => {
    const converted = convertPastedHtml(html, files, () => `p${Date.now().toString(36)}-${(refCounter.current += 1)}`)
    let body = converted.html
    let items: Pending[] = converted.pending.map((item) => ({ ...item, status: 'waiting' }))
    if (!onUploadMedia && items.length) {
      // No pictures in this editor: their places go, the words stay.
      body = body.replace(/<figure data-pending-ref="[^"]*"[^>]*><\/figure>/g, '')
      items = []
    }
    const clean = hydrateArticle(body, (id) => uploaded.current.get(id) ?? mediaFor?.(id) ?? null)
    insertAtCaret(clean)

    // The server takes so many pictures per article; the ones past that are
    // marked at once rather than refused at Post.
    const placed = editorRef.current?.querySelectorAll('figure[data-media-id]').length ?? 0
    let room = MAX_ARTICLE_MEDIA - placed
    const toBring: Pending[] = []
    for (const item of items) {
      pending.current.set(item.ref, item)
      const figure = placeholderFor(item.ref)
      if (!figure) continue
      if (room <= 0) {
        item.status = 'failed'
        item.error = `An article can carry at most ${MAX_ARTICLE_MEDIA} pictures and clips.`
      } else {
        room -= 1
        toBring.push(item)
      }
      drawPending(figure, item)
    }
    emit()
    setMediaError(null)
    void bringAll(toBring)
  }

  /** The words alone, as paragraphs - whatever else was copied with them. */
  const pastePlain = (text: string) => {
    if (!text) return
    insertAtCaret(plainTextToHtml(text))
    emit()
  }

  const pasteAsPlainText = async () => {
    saveSelection()
    try {
      pastePlain(await navigator.clipboard.readText())
    } catch {
      setMediaError('The browser did not allow reading the clipboard. Press Ctrl+Shift+V (⌘⇧V on a Mac) instead.')
    }
  }

  /** Retry, Upload a replacement, or Remove, on a placeholder that failed. */
  const pendingAction = (button: Element) => {
    const figure = button.closest<HTMLElement>('figure[data-pending-ref]')
    const ref = figure?.getAttribute('data-pending-ref') ?? ''
    const item = pending.current.get(ref)
    if (!figure) return
    const action = button.getAttribute('data-rte-action')
    if (action === 'remove' || !item) {
      pending.current.delete(ref)
      figure.remove()
      emit()
      return
    }
    if (action === 'retry') {
      void bringIn(item)
    } else if (action === 'replace') {
      replaceTarget.current = figure
      pickFile('image/*')
    }
  }

  /** The file chosen to stand in for a picture that could not be brought in. */
  const replaceWith = (file: File) => {
    const figure = replaceTarget.current
    replaceTarget.current = null
    const ref = figure?.getAttribute('data-pending-ref') ?? ''
    const item = pending.current.get(ref)
    if (!figure || !item || !figure.isConnected) return
    item.file = file
    item.src = ''
    void bringIn(item)
  }

  const onPaste = (event: React.ClipboardEvent) => {
    event.preventDefault()
    saveSelection()
    const text = event.clipboardData.getData('text/plain')
    if (plainNext.current) {
      plainNext.current = false
      pastePlain(text)
      return
    }
    // An article copied from Kinjy: its pictures are markers naming files
    // that already exist, and the writer's own come along as they are.
    const html = event.clipboardData.getData('text/html')
    if (html && /data-media-id=|data-embed=/.test(html)) {
      void pasteArticle(html)
      return
    }
    const files = Array.from(event.clipboardData.files)
    // A YouTube or Vimeo address on its own becomes the video.
    const embed = onUploadMedia && !/\s/.test(text.trim()) ? embedFromUrl(text) : null
    if (embed) {
      insertFigure({ 'data-embed': embed.provider, 'data-id': embed.id }, null)
      return
    }
    // Anything with formatting - a page, a document - keeps its formatting,
    // its links and its pictures, in their order.
    if (html && /<[a-z]/i.test(html)) {
      pasteRich(html, files)
      return
    }
    if (files.length && onUploadMedia) {
      void addFiles(files)
      return
    }
    document.execCommand('insertText', false, text)
    emit()
  }

  const onDrop = (event: React.DragEvent) => {
    const files = Array.from(event.dataTransfer.files)
    if (!files.length || !onUploadMedia) return
    event.preventDefault()
    // Where it was dropped, not where the caret happened to be.
    const at = document.caretRangeFromPoint?.(event.clientX, event.clientY)
    if (at && editorRef.current?.contains(at.startContainer)) savedRange.current = at
    void addFiles(files)
  }

  const toolButton = cn('flex h-7 w-7 items-center justify-center rounded-card-sm')

  return (
    <div className={cn('overflow-hidden rounded-card-sm border', tok.input)}>
      <div className={cn('flex flex-wrap items-center gap-0.5 border-b p-1', tok.divider, 'border-b-current/10')}>
        {TOOLS.map((tool, index) =>
          tool === 'divider' ? (
            <span key={`d${index}`} className="mx-1 h-4 w-px bg-current/15" aria-hidden="true" />
          ) : (
            <button
              key={tool.id}
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => run(tool)}
              aria-label={tool.label}
              aria-pressed={active.has(tool.id)}
              title={tool.label}
              className={cn(toolButton, active.has(tool.id) ? 'bg-gold/20 text-gold-soft' : cn(tok.mid, tok.hoverBg))}
            >
              <tool.icon size={14} />
            </button>
          ),
        )}
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={addLink}
          aria-label="Add link"
          title="Add link"
          className={cn(toolButton, tok.mid, tok.hoverBg)}
        >
          <Link2 size={14} />
        </button>
        {onUploadMedia && (
          <>
            <span className="mx-1 h-4 w-px bg-current/15" aria-hidden="true" />
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pickFile('image/*')}
              disabled={uploading}
              aria-label="Insert a picture here"
              title="Insert a picture here (or paste one)"
              className={cn(toolButton, tok.mid, tok.hoverBg)}
            >
              <ImagePlus size={14} />
            </button>
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pickFile('video/*')}
              disabled={uploading}
              aria-label="Insert a video clip here"
              title="Insert a video clip here"
              className={cn(toolButton, tok.mid, tok.hoverBg)}
            >
              <Clapperboard size={14} />
            </button>
            <button
              type="button"
              onMouseDown={(e) => e.preventDefault()}
              onClick={embedVideo}
              aria-label="Embed a YouTube or Vimeo video here"
              title="Embed a YouTube or Vimeo video here"
              className={cn(toolButton, tok.mid, tok.hoverBg)}
            >
              <Youtube size={14} />
            </button>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                const chosen = Array.from(e.target.files ?? [])
                if (replaceTarget.current) {
                  if (chosen[0]) replaceWith(chosen[0])
                  replaceTarget.current = null
                  e.target.value = ''
                  return
                }
                void addFiles(chosen)
              }}
            />
            {uploading && <span className={cn('ms-2 text-xs', tok.low)}>Uploading…</span>}
          </>
        )}
        <span className="mx-1 h-4 w-px bg-current/15" aria-hidden="true" />
        <button
          type="button"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => void pasteAsPlainText()}
          aria-label="Paste as plain text"
          title="Paste as plain text (Ctrl+Shift+V)"
          className={cn(toolButton, tok.mid, tok.hoverBg)}
        >
          <ClipboardType size={14} />
        </button>
        {progress && (
          <span className={cn('ms-2 text-xs', tok.low)} role="status">
            Bringing in pictures: {progress.done} of {progress.total}
          </span>
        )}
      </div>
      {asking && (
        // Not a <form>: the editor sits inside the composer's form, and a form
        // inside a form is invalid and submits the wrong one.
        <div className="flex flex-wrap items-center gap-2 border-b border-current/10 px-3 py-2">
          <input
            autoFocus
            value={askValue}
            onChange={(e) => setAskValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                submitAsk()
              }
              if (e.key === 'Escape') {
                e.preventDefault()
                setAsking(null)
                restoreSelection()
              }
            }}
            placeholder={
              asking === 'link'
                ? 'https://…'
                : asking === 'caption'
                  ? 'Caption under the picture'
                  : asking === 'alt'
                    ? 'What the picture shows, for people who cannot see it'
                    : 'YouTube or Vimeo address'
            }
            aria-label={
              asking === 'link'
                ? 'Link address'
                : asking === 'caption'
                  ? 'Caption'
                  : asking === 'alt'
                    ? 'Picture description'
                    : 'Video address'
            }
            maxLength={asking === 'caption' || asking === 'alt' ? 300 : undefined}
            className={cn('min-w-0 flex-1 rounded-full border border-current/15 bg-transparent px-3 py-1.5 text-sm focus:outline-none', tok.text)}
          />
          <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={submitAsk}
            disabled={!askValue.trim() && asking !== 'caption' && asking !== 'alt'}
            className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-3.5 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
          >
            {asking === 'link' ? 'Add link' : asking === 'embed' ? 'Embed' : 'Save'}
          </button>
          <button
            type="button"
            onClick={() => {
              setAsking(null)
              restoreSelection()
            }}
            className={cn('text-xs', tok.low)}
          >
            Cancel
          </button>
        </div>
      )}
      {mediaError && (
        <p role="alert" className="border-b border-current/10 px-3 py-1.5 text-xs text-red-300">
          {mediaError}
        </p>
      )}
      {unresolved > 0 && !progress && (
        <p role="status" className="border-b border-current/10 px-3 py-1.5 text-xs text-amber-200">
          {unresolved === 1 ? 'One picture is' : `${unresolved} pictures are`} not in yet. Retry, upload a replacement or
          remove {unresolved === 1 ? 'it' : 'them'} before publishing.
        </p>
      )}

      <div className="relative">
        {empty && (
          <p className={cn('pointer-events-none absolute inset-x-3 top-3 text-base', tok.low)} aria-hidden="true">
            {placeholder}
          </p>
        )}
        <div
          ref={editorRef}
          contentEditable
          suppressContentEditableWarning
          role="textbox"
          aria-multiline="true"
          aria-label="Article body"
          onInput={emit}
          onBlur={emit}
          onPaste={onPaste}
          onDrop={onDrop}
          // A click on a picture chooses it; a click anywhere else lets it go.
          onClick={(event) => {
            const action = event.target instanceof Element ? event.target.closest('[data-rte-action]') : null
            if (action && editorRef.current?.contains(action)) {
              event.preventDefault()
              pendingAction(action)
              return
            }
            selectFigure(figureAt(event.target))
          }}
          // A chosen picture goes with Delete or Backspace, as in any editor.
          // Escape lets it go without deleting it.
          onKeyDown={(event) => {
            // Paste and match style: the paste that follows is words only.
            if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'v') {
              plainNext.current = true
              window.setTimeout(() => (plainNext.current = false), 500)
            }
            if (!selected.current) return
            if (event.key === 'Delete' || event.key === 'Backspace') {
              event.preventDefault()
              deleteSelected()
            } else if (event.key === 'Escape') {
              event.preventDefault()
              selectFigure(null)
            } else if (event.key.length === 1 || event.key === 'Enter') {
              // Typing moves on from the picture rather than replacing it.
              selectFigure(null)
            }
          }}
          // Right-click on a picture opens its own menu; on text, the
          // browser's - which is where Copy and Paste live for the words.
          onContextMenu={(event) => {
            const figure = figureAt(event.target)
            if (!figure) return
            event.preventDefault()
            selectFigure(figure)
            setFigureMenu(true)
          }}
          onKeyUp={() => {
            refreshActive()
            saveSelection()
          }}
          onMouseUp={() => {
            refreshActive()
            saveSelection()
          }}
          style={{ minHeight }}
          className={cn(
            'prose-article w-full px-3 py-3 text-base focus:outline-none',
            tok.text,
          )}
        />
        {figureMenu && selected.current && (
          <FloatingPanel
            anchor="rte-selected-figure"
            label="Picture options"
            onClose={() => setFigureMenu(false)}
          >
            <button
              type="button"
              onClick={() => askAbout('caption')}
              className="flex w-full items-center gap-2.5 rounded-card-sm px-3 py-2 text-sm hover:bg-white/10"
            >
              <Captions size={14} aria-hidden="true" />
              Caption
            </button>
            {selected.current.getAttribute('data-kind') === 'image' && (
              <button
                type="button"
                onClick={() => askAbout('alt')}
                className="flex w-full items-center gap-2.5 rounded-card-sm px-3 py-2 text-sm hover:bg-white/10"
              >
                <ScanText size={14} aria-hidden="true" />
                Description
              </button>
            )}
            <div className="flex items-center gap-1 px-2 py-1" role="group" aria-label="Alignment">
              {(
                [
                  ['left', AlignLeft, 'To the left'],
                  ['center', AlignCenter, 'Centred'],
                  ['right', AlignRight, 'To the right'],
                  ['wide', StretchHorizontal, 'Full width'],
                ] as const
              ).map(([align, Icon, label]) => {
                const current = selected.current?.getAttribute('data-align') ?? ''
                return (
                  <button
                    key={align}
                    type="button"
                    aria-label={label}
                    title={label}
                    aria-pressed={current === align}
                    onClick={() => {
                      const figure = selected.current
                      if (figure) setFigureDetail(figure, 'data-align', current === align ? '' : align)
                      setFigureMenu(false)
                    }}
                    className={cn(
                      'flex h-8 w-8 items-center justify-center rounded-card-sm hover:bg-white/10',
                      current === align && 'bg-gold/20 text-gold-soft',
                    )}
                  >
                    <Icon size={14} aria-hidden="true" />
                  </button>
                )
              })}
            </div>
            <button
              type="button"
              onClick={() => {
                setFigureMenu(false)
                deleteSelected()
              }}
              className="flex w-full items-center gap-2.5 rounded-card-sm px-3 py-2 text-sm text-red-300 hover:bg-white/10"
            >
              <Trash2 size={14} aria-hidden="true" />
              Delete
            </button>
          </FloatingPanel>
        )}
      </div>
    </div>
  )
}
