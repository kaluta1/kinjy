import { Link, useParams } from 'react-router'
import { ArrowLeft } from 'lucide-react'
import AppShell from '@/components/app/AppShell'
import { EventCard } from '@/components/community/CommunityEvents'
import { useApi } from '@/hooks/useApi'
import { kaluta, type CommunityEvent } from '@/lib/api'

/**
 * One event at its own address (/events/<id>): the link that is shared.
 *
 * The community's rules decide who sees it - the server answers 404 to
 * anybody who may not read that community's events, so a link to a private
 * event shows nothing to a stranger.
 */
export default function EventPage() {
  const { eventId = '' } = useParams()
  const event = useApi<CommunityEvent>(() => kaluta.communityPaid.event(eventId), [eventId])
  const e = event.data

  return (
    <AppShell>
      <div className="mx-auto max-w-2xl space-y-4">
        {e?.community && (
          <Link
            to={`/communities/${e.community.slug}`}
            className="inline-flex items-center gap-1.5 text-sm font-semibold text-text-mid hover:text-text-hi"
          >
            <ArrowLeft size={14} aria-hidden="true" /> {e.community.name}
          </Link>
        )}
        {event.loading && !e && <p className="caption">Loading the event…</p>}
        {event.error && !e && (
          <div className="cloud-card p-6 text-center">
            <p className="text-base font-semibold text-text-hi">This event cannot be opened</p>
            <p className="mt-1 text-sm text-text-low">
              It may have been removed, or it belongs to a community you are not a member of.
            </p>
          </div>
        )}
        {e && (
          <>
            <h1 className="text-[clamp(28px,4vw,40px)] font-bold leading-tight tracking-[-0.035em] text-text-hi">{e.title}</h1>
            <EventCard
              event={e}
              // The server checks membership again when somebody registers.
              member
              steward={Boolean(e.can_edit)}
              linkTitle={false}
              onChanged={event.reload}
            />
          </>
        )}
      </div>
    </AppShell>
  )
}
