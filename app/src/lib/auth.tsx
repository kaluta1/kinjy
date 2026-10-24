import { createContext, useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { ApiError, kaluta, tokens, type AuthUser } from '@/lib/api'

export interface AuthState {
  user: AuthUser | null
  /** True until the stored token has been checked against the API once. */
  loading: boolean
  signIn: (email: string, password: string) => Promise<AuthUser>
  signUp: (input: {
    email: string
    password: string
    display_name: string
    handle: string
    date_of_birth: string
    country?: string
    lang?: string
    referral_code?: string
    referral_link?: string
  }) => Promise<AuthUser>
  signOut: () => Promise<void>
  refresh: () => Promise<void>
}

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    if (!tokens.access) {
      setUser(null)
      setLoading(false)
      return
    }
    try {
      setUser(await kaluta.auth.me())
    } catch (err) {
      // A 401 here means the stored pair is dead (the client already tried to
      // refresh it). Anything else — a service being down — must NOT sign the
      // member out; they would lose their session over a transient blip.
      if (err instanceof ApiError && err.status === 401) {
        tokens.clear()
        setUser(null)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const signIn = useCallback(async (email: string, password: string) => {
    const signedIn = await kaluta.auth.login(email, password)
    setUser(signedIn)
    return signedIn
  }, [])

  const signUp = useCallback<AuthState['signUp']>(async (input) => {
    const created = await kaluta.auth.register(input)
    setUser(created)
    return created
  }, [])

  const signOut = useCallback(async () => {
    await kaluta.auth.logout()
    setUser(null)
  }, [])

  const value = useMemo<AuthState>(
    () => ({ user, loading, signIn, signUp, signOut, refresh }),
    [user, loading, signIn, signUp, signOut, refresh],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
