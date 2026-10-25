import { useApi } from '@/hooks/useApi'
import { kaluta, type AdminMemberBrief, type AdminPaymentBrief } from '@/lib/api'

const unique = (ids: Array<string | null | undefined>) =>
  [...new Set(ids.filter((id): id is string => Boolean(id)))].sort()

/** Names for the account ids on a screen, in one request. */
export function useMembers(ids: Array<string | null | undefined>): Record<string, AdminMemberBrief> | null {
  const list = unique(ids)
  const key = list.join(',')
  const res = useApi(
    () => (list.length ? kaluta.adminConsole.lookupUsers(list) : Promise.resolve({ users: {} })),
    [key],
  )
  return res.data?.users ?? (list.length ? null : {})
}

/** The payments behind journal references (pmt_…), with the provider's id. */
export function usePayments(ids: Array<string | null | undefined>): Record<string, AdminPaymentBrief> {
  const list = unique(ids).filter((id) => id.startsWith('pmt_'))
  const key = list.join(',')
  const res = useApi(
    () => (list.length ? kaluta.adminConsole.lookupPayments(list) : Promise.resolve({ payments: {} })),
    [key],
  )
  return res.data?.payments ?? {}
}
