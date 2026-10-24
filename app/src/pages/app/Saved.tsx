import { useState } from 'react'
import AppShell from '@/components/app/AppShell'
import PostCard from '@/components/social/PostCard'
import { PanelState } from '@/components/dashboard/primitives'
import { useApi } from '@/hooks/useApi'
import { useAuth } from '@/hooks/useAuth'
import { kaluta } from '@/lib/api'

/**
 * The posts a member saved to read again. Private to them; a post they can no
 * longer read (they left its community, it was removed) is simply not listed.
 */
export default function Saved() {
  const { user } = useAuth()
  const [offset, setOffset] = useState(0)
  const saved = useApi(() => kaluta.posts.bookmarks(offset), [offset])
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const items = (saved.data?.items ?? []).filter((p) => !hidden.has(p.id))

  return (
    <AppShell>
      <header className="mb-6">
        <h1 className="text-[clamp(32px,4.5vw,48px)] font-bold leading-[1.05] tracking-[-0.04em] text-text-hi">Saved</h1>
        <p className="mt-2 text-[0.95rem] text-text-low">Posts you saved to read again. Only you see this list.</p>
      </header>
      <PanelState
        loading={saved.loading && !saved.data}
        error={saved.error}
        empty={items.length === 0}
        emptyLabel="Nothing saved yet. Use Save under a post to keep it here."
      >
        <div className="space-y-4">
          {items.map((post) => (
            <PostCard
              key={post.id}
              post={post}
              algorithmId="chronological"
              mode="new"
              isOwn={post.author_id === user?.id}
              currentUserId={user?.id ?? ''}
              onHidden={(id) => setHidden((h) => new Set(h).add(id))}
              onChangeAlgorithm={() => {}}
            />
          ))}
        </div>
      </PanelState>
      {(saved.data?.items.length ?? 0) === 20 && (
        <button type="button" onClick={() => setOffset(offset + 20)}
          className="mt-4 rounded-full border border-[var(--cloud-border)] px-4 py-2 text-sm font-semibold text-text-mid hover:text-text-hi">
          Older
        </button>
      )}
    </AppShell>
  )
}
