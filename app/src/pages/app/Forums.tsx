import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import {
  ArrowLeft,
  Bell,
  BellOff,
  Bookmark,
  BookOpen,
  CheckCircle2,
  ChevronRight,
  Globe,
  Landmark,
  Lock,
  MessageSquare,
  MessageSquarePlus,
  Paperclip,
  Pin,
  Plus,
  ScrollText,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  UsersRound,
  X,
} from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import KnowledgePanel from '@/components/forums/KnowledgePanel'
import ThreadComposer from '@/components/forums/ThreadComposer'
import ThreadView from '@/components/forums/ThreadView'
import { ForumPrefsDialog, Sheet } from '@/components/forums/ForumDialogs'
import { ForumSettingsDialog, ModerationDialog } from '@/components/forums/ForumAdmin'
import { KIND, VISIBILITY, ago, countryName, field } from '@/components/forums/forumText'
import MemberAvatar from '@/components/social/MemberAvatar'
import { useAuth } from '@/hooks/useAuth'
import { useAppTheme } from '@/components/appdemo/theme'
import {
  ApiError,
  kaluta,
  type Forum,
  type ForumDetail,
  type Thread,
  type ThreadBrief,
  type ThreadDetail,
  type ThreadKind,
  type ThreadSort,
} from '@/lib/api'
import { cn } from '@/lib/utils'

const SORT_TABS: Array<{ id: ThreadSort; label: string; empty: string }> = [
  { id: 'recent', label: 'Recent', empty: 'No discussions posted yet. Be the first to start a conversation!' },
  { id: 'trending', label: 'Trending', empty: 'Nothing is trending here yet.' },
  { id: 'solved', label: 'Solved', empty: 'No question has been answered here yet.' },
  { id: 'unanswered', label: 'Unanswered', empty: 'Every question here has at least one reply.' },
]

const LANG_FILTERS = [
  ['', 'Any language'],
  ['en', 'English'],
  ['fr', 'Français'],
  ['sw', 'Kiswahili'],
  ['ar', 'العربية'],
  ['pt', 'Português'],
  ['es', 'Español'],
] as const

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => (typeof window !== 'undefined' ? window.matchMedia(query).matches : false))
  useEffect(() => {
    const list = window.matchMedia(query)
    const update = () => setMatches(list.matches)
    update()
    list.addEventListener('change', update)
    return () => list.removeEventListener('change', update)
  }, [query])
  return matches
}

function VisibilityBadge({ visibility }: { visibility?: string }) {
  if (!visibility) return null
  const Icon = visibility === 'public' ? Globe : visibility === 'members' ? UsersRound : Lock
  return (
    <span title={VISIBILITY[visibility]?.hint} className="inline-flex items-center gap-1 rounded-full border border-white/10 px-2 py-0.5 text-[10px] font-semibold text-text-mid">
      <Icon size={10} aria-hidden="true" />
      {VISIBILITY[visibility]?.label ?? visibility}
    </span>
  )
}

