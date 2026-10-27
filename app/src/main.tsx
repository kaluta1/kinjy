import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './index.css'
import './i18n'
import App from './App.tsx'
import { AuthProvider } from './lib/auth'
import { AppThemeProvider } from './components/appdemo/theme'
import { reloadForUpdate } from './components/app/RouteErrorBoundary'
import { installSingleAudio } from './lib/singleAudio'

// A tab opened before a deployment asks for page files that no longer exist.
// Vite reports it here before React sees it: reload once for the new version.
window.addEventListener('vite:preloadError', (event) => {
  if (reloadForUpdate()) event.preventDefault()
})

// Two players of the same video must never be heard at once (an echo).
installSingleAudio()

createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <AuthProvider>
      {/* persist: the member's display mode and language survive navigation and
          reloads. The /app demo nests its own throwaway provider. */}
      <AppThemeProvider persist initialMode="system">
        <App />
      </AppThemeProvider>
    </AuthProvider>
  </BrowserRouter>,
)
