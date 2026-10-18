import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import {
  Clapperboard,
  FileText,
  Globe2,
  Languages,
  Newspaper,
  Image as ImageIcon,
  Loader2,
  Lock,
  MapPin,
  Tag,
  Sparkles,
  Users,
  Video,
  X,
} from 'lucide-react'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { useMyProfile } from '@/hooks/useMyProfile'
import { useAppTheme } from '@/components/appdemo/theme'
import MemberAvatar from './MemberAvatar'
import { ApiError, kaluta, type Post, type UploadedMedia } from '@/lib/api'
import RichTextEditor from './RichTextEditor'
import { MentionList, useMentionSuggest } from './MentionSuggest'
import { onChange } from '@/lib/live'
import { articleMediaIds, htmlToText } from '@/lib/richtext'
import { LANGUAGES } from '@/i18n'
import { cn } from '@/lib/utils'

const MAX = 20000

/** "Translate ×4" — the platform's four other languages. */
const TRANSLATE_LANGS = ['en', 'sw', 'fr', 'ar', 'zh']

/**
 * One-to-Many (blueprint §Creator Studio), the way /app shows it.
 *
 * These are *declarations of intent*, not transformations: the pipeline that
 * would turn one post into a short, a newsletter and four translations does
 * not exist yet. The chips record what the author asked for so the post
 * carries it, and the strip says plainly that the work is queued rather than
 * done — a chip that silently did nothing would be the worse lie.
 */
const ONE_TO_MANY = [
  { id: 'short', icon: Clapperboard, label: 'Short video' },
  { id: 'newsletter', icon: Newspaper, label: 'Newsletter' },
  { id: 'translate', icon: Languages, label: 'Translate ×4' },
] as const

/**
 * Mirrors HASHTAG_RE in social-service. Keeping the two in step matters: a tag
 * shown as recognised here but dropped there is worse than no feedback at all.
 */
const HASHTAG_RE = /#([\wÀ-ÿ؀-ۿ一-鿿][\wÀ-ÿ؀-ۿ一-鿿-]{1,49})/g

function findHashtags(text: string): string[] {
  const found = [...text.matchAll(HASHTAG_RE)].map((m) => m[1].toLowerCase())
  return [...new Set(found)]
}

/**
 * Provenance labels (blueprint §17). The composer asks rather than guesses:
 * the platform cannot detect AI assistance reliably, so the honest design is to
 * make the author declare it — and to keep the label attached from here on.
 */
const PROVENANCE = [
  { id: 'original', label: 'Original', hint: 'You made this yourself.' },
  { id: 'ai_assisted', label: 'AI assisted', hint: 'AI helped you write or edit it.' },
  { id: 'ai_generated', label: 'AI generated', hint: 'AI produced it.' },
] as const

const VISIBILITY = [
  { id: 'public', label: 'Anyone', icon: Globe2 },
  { id: 'followers', label: 'Followers', icon: Users },
  { id: 'circle', label: 'A circle', icon: Lock },
] as const

type Kind = 'text' | 'image' | 'video' | 'article'

/**
 * The types the server actually stores, named one by one rather than as
 * `image/*`.
 *
 * `image/*` is what an iPhone reads as "send whatever you have", and what it
 * has is HEIC - which media-service refuses with a 415 and no browser can
 * display anyway. Naming jpeg and png makes iOS transcode the photo on the way
 * out, so the picture a member chose is the picture that uploads. It also stops
 * the picker offering files that were always going to be rejected.
 */
const IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif'
const VIDEO_ACCEPT = 'video/mp4,video/webm'

const KINDS: Array<{ id: Kind; label: string; icon: typeof ImageIcon; accept?: string; tint: string }> = [
  { id: 'image', label: 'Photo', icon: ImageIcon, accept: IMAGE_ACCEPT, tint: 'text-emerald-400' },
  { id: 'video', label: 'Video', icon: Video, accept: VIDEO_ACCEPT, tint: 'text-red-400' },
  { id: 'article', label: 'Article', icon: FileText, tint: 'text-sky' },
]

