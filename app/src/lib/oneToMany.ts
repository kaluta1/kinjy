import { Clapperboard, Languages, Newspaper } from 'lucide-react'
import type { OneToManyTarget } from '@/lib/api'

/** The formats One-to-Many makes from a post, in the order they are shown. */
export const ONE_TO_MANY_OPTIONS: Array<{ id: OneToManyTarget; icon: typeof Clapperboard; label: string }> = [
  { id: 'short_video', icon: Clapperboard, label: 'Short video' },
  { id: 'newsletter', icon: Newspaper, label: 'Newsletter' },
  { id: 'translation', icon: Languages, label: 'Translate ×4' },
]