function ThreadRow({ thread, onOpen }: { thread: Thread | ThreadBrief; onOpen: () => void }) {
  const kind = (thread.kind ?? 'question') as ThreadKind
  const full = thread as Thread
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex w-full flex-col gap-2.5 rounded-2xl border border-white/8 bg-ink-2/40 p-4 text-start transition-all hover:border-gold/30 hover:bg-ink-2/60"
    >
      <div className="flex w-full flex-wrap items-center gap-1.5">
        <span className="rounded-full border border-gold/30 bg-gold/10 px-2 py-0.5 text-[10px] font-semibold text-gold">{KIND[kind].label}</span>
        {kind === 'question' &&
          (thread.solved ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">
              <CheckCircle2 size={10} aria-hidden="true" /> Solved
            </span>
          ) : thread.replies_count === 0 ? (
            <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] font-semibold text-text-mid">Unanswered</span>
          ) : null)}
        {full.pinned && (
          <span className="inline-flex items-center gap-1 rounded-full border border-gold/40 bg-gold/10 px-2 py-0.5 text-[10px] font-semibold text-gold">
            <Pin size={10} aria-hidden="true" /> Pinned
          </span>
        )}
        {full.locked && <Lock size={11} className="text-text-low" aria-label="Locked" />}
        {full.commercial && <span className="rounded-full border border-amber-400/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-200">Commercial</span>}
        {full.session_id && <span className="rounded-full border border-sky-400/30 bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold text-sky">Expert session</span>}
        {'forum_name' in thread && thread.forum_name && <span className="text-[11px] text-text-low">in {thread.forum_name}</span>}
      </div>
      <h3 className="text-base font-semibold leading-snug text-text-hi transition-colors group-hover:text-gold">{thread.title}</h3>
      {'excerpt' in thread && thread.excerpt && <p className="line-clamp-2 text-xs text-text-mid">{thread.excerpt}</p>}
      {full.ai_summary && (
        <p className="flex items-start gap-1.5 rounded-xl border border-sky-400/20 bg-sky-500/8 px-3 py-2 text-xs text-sky">
          <Sparkles size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span className="line-clamp-2">AI summary: {full.ai_summary}</span>
        </p>
      )}
      <div className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 border-t border-white/6 pt-2 text-xs text-text-low">
        <span className="flex min-w-0 items-center gap-1.5">
          <MemberAvatar handle={thread.author?.handle} displayName={thread.author?.display_name} avatarUrl={thread.author?.avatar_url} size={20} />
          <span className="truncate font-medium text-text-mid">{thread.author?.display_name || 'Member'}</span>
        </span>
        <span className="flex items-center gap-1">
          <MessageSquare size={12} aria-hidden="true" /> {thread.replies_count} {thread.replies_count === 1 ? 'reply' : 'replies'}
        </span>
        {full.last_reply_at ? <span>last reply {ago(full.last_reply_at)}</span> : <span>{ago(full.created_at ?? thread.last_activity_at)}</span>}
        {full.edited_at && <span>· edited</span>}
        {(full.attachments_count ?? 0) > 0 && <Paperclip size={11} aria-label="Has attachments" />}
        {full.tags?.slice(0, 3).map((t) => <span key={t}>#{t}</span>)}
        {(full.country || full.region) && <span>{[full.region, countryName(full.country)].filter(Boolean).join(', ')}</span>}
      </div>
    </button>
  )
}

