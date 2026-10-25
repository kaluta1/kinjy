import type { AdminMemberBrief, AdminPaymentBrief } from '@/lib/api'

/**
 * A member as an admin needs to recognise them: name, @handle and e-mail,
 * with the account id kept small for support tickets. Falls back to the id
 * while names load, and says so when the account no longer exists.
 */
export function Member({
  id,
  members,
  inline = false,
}: {
  id: string | null | undefined
  members: Record<string, AdminMemberBrief> | null
  inline?: boolean
}) {
  if (!id) return <span className="text-text-low">—</span>
  const m = members?.[id]
  if (!m) {
    return (
      <span className="break-all text-text-mid" title={id}>
        {members === null ? id : `Deleted account · ${id}`}
      </span>
    )
  }
  if (inline) {
    return (
      <span className="text-text-hi" title={`${m.email} · ${id}`}>
        {m.display_name} <span className="text-text-low">@{m.handle}</span>
      </span>
    )
  }
  return (
    <span className="block min-w-0">
      <span className="block truncate text-sm font-semibold text-text-hi">
        {m.display_name} <span className="font-normal text-text-low">@{m.handle}</span>
      </span>
      <span className="block truncate text-xs text-text-low" title={id}>
        {m.email} · {id}
      </span>
    </span>
  )
}

/** Where to find a payment at the provider: its name and the provider's id. */
export function ProviderRef({ payment }: { payment: AdminPaymentBrief | null | undefined }) {
  if (!payment) return null
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-xs">
      <span className="rounded-full bg-gold/15 px-2 py-0.5 font-semibold text-gold-soft">{payment.rail_name}</span>
      {payment.external_id ? (
        <span className="mono-data select-all break-all text-text-hi">{payment.external_id}</span>
      ) : (
        <span className="text-text-low">no provider id</span>
      )}
      <span className="text-text-low">· {payment.status}</span>
    </span>
  )
}
