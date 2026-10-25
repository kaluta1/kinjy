/** Words for an ebook's details, shared by the sell form, the page and the library. */

export const EBOOK_CATEGORY_LABELS: Record<string, string> = {
  business: 'Business',
  education: 'Education',
  fiction: 'Fiction',
  health: 'Health',
  history: 'History',
  religion: 'Religion & spirituality',
  self_help: 'Self-help',
  technology: 'Technology',
  children: 'Children',
  cooking: 'Cooking',
  arts: 'Arts',
  finance: 'Money & finance',
  languages: 'Languages',
  science: 'Science',
  travel: 'Travel',
  other: 'Other',
}

/** The languages offered first; codes are ISO 639. */
export const EBOOK_LANGUAGES: Array<[string, string]> = [
  ['en', 'English'],
  ['fr', 'French'],
  ['sw', 'Swahili'],
  ['ar', 'Arabic'],
  ['pt', 'Portuguese'],
  ['es', 'Spanish'],
  ['de', 'German'],
  ['zh', 'Chinese'],
  ['ha', 'Hausa'],
  ['yo', 'Yoruba'],
  ['ig', 'Igbo'],
  ['am', 'Amharic'],
  ['zu', 'Zulu'],
  ['ln', 'Lingala'],
  ['wo', 'Wolof'],
]

export function languageName(code: string | null | undefined): string | null {
  if (!code) return null
  return EBOOK_LANGUAGES.find(([c]) => c === code)?.[1] ?? code.toUpperCase()
}

export const LICENSE_LABELS: Record<string, string> = {
  personal: 'Personal use: read it, do not share or resell it',
  commercial: 'Commercial use allowed',
}

/** "PDF" works in every browser and reader; EPUB needs an ebook app. */
export const FORMAT_NOTES: Record<string, string> = {
  pdf: 'Opens on any phone, tablet or computer.',
  epub: 'Opens in an ebook app (Apple Books, Google Play Books, Calibre, most e-readers).',
}

/** "2.4 MB", "830 KB". */
export function fileSize(bytes: number | null | undefined): string {
  if (!bytes) return ''
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}
