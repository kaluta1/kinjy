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
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useAppTheme } from '@/components/appdemo/theme'
import { embedFromUrl, hydrateArticle, sanitizeHtml, type InlineMedia } from '@/lib/richtext'
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
}) {
  const { tok } = useAppTheme()
  const editorRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [active, setActive] = useState<Set<string>>(new Set())
  const [empty, setEmpty] = useState(!value)
  const [uploading, setUploading] = useState(false)
  useEffect(() => onUploadingChange?.(uploading), [uploading, onUploadingChange])
  const [mediaError, setMediaError] = useState<string | null>(null)
  // Where the caret was last seen inside the text. A toolbar click or a file
  // dialog takes the focus away; the media goes where the writer was, not to
  // the end of the article.
  const savedRange = useRef<Range | null>(null)
  // Uploaded in this session, so a figure keeps its preview when the text is
  // seeded again or re-rendered.
  const uploaded = useRef(new Map<string, InlineMedia>())

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
  const [asking, setAsking] = useState<null | 'link' | 'embed'>(null)
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

  const submitAsk = () => {
    const raw = askValue.trim()
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

  const addLink = () => ask('link')
  const embedVideo = () => ask('embed')

  /**
   * Paste as plain text: keeping source formatting is how junk markup arrives.
   * A pasted picture (a screenshot, an image copied from a page) is uploaded
   * and placed where the caret is; a pasted YouTube or Vimeo address on its
   * own becomes the video.
   */
  const onPaste = (event: React.ClipboardEvent) => {
    event.preventDefault()
    const files = Array.from(event.clipboardData.files)
    if (files.length && onUploadMedia) {
      saveSelection()
      void addFiles(files)
      return
    }
    const text = event.clipboardData.getData('text/plain')
    const embed = onUploadMedia && !/\s/.test(text.trim()) ? embedFromUrl(text) : null
    if (embed) {
      saveSelection()
      insertFigure({ 'data-embed': embed.provider, 'data-id': embed.id }, null)
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
              onChange={(e) => void addFiles(Array.from(e.target.files ?? []))}
            />
            {uploading && <span className={cn('ms-2 text-xs', tok.low)}>Uploading…</span>}
          </>
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
            placeholder={asking === 'link' ? 'https://…' : 'YouTube or Vimeo address'}
            aria-label={asking === 'link' ? 'Link address' : 'Video address'}
            className={cn('min-w-0 flex-1 rounded-full border border-current/15 bg-transparent px-3 py-1.5 text-sm focus:outline-none', tok.text)}
          />
          <button
            type="button"
            onMouseDown={(e) => e.preventDefault()}
            onClick={submitAsk}
            disabled={!askValue.trim()}
            className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-3.5 py-1.5 text-xs font-bold text-ink disabled:opacity-40"
          >
            {asking === 'link' ? 'Add link' : 'Embed'}
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
      </div>
    </div>
  )
}
