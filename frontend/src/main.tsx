import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import '@fontsource-variable/sora'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import './base.css'
import './theme.css'
import { App } from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
