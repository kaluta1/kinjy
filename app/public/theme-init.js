// Applies the remembered display mode before the app's JavaScript runs, so a
// page does not paint in the other palette first. Mirrors AppThemeProvider:
// `kaluta.display_mode` is light, cloud (dark) or system; no choice follows the
// device. AppThemeProvider takes over once React has started.
;(function () {
  var mode = 'system'
  try {
    var stored = localStorage.getItem('kaluta.display_mode')
    if (stored === 'dark') stored = 'cloud'
    if (stored === 'light' || stored === 'cloud' || stored === 'system') mode = stored
  } catch (e) {
    /* storage switched off: follow the device */
  }
  if (mode === 'system') {
    mode = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'cloud' : 'light'
  }
  document.documentElement.dataset.theme = mode
})()
