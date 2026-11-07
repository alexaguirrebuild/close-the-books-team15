import '@fontsource-variable/ibm-plex-sans'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles/tokens.css'
import './styles/base.css'
import './styles/shell.css'
import './styles/review.css'
import './styles/case.css'
import './styles/decision.css'
import './styles/overlays.css'
import './styles/summary.css'
import './styles/export.css'
import './styles/load.css'

if (import.meta.env.DEV) {
  void import('./store').then(({ useStore }) => {
    ;(window as unknown as { __cuadra: unknown }).__cuadra = useStore
  })
}

const root = document.getElementById('root')
if (!root) throw new Error('Falta el elemento raíz')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