export default function Forums() {
  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const { user } = useAuth()
  const { lang: locale } = useAppTheme()

  const [forums, setForums] = useState<Forum[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [forumId, setForumId] = useState<string | null>(null)
  const [forum, setForum] = useState<ForumDetail | null>(null)
  const [threads, setThreads] = useState<Thread[]>([])
  const [sort, setSort] = useState<ThreadSort>('recent')
  const [view, setView] = useState<'discussions' | 'knowledge'>('discussions')
  const [params, setParams] = useSearchParams()
  const linkedThread = useRef(params.get('thread'))
  const linkedForum = useRef(params.get('forum'))
  const linkedView = useRef(params.get('view'))
  const [review, setReview] = useState(params.get('review'))
  const [threadsLoading, setThreadsLoading] = useState(false)
  const latestThreadsRequest = useRef(0)
  const [open, setOpen] = useState<ThreadDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [canTranslate, setCanTranslate] = useState(false)

  // The directory's own filters (sent to the server) and a global search.
  const [directoryQuery, setDirectoryQuery] = useState('')
  const [category, setCategory] = useState('')
  const [dirLang, setDirLang] = useState('')
  const [followedOnly, setFollowedOnly] = useState(params.get('followed') === '1')
  const [recommended, setRecommended] = useState<Array<Forum & { reason: string }>>([])
  const [globalResults, setGlobalResults] = useState<Awaited<ReturnType<typeof kaluta.forums.search>> | null>(null)
  const [saved, setSaved] = useState<ThreadBrief[] | null>(null)

  // One forum's filters.
  const [threadQuery, setThreadQuery] = useState('')
  const [kindFilter, setKindFilter] = useState<ThreadKind | ''>('')
  const [langFilter, setLangFilter] = useState('')
  const [countryFilter, setCountryFilter] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [showFilters, setShowFilters] = useState(false)

  const [isCreatingForum, setIsCreatingForum] = useState(false)
  const [newForum, setNewForum] = useState({ name: '', description: '', category: '', visibility: 'inherit' as 'inherit' | 'private' })
  const [isCreatingThread, setIsCreatingThread] = useState(false)
  const [dialog, setDialog] = useState<null | 'settings' | 'moderation' | 'prefs'>(null)
  const [reviewNote, setReviewNote] = useState('')

  useEffect(() => {
    kaluta.messages.capabilities().then((c) => setCanTranslate(c.translate)).catch(() => setCanTranslate(false))
  }, [])

  const loadForums = useCallback(async () => {
    try {
      const result = await kaluta.forums.list({ q: directoryQuery.trim() || undefined, category: category || undefined, lang: dirLang || undefined, followed: followedOnly || undefined })
      setForums(result.items)
      setCategories(result.categories ?? [])
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load forums')
    }
  }, [directoryQuery, category, dirLang, followedOnly])

  useEffect(() => {
    const t = window.setTimeout(() => void loadForums(), directoryQuery ? 300 : 0)
    return () => window.clearTimeout(t)
  }, [loadForums, directoryQuery])

  useEffect(() => {
    if (user) kaluta.forums.recommended().then((r) => setRecommended(r.items)).catch(() => setRecommended([]))
  }, [user])

  const loadForum = useCallback(async (id: string) => {
    try {
      setForum(await kaluta.forums.detail(id))
    } catch (err) {
      setForum(null)
      setError(err instanceof ApiError ? err.message : 'Could not open the forum')
    }
  }, [])

  const fetchThreads = useCallback(
    async (id: string, order: ThreadSort) => {
      const request = ++latestThreadsRequest.current
      setThreadsLoading(true)
      try {
        const result = await kaluta.forums.threads(id, order, {
          q: threadQuery.trim() || undefined,
          kind: kindFilter || undefined,
          lang: langFilter || undefined,
          country: countryFilter.length === 2 ? countryFilter : undefined,
          tag: tagFilter.trim() || undefined,
        })
        if (request === latestThreadsRequest.current) setThreads(result.items)
      } catch (err) {
        if (request === latestThreadsRequest.current) {
          setThreads([])
          setError(err instanceof ApiError ? err.message : 'Could not load threads')
        }
      } finally {
        if (request === latestThreadsRequest.current) setThreadsLoading(false)
      }
    },
    [threadQuery, kindFilter, langFilter, countryFilter, tagFilter],
  )

  // Filters re-run the list as they change (the search box after a pause).
  useEffect(() => {
    if (!forumId) return
    const t = window.setTimeout(() => void fetchThreads(forumId, sort), threadQuery ? 300 : 0)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchThreads])

  const selectForum = useCallback(
    async (id: string, nextView: 'discussions' | 'knowledge' = 'discussions') => {
      setForumId(id)
      setSort('recent')
      setView(nextView)
      setOpen(null)
      setGlobalResults(null)
      setSaved(null)
      setIsCreatingThread(false)
      setThreadQuery('')
      setKindFilter('')
      setTagFilter('')
      void loadForum(id)
      await fetchThreads(id, 'recent')
    },
    [fetchThreads, loadForum],
  )

  const openThread = useCallback(
    async (threadId: string) => {
      setError(null)
      try {
        let detail = await kaluta.forums.thread(threadId)
        // A merged thread leads to the one it was merged into.
        if (detail.merged_into) detail = await kaluta.forums.thread(detail.merged_into)
        setOpen(detail)
        if (detail.forum_id !== forumId) {
          setForumId(detail.forum_id)
          void loadForum(detail.forum_id)
          void fetchThreads(detail.forum_id, 'recent')
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not open the thread')
      }
    },
    [forumId, fetchThreads, loadForum],
  )

  useEffect(() => {
    const threadId = linkedThread.current
    const fid = linkedForum.current
    if (threadId) {
      linkedThread.current = null
      void openThread(threadId).finally(() => setParams({}, { replace: true }))
    } else if (fid) {
      linkedForum.current = null
      void selectForum(fid, linkedView.current === 'knowledge' ? 'knowledge' : 'discussions').finally(() => setParams({}, { replace: true }))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const reloadOpen = async () => {
    if (open) setOpen(await kaluta.forums.thread(open.id))
  }

  const backToThreads = () => {
    setOpen(null)
    if (forumId) {
      void fetchThreads(forumId, sort)
      void loadForum(forumId)
    }
  }

  const changeSort = (next: ThreadSort) => {
    if (!forumId || next === sort) return
    setSort(next)
    void fetchThreads(forumId, next)
  }

  const createForum = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!newForum.name.trim()) return
    try {
      const created = await kaluta.forums.create({
        name: newForum.name.trim(),
        description: newForum.description.trim(),
        category: newForum.category.trim() || undefined,
        visibility: newForum.visibility,
      })
      setNewForum({ name: '', description: '', category: '', visibility: 'inherit' })
      setIsCreatingForum(false)
      await loadForums()
      void selectForum(created.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create the forum')
    }
  }

  const runGlobalSearch = async () => {
    if (directoryQuery.trim().length < 2) return
    try {
      setGlobalResults(await kaluta.forums.search(directoryQuery.trim(), { lang: dirLang || undefined }))
      setOpen(null)
      setForumId(null)
      setSaved(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Search failed')
    }
  }

  const toggleFollow = async () => {
    if (!forum) return
    try {
      if (forum.following) await kaluta.forums.unfollow(forum.id)
      else await kaluta.forums.follow(forum.id)
      await loadForum(forum.id)
      void loadForums()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change the follow')
    }
  }

  const showMobileForums = !isDesktop && !forumId && !open && !globalResults && !saved
  const showMobileThreads = !isDesktop && Boolean(forumId) && !open
  const showMobileThread = !isDesktop && Boolean(open)
  const activeForum = useMemo(() => forums.find((f) => f.id === forumId), [forums, forumId])
  const inputClass =
    'w-full rounded-2xl border border-white/10 bg-ink-2/60 px-4 py-3 text-sm text-text-hi placeholder:text-text-low focus:border-gold/50 focus:outline-none transition-colors'

  return (
    <AppShell title="Forums" subtitle="Questions, answers and discussions, by topic and by place. What gets settled becomes cited knowledge.">
      <div className="mx-auto max-w-7xl">
        <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
          {(isDesktop || showMobileForums) && (
            <aside className="space-y-4">
              <div className="cloud-card space-y-3 p-4">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold text-text-hi">Forums</h2>
                  <div className="flex gap-1">
                    {user && (
                      <button type="button" onClick={() => setDialog('prefs')} aria-label="Forum notifications and profile" title="Forum notifications and profile" className="inline-flex min-h-[40px] min-w-[40px] items-center justify-center rounded-xl bg-white/8 text-text-mid hover:bg-white/12">
                        <Settings2 size={15} aria-hidden="true" />
                      </button>
                    )}
                    <button type="button" onClick={() => setIsCreatingForum(!isCreatingForum)} className="inline-flex min-h-[40px] min-w-[40px] items-center justify-center rounded-xl bg-white/8 text-text-mid hover:bg-white/12" aria-label={isCreatingForum ? 'Cancel' : 'Open a new forum'}>
                      {isCreatingForum ? <X size={16} aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}
                    </button>
                  </div>
                </div>

                {isCreatingForum && (
                  <form onSubmit={createForum} className="space-y-2 border-t border-white/8 pt-3">
                    <input value={newForum.name} onChange={(e) => setNewForum({ ...newForum, name: e.target.value })} placeholder="Forum name (e.g. Agritech & crops)" aria-label="Forum name" className={field} autoFocus />
                    <textarea value={newForum.description} onChange={(e) => setNewForum({ ...newForum, description: e.target.value })} rows={2} placeholder="What it is about, and who it is for" aria-label="Description" className={`${field} resize-none`} />
                    <input value={newForum.category} onChange={(e) => setNewForum({ ...newForum, category: e.target.value })} placeholder="Subject (e.g. Farming) - optional" aria-label="Subject" className={field} />
                    <select value={newForum.visibility} onChange={(e) => setNewForum({ ...newForum, visibility: e.target.value as 'inherit' | 'private' })} aria-label="Who can read it" className={field}>
                      <option value="inherit">Public: anyone can read it</option>
                      <option value="private">Private: only you and the moderators you appoint</option>
                    </select>
                    <p className="text-xs text-text-low">A forum of a community is created from the community’s page, by its owner or moderators.</p>
                    <button type="submit" disabled={!newForum.name.trim()} className="inline-flex min-h-[40px] items-center rounded-xl bg-gold px-4 text-xs font-bold text-ink disabled:opacity-40">
                      Create
                    </button>
                  </form>
                )}

                <form
                  className="relative"
                  onSubmit={(e) => {
                    e.preventDefault()
                    void runGlobalSearch()
                  }}
                >
                  <Search size={15} className="absolute left-3.5 top-1/2 -translate-y-1/2 text-text-low" aria-hidden="true" />
                  <input type="search" value={directoryQuery} onChange={(e) => setDirectoryQuery(e.target.value)} placeholder="Find a forum, or press Enter to search all questions" aria-label="Search forums and questions" className={cn(inputClass, 'py-2.5 pl-9 text-xs')} />
                </form>
                <div className="flex flex-wrap gap-1.5">
                  <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Subject" className="rounded-full border border-white/10 bg-transparent px-2.5 py-1 text-xs text-text-mid">
                    <option value="">All subjects</option>
                    {categories.map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <select value={dirLang} onChange={(e) => setDirLang(e.target.value)} aria-label="Language" className="rounded-full border border-white/10 bg-transparent px-2.5 py-1 text-xs text-text-mid">
                    {LANG_FILTERS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                  </select>
                  {user && (
                    <button type="button" aria-pressed={followedOnly} onClick={() => setFollowedOnly((v) => !v)} className={cn('rounded-full border px-2.5 py-1 text-xs font-semibold', followedOnly ? 'border-gold/60 bg-gold/15 text-gold' : 'border-white/10 text-text-mid')}>
                      Following
                    </button>
                  )}
                  {user && (
                    <button
                      type="button"
                      onClick={() => {
                        setForumId(null)
                        setOpen(null)
                        setGlobalResults(null)
                        kaluta.forums.saved().then((r) => setSaved(r.items)).catch(() => setSaved([]))
                      }}
                      className="inline-flex items-center gap-1 rounded-full border border-white/10 px-2.5 py-1 text-xs font-semibold text-text-mid"
                    >
                      <Bookmark size={11} aria-hidden="true" /> Saved
                    </button>
                  )}
                </div>
              </div>

              <nav aria-label="Forums directory">
                <ul className="space-y-2">
                  {forums.length === 0 && (
                    <li className="cloud-card p-6 text-center text-sm text-text-low">
                      {followedOnly ? 'You do not follow any forum yet.' : 'No forum matches.'}
                    </li>
                  )}
                  {forums.map((f) => {
                    const isSelected = forumId === f.id
                    return (
                      <li key={f.id}>
                        <button
                          type="button"
                          onClick={() => void selectForum(f.id)}
                          className={cn('group flex w-full items-start gap-3 rounded-2xl border p-3.5 text-start transition-all', isSelected ? 'border-gold/50 bg-gold/10' : 'border-white/8 bg-ink-2/40 hover:border-white/20')}
                        >
                          <span className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-xl', isSelected ? 'bg-gold/20 text-gold' : 'bg-white/6 text-text-low')}>
                            <Landmark size={18} aria-hidden="true" />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="flex items-center gap-1.5">
                              <span className="truncate text-sm font-medium text-text-hi">{f.name}</span>
                              {f.following && <Bell size={11} className="shrink-0 text-gold-soft" aria-label="Following" />}
                            </span>
                            {f.description && <span className="line-clamp-2 block text-xs text-text-mid">{f.description}</span>}
                            <span className="caption mt-1 flex flex-wrap items-center gap-x-1.5 text-text-low">
                              <span>{f.threads_count} {f.threads_count === 1 ? 'discussion' : 'discussions'}</span>
                              {(f.unanswered_count ?? 0) > 0 && <span>· {f.unanswered_count} unanswered</span>}
                              {f.last_activity_at && <span>· active {ago(f.last_activity_at)}</span>}
                              {f.community && <span className="truncate text-gold-soft">· {f.community.name}</span>}
                            </span>
                            <span className="mt-1 flex flex-wrap gap-1">
                              <VisibilityBadge visibility={f.visibility} />
                              {f.category && <span className="rounded-full bg-white/6 px-2 py-0.5 text-[10px] text-text-mid">{f.category}</span>}
                            </span>
                          </span>
                          <ChevronRight size={16} className="mt-3 shrink-0 text-text-low" aria-hidden="true" />
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </nav>

              {recommended.length > 0 && !followedOnly && (
                <section className="cloud-card p-4">
                  <h2 className="text-sm font-semibold text-text-hi">Suggested for you</h2>
                  <ul className="mt-2 space-y-2">
                    {recommended.slice(0, 5).map((f) => (
                      <li key={f.id}>
                        <button type="button" onClick={() => void selectForum(f.id)} className="w-full text-start">
                          <span className="block text-sm text-text-hi hover:text-gold">{f.name}</span>
                          <span className="block text-xs text-text-low">{f.reason}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </aside>
          )}

          <main className="min-w-0 space-y-4">
            {!isDesktop && (globalResults || saved) && (
              <button type="button" onClick={() => { setGlobalResults(null); setSaved(null) }} className="inline-flex min-h-[44px] items-center gap-2 rounded-xl px-2.5 text-xs font-semibold text-gold">
                <ArrowLeft size={16} aria-hidden="true" /> Forums
              </button>
            )}
            {globalResults && !open && (
              <section className="space-y-3" aria-label="Search results">
                <h2 className="text-sm font-bold text-text-hi">Results for “{directoryQuery}” in the forums you can read</h2>
                {globalResults.threads.length === 0 && globalResults.knowledge.length === 0 && <p className="cloud-card p-6 text-sm text-text-low">Nothing found.</p>}
                {globalResults.knowledge.length > 0 && (
                  <div className="cloud-card p-4">
                    <h3 className="flex items-center gap-1.5 text-xs font-bold text-text-hi"><BookOpen size={13} aria-hidden="true" /> Knowledge</h3>
                    <ul className="mt-2 space-y-2">
                      {globalResults.knowledge.map((k) => (
                        <li key={k.id}>
                          <button type="button" onClick={() => void selectForum(k.forum_id, 'knowledge')} className="text-start text-sm text-text-hi hover:text-gold">{k.question}</button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <ul className="space-y-3">
                  {globalResults.threads.map((t) => <li key={t.id}><ThreadRow thread={t} onOpen={() => void openThread(t.id)} /></li>)}
                </ul>
              </section>
            )}
            {saved && !open && (
              <section className="space-y-3" aria-label="Saved threads">
                <h2 className="text-sm font-bold text-text-hi">Saved threads</h2>
                {saved.length === 0 && <p className="cloud-card p-6 text-sm text-text-low">Nothing saved yet. Use Save on a thread.</p>}
                <ul className="space-y-3">{saved.map((t) => <li key={t.id}><ThreadRow thread={t} onOpen={() => void openThread(t.id)} /></li>)}</ul>
              </section>
            )}

            {isDesktop && !forumId && !globalResults && !saved && (
              <div className="cloud-card flex min-h-[360px] flex-col items-center justify-center p-8 text-center">
                <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-white/10 bg-white/4 text-gold">
                  <Landmark size={26} aria-hidden="true" />
                </div>
                <h3 className="text-base font-semibold text-text-hi">Choose a forum, or search every question</h3>
                <p className="caption mt-1.5 max-w-sm text-text-mid">A forum is about one subject or one place. Some belong to a community; reading or following one never makes you a member of it.</p>
              </div>
            )}

            {forumId && !open && (isDesktop || showMobileThreads) && (
              <div className="space-y-4">
                <div className="cloud-card p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="flex min-w-0 items-start gap-3">
                      {!isDesktop && (
                        <button type="button" onClick={() => { setForumId(null); setForum(null); setIsCreatingThread(false) }} className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-xl bg-white/8 text-text-mid" aria-label="Back to forums">
                          <ArrowLeft size={18} aria-hidden="true" />
                        </button>
                      )}
                      <div className="min-w-0">
                        <h2 className="truncate text-lg font-bold text-text-hi">{forum?.name ?? activeForum?.name ?? 'Forum'}</h2>
                        {forum?.description && <p className="mt-0.5 text-sm text-text-mid">{forum.description}</p>}
                        <p className="caption mt-1 flex flex-wrap items-center gap-1.5 text-text-low">
                          <VisibilityBadge visibility={forum?.visibility} />
                          <span>{forum?.threads_count ?? 0} discussions</span>
                          {(forum?.unanswered_count ?? 0) > 0 && <span>· {forum?.unanswered_count} unanswered</span>}
                          {forum?.last_activity_at && <span>· active {ago(forum.last_activity_at)}</span>}
                          {forum?.community && (
                            <>
                              <span>· in</span>
                              <Link to={`/communities/${forum.community.slug}`} className="font-semibold text-gold-soft hover:underline">{forum.community.name}</Link>
                            </>
                          )}
                        </p>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {user && forum && (
                        <button type="button" onClick={() => void toggleFollow()} aria-pressed={forum.following} className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl border border-white/10 px-3 text-xs font-semibold text-text-mid hover:text-text-hi">
                          {forum.following ? <BellOff size={14} aria-hidden="true" /> : <Bell size={14} aria-hidden="true" />}
                          {forum.following ? 'Following' : 'Follow'}
                        </button>
                      )}
                      {forum && (forum.can_configure || forum.can_moderate) && (
                        <button type="button" onClick={() => setDialog('settings')} className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl border border-white/10 px-3 text-xs font-semibold text-text-mid hover:text-text-hi">
                          <Settings2 size={14} aria-hidden="true" /> Settings
                        </button>
                      )}
                      {forum?.can_moderate && (
                        <button type="button" onClick={() => setDialog('moderation')} className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl border border-white/10 px-3 text-xs font-semibold text-text-mid hover:text-text-hi">
                          <ShieldCheck size={14} aria-hidden="true" /> Moderation
                        </button>
                      )}
                      {user && forum?.can_post && (
                        <button type="button" onClick={() => { setView('discussions'); setIsCreatingThread(!isCreatingThread) }} className="inline-flex min-h-[40px] items-center gap-2 rounded-xl bg-gradient-to-r from-gold-soft to-gold px-4 text-xs font-bold text-ink">
                          {isCreatingThread ? <X size={15} aria-hidden="true" /> : <MessageSquarePlus size={15} aria-hidden="true" />}
                          {isCreatingThread ? 'Cancel' : 'New thread'}
                        </button>
                      )}
                    </div>
                  </div>
                  {forum?.following && forum.follow && (
                    <div className="mt-2 flex flex-wrap gap-3 text-xs text-text-mid">
                      <label className="flex items-center gap-1.5">
                        <input type="checkbox" className="accent-[#E5B556]" checked={forum.follow.notify_new} onChange={(e) => void kaluta.forums.follow(forum.id, { notify_new: e.target.checked }).then(() => loadForum(forum.id))} />
                        Tell me about each new thread
                      </label>
                      <label className="flex items-center gap-1.5">
                        <input type="checkbox" className="accent-[#E5B556]" checked={forum.follow.digest} onChange={(e) => void kaluta.forums.follow(forum.id, { digest: e.target.checked }).then(() => loadForum(forum.id))} />
                        Weekly digest
                      </label>
                    </div>
                  )}
                  {forum?.rules && (
                    <details className="mt-3 rounded-xl border border-white/10 px-3 py-2">
                      <summary className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-text-mid">
                        <ScrollText size={13} aria-hidden="true" /> Forum rules
                      </summary>
                      <p className="mt-1 whitespace-pre-wrap text-xs text-text-mid">{forum.rules}</p>
                    </details>
                  )}
                  {forum && forum.sessions.length > 0 && (
                    <ul className="mt-3 space-y-1.5">
                      {forum.sessions.map((s) => (
                        <li key={s.id} className="rounded-xl border border-sky-400/25 bg-sky-500/5 px-3 py-2 text-xs">
                          <span className="font-semibold text-sky">{s.open ? 'Open now: ' : 'Expert session: '}{s.title}</span>
                          <span className="block text-text-mid">
                            with {s.expert?.display_name ?? 'an expert'} · {new Date(s.starts_at).toLocaleString()} – {new Date(s.ends_at).toLocaleString()}
                          </span>
                          {s.expert_intro && <span className="block text-text-low">Introduced by the organiser: {s.expert_intro}</span>}
                          <button type="button" onClick={() => void kaluta.forums.threads(forum.id, 'recent', { session_id: s.id }).then((r) => setThreads(r.items))} className="mt-0.5 font-semibold text-gold-soft underline">
                            See its questions
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                  {!user && <p className="mt-2 text-xs text-text-low">Sign in to ask, answer or follow.</p>}
                  {user && forum && !forum.can_post && <p className="mt-2 text-xs text-text-low">In this forum only some members can start threads.</p>}

                  <div role="group" aria-label="Forum sections" className="mt-4 flex gap-2">
                    {(
                      [
                        { id: 'discussions', label: 'Discussions', icon: MessageSquare },
                        { id: 'knowledge', label: 'Knowledge', icon: BookOpen },
                      ] as const
                    ).map(({ id, label, icon: Icon }) => (
                      <button key={id} type="button" aria-pressed={view === id} onClick={() => setView(id)} className={cn('inline-flex min-h-[44px] items-center gap-2 rounded-xl border px-4 text-xs font-semibold', view === id ? 'border-gold/60 bg-gold/15 text-gold' : 'border-white/10 bg-white/4 text-text-mid hover:text-text-hi')}>
                        <Icon size={14} aria-hidden="true" />
                        {label}
                      </button>
                    ))}
                  </div>

                  {isCreatingThread && forum && (
                    <ThreadComposer
                      forum={forum}
                      onCancel={() => setIsCreatingThread(false)}
                      onOpenThread={(id) => void openThread(id)}
                      onPosted={(id) => {
                        setIsCreatingThread(false)
                        void fetchThreads(forum.id, sort)
                        void loadForum(forum.id)
                        void openThread(id)
                      }}
                    />
                  )}

                  <div className={cn('mt-4 space-y-2', view !== 'discussions' && 'hidden')}>
                    <div className="flex gap-2">
                      <div className="relative flex-1">
                        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-low" aria-hidden="true" />
                        <input type="search" value={threadQuery} onChange={(e) => setThreadQuery(e.target.value)} placeholder="Search this forum" aria-label="Search this forum" className={cn(inputClass, 'py-2 pl-8 text-xs')} />
                      </div>
                      <button type="button" aria-expanded={showFilters} onClick={() => setShowFilters((v) => !v)} className="inline-flex min-h-[40px] items-center gap-1 rounded-xl border border-white/10 px-3 text-xs font-semibold text-text-mid">
                        <SlidersHorizontal size={13} aria-hidden="true" /> Filters
                      </button>
                    </div>
                    {showFilters && (
                      <div className="flex flex-wrap gap-1.5">
                        <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value as ThreadKind | '')} aria-label="Type" className="rounded-full border border-white/10 bg-transparent px-2.5 py-1 text-xs text-text-mid">
                          <option value="">Every type</option>
                          {(Object.keys(KIND) as ThreadKind[]).map((k) => <option key={k} value={k}>{KIND[k].label}</option>)}
                        </select>
                        <select value={langFilter} onChange={(e) => setLangFilter(e.target.value)} aria-label="Language" className="rounded-full border border-white/10 bg-transparent px-2.5 py-1 text-xs text-text-mid">
                          {LANG_FILTERS.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                        </select>
                        <input value={countryFilter} onChange={(e) => setCountryFilter(e.target.value.toUpperCase().slice(0, 2))} placeholder="Country (CI…)" aria-label="Country code" className="w-28 rounded-full border border-white/10 bg-transparent px-2.5 py-1 text-xs text-text-mid" />
                        <input value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} placeholder="Topic" aria-label="Topic" className="w-28 rounded-full border border-white/10 bg-transparent px-2.5 py-1 text-xs text-text-mid" />
                        {forum?.tags?.map((t) => (
                          <button key={t} type="button" onClick={() => setTagFilter(t)} className="rounded-full bg-white/6 px-2 py-1 text-[11px] text-text-mid">#{t}</button>
                        ))}
                      </div>
                    )}
                    <div role="group" aria-label="Sort discussions" className="flex gap-2 overflow-x-auto pb-1">
                      {SORT_TABS.map((tab) => (
                        <button key={tab.id} type="button" aria-pressed={sort === tab.id} onClick={() => changeSort(tab.id)} className={cn('inline-flex min-h-[40px] shrink-0 items-center rounded-full border px-4 text-xs font-semibold', sort === tab.id ? 'border-gold/60 bg-gold/15 text-gold' : 'border-white/10 bg-white/4 text-text-mid hover:text-text-hi')}>
                          {tab.label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>

                {view === 'knowledge' && <KnowledgePanel forumId={forumId} onOpenThread={(id) => { setView('discussions'); void openThread(id) }} />}

                <div className={cn('space-y-3', view !== 'discussions' && 'hidden')}>
                  {threadsLoading && threads.length === 0 && <div role="status" className="cloud-card p-8 text-center text-sm text-text-low">Loading discussions…</div>}
                  {!threadsLoading && threads.length === 0 && (
                    <div className="cloud-card p-8 text-center text-sm text-text-low">
                      {threadQuery || kindFilter || langFilter || countryFilter || tagFilter ? 'Nothing matches these filters.' : SORT_TABS.find((tab) => tab.id === sort)?.empty}
                    </div>
                  )}
                  {threads.map((thread) => (
                    <article key={thread.id} aria-busy={threadsLoading}>
                      <ThreadRow thread={thread} onOpen={() => void openThread(thread.id)} />
                    </article>
                  ))}
                </div>
              </div>
            )}

            {open && (isDesktop || showMobileThread) && (
              <ThreadView
                thread={open}
                forums={forums}
                userId={user?.id ?? null}
                canTranslate={canTranslate}
                locale={locale}
                backLabel={`Back to ${forum?.name ?? activeForum?.name ?? 'the forum'}`}
                onBack={backToThreads}
                onReload={reloadOpen}
                onOpenThread={(id) => void openThread(id)}
              />
            )}
          </main>
        </div>
      </div>

      {error && (
        <div role="alert" className="mt-4 rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
          {error}
        </div>
      )}
      {dialog === 'prefs' && <ForumPrefsDialog onClose={() => setDialog(null)} />}
      {dialog === 'settings' && forum && (
        <ForumSettingsDialog
          forum={forum}
          onClose={() => setDialog(null)}
          onChanged={() => {
            void loadForum(forum.id)
            void loadForums()
          }}
        />
      )}
      {dialog === 'moderation' && forum && <ModerationDialog forumId={forum.id} onClose={() => setDialog(null)} onOpenThread={(id) => void openThread(id)} />}
      {review && (
        <Sheet title="Ask for a review" onClose={() => { setReview(null); setParams({}, { replace: true }) }}>
          <p className="text-sm text-text-mid">A moderator hid something you posted, for the reason given in your notification. Tell them why you think it should be shown again; they will answer you.</p>
          <textarea value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} rows={4} maxLength={2000} aria-label="Why it should be shown again" className={`${field} mt-2 resize-none`} />
          <button
            type="button"
            disabled={!reviewNote.trim()}
            onClick={() => {
              const [kind, id] = review.split(':')
              void kaluta.forums
                .requestReview(kind as 'thread' | 'reply', id, reviewNote.trim())
                .then(() => {
                  setReview(null)
                  setParams({}, { replace: true })
                  setError(null)
                })
                .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not send the request'))
            }}
            className="mt-3 rounded-full bg-gradient-to-br from-gold-soft to-gold px-5 py-2 text-sm font-bold text-ink disabled:opacity-50"
          >
            Send to the moderators
          </button>
        </Sheet>
      )}
    </AppShell>
  )
}
