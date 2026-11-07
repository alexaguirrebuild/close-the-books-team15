import '@fontsource-variable/inter-tight'
import '@fontsource-variable/jetbrains-mono'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './styles/base.css'
import './styles/screens.css'
import './styles/bench.css'

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