/**
 * The composer, in the shape people already know: a one-line prompt in the feed
 * that opens a dialog holding the actual form.
 *
 * Everything used to sit expanded in the feed — nine controls competing with the
 * posts below them, most of them irrelevant until you have decided to write.
 * Collapsed, the feed stays a feed; opened, the form has room to be explicit
 * about visibility and provenance.
 */
/**
 * The real pixel size of a picked file.
 *
 * Returned as {} rather than thrown when it cannot be read - a file the browser
 * will not decode is a problem for the upload to report, not for the measuring
 * step, and a post without dimensions still works (the feed falls back to a
 * reserved box).
 */
async function measure(file: File): Promise<{ width?: number; height?: number }> {
  const url = URL.createObjectURL(file)
  try {
    if (file.type.startsWith('video/')) {
      return await new Promise((resolve) => {
        const video = document.createElement('video')
        video.preload = 'metadata'
        video.onloadedmetadata = () => resolve({ width: video.videoWidth, height: video.videoHeight })
        video.onerror = () => resolve({})
        video.src = url
      })
    }
    return await new Promise((resolve) => {
      const image = new Image()
      image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight })
      image.onerror = () => resolve({})
      image.src = url
    })
  } finally {
    URL.revokeObjectURL(url)
  }
}

export default function Composer({
  onPosted,
  /** Hide the inline card — the feed shows a floating button instead once scrolled. */
  collapsed = false,
  openSignal = 0,
  community,
}: {
  onPosted: (post: Post) => void
  collapsed?: boolean
  openSignal?: number
  /**
   * Post into this community instead of choosing an audience.
   *
   * Inside a group the audience is not a question: the post goes to the group.
   * So the selector is replaced by the group's name, which also tells the
   * member where what they are writing will land - the one thing worth saying
   * when the same composer serves several places.
   */
  community?: { id: string; name: string }
}) {
  const { i18n } = useTranslation()
  const { user } = useAuth()
  const { tok } = useAppTheme()
  const fileRef = useRef<HTMLInputElement>(null)

  const [open, setOpen] = useState(false)
  const [kind, setKind] = useState<Kind>('text')
  const [body, setBody] = useState('')
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  // Finishing an @mention as it is typed, in the box where most of them are
  // written.
  const mention = useMentionSuggest(body, (next) => setBody(next.slice(0, MAX)), bodyRef)
  const [headline, setHeadline] = useState('')
  // previewUrl is local, made from the file the member picked. The server's own
  // address cannot be the preview: an upload nobody has attached to a post yet is
  // served only against a ticket, so it would show as a broken image until posted.
  const [media, setMedia] = useState<Array<UploadedMedia & { previewUrl?: string }>>([])
  // Pictures and clips placed inside an article's text. Attached to the post
  // like any other media - checked, classified, signed - and placed by id.
  const [inlineMedia, setInlineMedia] = useState<Array<UploadedMedia & { previewUrl: string }>>([])
  const [uploading, setUploading] = useState(false)
  // A picture still uploading inside the article: posting now would lose it.
  const [articleUploading, setArticleUploading] = useState(false)
  const [visibility, setVisibility] = useState<string>(community ? 'community' : 'public')
  const [circleId, setCircleId] = useState('')
  const [extras, setExtras] = useState<string[]>([])
  const [provenance, setProvenance] = useState<string>('original')
  const uploadInline = useCallback(
    async (file: File) => {
      const [asset, size] = await Promise.all([kaluta.media.upload(file, provenance), measure(file)])
      const previewUrl = URL.createObjectURL(file)
      setInlineMedia((current) => [...current, { ...asset, ...size, previewUrl }])
      return { id: asset.id, url: previewUrl, kind: asset.kind }
    },
    [provenance],
  )
  // Pictures that arrived with a pasted article: files the writer already
  // owns, so nothing is uploaded, but they are attached on Post exactly like
  // the ones uploaded here - the list below only attaches what it knows.
  const adoptInline = useCallback((items: Array<{ id: string; kind: string; url: string }>) => {
    setInlineMedia((current) => [
      ...current,
      ...items
        .filter((item) => !current.some((known) => known.id === item.id))
        .map((item) => ({
          id: item.id,
          url: item.url,
          kind: item.kind,
          size_bytes: 0,
          provenance: 'original',
          provenance_signed: false,
          previewUrl: item.url,
        })),
    ])
  }, [])
  // The author's own declaration. Nothing infers this — a wrong guess either
  // censors an adult or shows a child what they should not see.
  const [mature, setMature] = useState(false)
  const [showDetails, setShowDetails] = useState(false)
  const [topics, setTopics] = useState('')
  const [city, setCity] = useState('')
  // Declared, not detected: the language gateway can only separate scripts, so
  // an author saying "this is Swahili" beats the server guessing "English".
  const [lang, setLang] = useState(i18n.language.slice(0, 2))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Separate from `error`: the post succeeded, and only the extra formats have
  // something to report. Showing that as an error would misrepresent it.
  const [note, setNote] = useState<string | null>(null)

  const circles = useApi(() => kaluta.circles.list(), [])
  // A circle made on the Circles page is offered here without a reload.
  useEffect(() => onChange('circles', circles.reload), [circles.reload])

  // In a community the button has to name the community. Left to the audience
  // state it read "Post to Anyone" on a post going to one group, which is both
  // wrong and the kind of wrong that costs somebody their privacy once.
  const audienceLabel = community
    ? community.name
    : visibility === 'circle'
      ? (circles.data ?? []).find((c) => c.id === circleId)?.name ?? 'a circle'
      : visibility === 'followers'
        ? 'Followers'
        : 'Anyone'

  // Tags from the body plus anything typed in the topics field, so the chips
  // show everything the post will actually carry.
  const detectedTags = [
    ...new Set([
      ...findHashtags(kind === 'article' ? `${headline} ${body}` : body),
      ...topics.split(',').map((t) => t.trim().toLowerCase().replace(/^#/, '')).filter(Boolean),
    ]),
  ]

  // The floating button lives in the feed, so it asks us to open by bumping a
  // counter rather than by us lifting the whole dialog out of this component.
  useEffect(() => {
    if (openSignal > 0) setOpen(true)
  }, [openSignal])

  const me = useMyProfile()
  const myAvatar = <MemberAvatar displayName={user?.display_name} avatarUrl={me?.avatar_url} size={40} />

  /** Open the dialog, optionally jumping straight to a picker. */
  const start = (target: Kind) => {
    setKind(target)
    setOpen(true)
    const spec = KINDS.find((k) => k.id === target)
    if (spec?.accept) {
      // The file dialog has to follow the click that opened us, so it is queued
      // for the next frame rather than fired before the dialog mounts.
      requestAnimationFrame(() => {
        if (fileRef.current) {
          fileRef.current.accept = spec.accept!
          fileRef.current.click()
        }
      })
    }
  }

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setUploading(true)
    setError(null)
    try {
      const uploaded: Array<UploadedMedia & { previewUrl?: string }> = []
      for (const file of Array.from(files).slice(0, 4)) {
        const [asset, size] = await Promise.all([kaluta.media.upload(file, provenance), measure(file)])
        uploaded.push({ ...asset, ...size, previewUrl: URL.createObjectURL(file) })
      }
      setMedia((current) => [...current, ...uploaded].slice(0, 4))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const reset = () => {
    setBody('')
    setHeadline('')
    for (const item of media) if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
    setMedia([])
    for (const item of inlineMedia) URL.revokeObjectURL(item.previewUrl)
    setInlineMedia([])
    setTopics('')
    setCity('')
    setKind('text')
    // Cleared deliberately: an adult declaration must not carry over onto the
    // next post the member writes.
    setMature(false)
    setShowDetails(false)
    setError(null)
    setNote(null)
  }

  // An article body is HTML, so "<p><br></p>" is empty even though it is not
  // an empty string — measure the text, not the markup.
  const hasText = kind === 'article' ? htmlToText(body).length > 0 : body.trim() !== ''
  const canPost = (hasText || media.length > 0) && !busy && !uploading && !articleUploading

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canPost) return
    if (!community && visibility === 'circle' && !circleId) {
      setError('Pick which circle this goes to.')
      return
    }
    if (kind === 'article' && !headline.trim()) {
      setError('An article needs a headline.')
      return
    }

    setBusy(true)
    setError(null)
    try {
      const post = await kaluta.posts.create({
        // An article keeps its headline as the first line so the card can show
        // it without a separate column in the posts table.
        // The article's headline becomes the document's first heading, so the
        // stored body is one self-contained HTML document. Splitting a plain
        // first line off the front stopped working once bodies became markup.
        body:
          kind === 'article'
            ? `<h2>${headline.trim().replace(/[<>&]/g, '')}</h2>${body}`
            : body.trim(),
        format: kind,
        // In a community the target is fixed and is not the member's to change:
        // whatever the audience state happens to hold, this post belongs here.
        visibility: community ? 'community' : visibility,
        community_id: community?.id,
        circle_id: !community && visibility === 'circle' ? circleId : undefined,
        provenance,
        mature,
        lang,
        topics: topics
          .split(',')
          .map((t) => t.trim().toLowerCase())
          .filter(Boolean),
        city: city.trim() || undefined,
        // The article's own pictures and clips go first, in the order they
        // appear, and only those still in the text: one deleted while writing
        // is not attached.
        media: [
          ...(kind === 'article'
            ? articleMediaIds(body)
                .map((id) => inlineMedia.find((m) => m.id === id))
                .filter((m): m is UploadedMedia & { previewUrl: string } => Boolean(m))
            : []),
          ...media,
        ].map((m) => ({
          media_id: m.id,
          url: m.url,
          kind: m.kind,
          alt_text: m.alt_text,
          // Lets the feed reserve the right box before the picture arrives,
          // instead of collapsing and then jumping when it does.
          width: m.width,
          height: m.height,
        })),
      })
      // One-to-Many, after the post exists. Deliberately *after*: the post is
      // the thing the member asked for, and a publishing engine that is down
      // must not cost them their post. Failures are reported as a note, not as
      // a failed publish.
      let publishNote: string | null = null
      if (extras.length > 0) {
        const targets = extras
          .map((id) => ({ short: 'short_video', newsletter: 'newsletter', translate: 'translation' })[id])
          .filter(Boolean) as string[]
        const plain = kind === 'article' ? htmlToText(body) : body.trim()
        try {
          const job = await kaluta.creators.publish({
            source_text: plain,
            source_lang: lang,
            source_ref: post.id,
            targets,
            target_langs: extras.includes('translate')
              ? TRANSLATE_LANGS
              : [lang],
          })
          const skipped = job.unsupported.length
            ? ` ${job.unsupported.join(', ')} still needs a media pipeline.`
            : ''
          publishNote = `Posted. ${job.ready} of ${job.outputs} formats produced.${skipped}`
        } catch (err) {
          publishNote =
            err instanceof ApiError
              ? `Posted, but the publishing engine refused: ${err.message}`
              : 'Posted, but the publishing engine could not be reached.'
        }
      }

      reset()
      // After reset, which clears it — the note is about the post that just
      // went out, not about the empty form replacing it.
      setNote(publishNote)
      setOpen(false)
      onPosted(post)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not publish — is social-service up?')
    } finally {
      setBusy(false)
    }
  }

  const field = cn(
    'w-full rounded-card-sm px-3 py-2 text-xs focus:outline-none',
    tok.input,
    tok.text,
  )

  return (
    <>
      {/* Collapsed prompt in the feed */}
      {/* Hidden while the form is open. Showing the prompt above the form you
          are already typing into is the same control twice, and it pushed the
          real composer down the page. */}
      {!collapsed && !open && (
      <div className={cn('rounded-card-lg p-3', tok.card)}>
        <div className="flex items-center gap-2.5">
          {myAvatar}
          <button
            type="button"
            onClick={() => start('text')}
            className={cn(
              'min-w-0 flex-1 rounded-full px-4 py-2.5 text-start text-sm',
              tok.input,
              tok.low,
              tok.hoverBg,
            )}
          >
            Share your idea…
          </button>
        </div>

        <div className={cn('mt-2.5 flex items-center gap-1 border-t pt-2.5', tok.divider, 'border-t-current/10')}>
          {KINDS.map((k) => (
            <button
              key={k.id}
              type="button"
              onClick={() => start(k.id)}
              className={cn(
                'flex flex-1 items-center justify-center gap-1.5 rounded-card-sm px-2 py-2 text-xs font-semibold',
                tok.mid,
                tok.hoverBg,
              )}
            >
              <k.icon size={16} className={k.tint} aria-hidden="true" />
              {k.label}
            </button>
          ))}
        </div>
      </div>
      )}

      {note && (
        <p className="mt-2 rounded-card-sm border border-sky/25 bg-sky/10 px-3 py-2 text-xs text-sky">
          {note}
        </p>
      )}

      {/* The form itself — expanded in place, the way /app does it. A modal
          blanks the feed behind it, which is exactly the context you are
          writing into. */}
      {open && (
          <div className={cn('overflow-hidden rounded-card-lg p-3.5', tok.card)}>
          <form onSubmit={submit}>
            {/* Who you are, and who will see it */}
            <div className="flex items-center gap-2.5">
              {myAvatar}
              <div className="min-w-0">
                <p className={cn('truncate text-sm font-semibold', tok.text)}>{user?.display_name}</p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {community ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-gold/15 px-2 py-0.5 text-[0.68rem] font-semibold text-gold-soft">
                      <Users size={10} aria-hidden="true" />
                      Posting in {community.name}
                    </span>
                  ) : (
                    VISIBILITY.map((v) => (
                      <button
                        key={v.id}
                        type="button"
                        onClick={() => setVisibility(v.id)}
                        className={cn(
                          'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.68rem] font-semibold',
                          visibility === v.id ? 'bg-gold/15 text-gold-soft' : cn(tok.low, tok.hoverBg),
                        )}
                      >
                        <v.icon size={10} aria-hidden="true" />
                        {v.label}
                      </button>
                    ))
                  )}
                </div>
              </div>
            </div>

            {!community && visibility === 'circle' && (
              <div className="mt-3">
                {(circles.data ?? []).length === 0 ? (
                  <p className="rounded-card-sm border border-amber-400/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                    You have no circles yet —{' '}
                    <Link to="/circles" className="font-semibold underline underline-offset-2">
                      create one
                    </Link>{' '}
                    before posting to it.
                  </p>
                ) : (
                  <select
                    value={circleId}
                    onChange={(e) => setCircleId(e.target.value)}
                    aria-label="Circle"
                    className={field}
                  >
                    <option value="">Choose a circle…</option>
                    {(circles.data ?? []).map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.name} · {c.members_count} member{c.members_count === 1 ? '' : 's'}
                      </option>
                    ))}
                  </select>
                )}
                {/* Said before posting, not discovered afterwards: a circle that
                    reaches nobody makes the post private to its author. */}
                {(circles.data ?? []).find((c) => c.id === circleId)?.members_count === 0 && (
                  <p className="mt-2 text-xs text-warning">
                    Nobody is in this circle yet — only you will see this post.
                  </p>
                )}
              </div>
            )}

            {kind === 'article' && (
              <input
                value={headline}
                onChange={(e) => setHeadline(e.target.value)}
                placeholder="Article headline"
                aria-label="Article headline"
                className={cn(
                  'mt-4 w-full bg-transparent text-lg font-semibold focus:outline-none',
                  tok.text,
                )}
              />
            )}

            {kind === 'article' ? (
              <div className="mt-3">
                <RichTextEditor
                  value={body}
                  onChange={setBody}
                  onUploadMedia={uploadInline}
                  onAdoptMedia={adoptInline}
                  onUploadingChange={setArticleUploading}
                />
              </div>
            ) : (
              <div className="relative">
                <textarea
                  ref={bodyRef}
                  value={body}
                  onChange={(e) => setBody(e.target.value.slice(0, MAX))}
                  onKeyDown={(event) => {
                    // Arrows and Enter belong to the list while it is open.
                    if (mention.onKeyDown(event)) event.preventDefault()
                  }}
                  onKeyUp={mention.sync}
                  onClick={mention.sync}
                  rows={4}
                  placeholder={`What's on your mind, ${user?.display_name?.split(' ')[0] ?? 'friend'}?`}
                  aria-label="Post body"
                  autoFocus
                  className={cn(
                    'mt-3 w-full resize-none bg-transparent text-base focus:outline-none',
                    tok.text,
                    'placeholder:opacity-60',
                  )}
                />
                {mention.open && (
                  <MentionList
                    items={mention.items}
                    index={mention.index}
                    onPick={mention.pick}
                    onHover={mention.setIndex}
                  />
                )}
              </div>
            )}

            {/* Live confirmation that a #tag was recognised. Without it, typing
                a hashtag looks like it does nothing until after publishing. */}
            {detectedTags.length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <span className={cn('text-[0.68rem]', tok.low)}>Topics detected</span>
                {detectedTags.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full bg-gold/15 px-2 py-0.5 text-[0.7rem] font-semibold text-gold-soft"
                  >
                    #{tag}
                  </span>
                ))}
              </div>
            )}

            {media.length > 0 && (
              <ul className={cn('grid gap-2', media.length === 1 ? 'grid-cols-1' : 'grid-cols-2')}>
                {media.map((item) => (
                  <li key={item.id} className="relative overflow-hidden rounded-card-sm border border-white/10">
                    {item.kind === 'video' ? (
                      <video src={item.previewUrl ?? item.url} className="h-36 w-full object-cover" muted playsInline />
                    ) : (
                      <img src={item.previewUrl ?? item.url} alt="" className="h-36 w-full object-cover" />
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
                        setMedia((c) => c.filter((m) => m.id !== item.id))
                      }}
                      aria-label="Remove"
                      className="absolute end-1.5 top-1.5 rounded-full bg-ink/80 p-1 text-text-hi"
                    >
                      <X size={12} />
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              onChange={(e) => void upload(e.target.files)}
              aria-hidden="true"
            />

            {/* Add to your post */}
            <div className={cn('mt-4 flex items-center gap-1 rounded-card-sm border p-2', tok.input)}>
              <span className={cn('me-auto ps-1 text-xs font-semibold', tok.mid)}>Add to your post</span>
              {KINDS.map((k) => (
                <button
                  key={k.id}
                  type="button"
                  onClick={() => (k.accept ? start(k.id) : setKind(k.id))}
                  aria-label={k.label}
                  title={k.label}
                  className={cn(
                    'rounded-full p-2',
                    kind === k.id ? 'bg-white/10' : tok.hoverBg,
                  )}
                >
                  <k.icon size={17} className={k.tint} aria-hidden="true" />
                </button>
              ))}
              <button
                type="button"
                onClick={() => setShowDetails((v) => !v)}
                aria-label="Topics, place and language"
                aria-expanded={showDetails}
                title="Topics, place and language"
                className={cn('rounded-full p-2', showDetails ? 'bg-white/10' : tok.hoverBg)}
              >
                <Tag size={17} className="text-gold" aria-hidden="true" />
              </button>
            </div>

            {uploading && (
              <p className={cn('mt-2 inline-flex items-center gap-1.5 text-xs', tok.low)}>
                <Loader2 size={12} className="animate-spin" aria-hidden="true" />
                Uploading…
              </p>
            )}

            {showDetails && (
              <div className="mt-3 grid gap-2 sm:grid-cols-3">
                <label className="relative">
                  <Tag size={12} className={cn('absolute start-2.5 top-2.5', tok.low)} aria-hidden="true" />
                  <input
                    value={topics}
                    onChange={(e) => setTopics(e.target.value)}
                    placeholder="Topics"
                    aria-label="Topics, comma separated"
                    className={cn(field, 'ps-7')}
                  />
                </label>
                <label className="relative">
                  <MapPin size={12} className={cn('absolute start-2.5 top-2.5', tok.low)} aria-hidden="true" />
                  <input
                    value={city}
                    onChange={(e) => setCity(e.target.value)}
                    placeholder="City"
                    aria-label="City"
                    className={cn(field, 'ps-7')}
                  />
                </label>
                <select
                  value={lang}
                  onChange={(e) => setLang(e.target.value)}
                  aria-label="Language of this post"
                  className={field}
                >
                  {LANGUAGES.map((l) => (
                    <option key={l.code} value={l.code}>
                      In {l.label}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {/* Provenance is a platform rule, not a detail — always visible. */}
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              <span className={cn('text-xs', tok.low)}>This content is</span>
              {PROVENANCE.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setProvenance(p.id)}
                  title={p.hint}
                  className={cn(
                    'rounded-full border px-2.5 py-1 text-[0.7rem] font-semibold',
                    provenance === p.id
                      ? 'border-sky/50 bg-sky/10 text-sky'
                      : cn('border-current/20', tok.low, tok.hoverBg),
                  )}
                >
                  {p.label}
                </button>
              ))}
            </div>

            {/* Age gate (§Safety). Declared by the author because the server has
                no way to know, and it is what keeps this post out of a minor's
                feed — a filter, not a warning banner. */}
            <label className="mt-2.5 flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                checked={mature}
                onChange={(event) => setMature(event.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 accent-gold"
              />
              <span className={cn('text-xs', tok.low)}>
                Adult content
                <span className="ms-1 opacity-70">— hidden from members set to child or teen</span>
              </span>
            </label>

            {/* One-to-Many, as /app presents it. */}
            <div className="mt-3 rounded-card-md border border-indigo/25 bg-indigo/8 p-3">
              <p className="mb-2 flex items-center gap-1.5 text-[0.68rem] font-bold uppercase tracking-wider text-sky">
                <Sparkles size={11} aria-hidden="true" /> One-to-Many · also create
              </p>
              <div className="flex flex-wrap gap-1.5">
                {ONE_TO_MANY.map((o) => {
                  const on = extras.includes(o.id)
                  return (
                    <button
                      key={o.id}
                      type="button"
                      aria-pressed={on}
                      onClick={() =>
                        setExtras((xs) => (on ? xs.filter((x) => x !== o.id) : [...xs, o.id]))
                      }
                      className={cn(
                        'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold',
                        on
                          ? 'bg-gradient-to-br from-gold-soft to-gold text-ink'
                          : cn(tok.subtleBg, tok.mid, tok.hoverBg),
                      )}
                    >
                      <o.icon size={12} aria-hidden="true" /> {o.label}
                    </button>
                  )
                })}
              </div>
              {extras.length > 0 && (
                <p className={cn('mt-2 text-[0.68rem]', tok.low)}>
                  Recorded with the post. The pipeline that produces these is not built yet, so
                  nothing is generated today — this is a request, not a result.
                </p>
              )}
            </div>

            {error && (
              <p role="alert" className="mt-3 text-sm text-red-300">
                {error}
              </p>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  reset()
                }}
                className={cn('rounded-full px-4 py-2 text-xs font-semibold', tok.subtleBg, tok.mid)}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canPost}
                className="rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-xs font-bold text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.35)] enabled:hover:brightness-110 disabled:opacity-40"
              >
                {busy ? 'Posting…' : `Post to ${audienceLabel}`}
              </button>
            </div>
          </form>
          </div>
        )}
    </>
  )
}
