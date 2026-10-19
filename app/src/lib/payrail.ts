/**
 * The rails paid on Kinjy's own page: Kaluta Pay (every payment since 19/10)
 * and Sonco Pay directly. Checked here once, so a new rail is not forgotten in
 * one of the screens that pay - which sent members to a hosted page instead.
 */
export function inPage(rail: string | null | undefined): boolean {
  return rail === 'kalutapay' || rail === 'soncopay'
}
