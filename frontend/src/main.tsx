import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { I18nProvider, LanguageSwitcher } from './I18n.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <div className="app-shell">
        <header className="app-language-bar"><b>JOJO Director</b><LanguageSwitcher /></header>
        <div className="app-content"><App /></div>
      </div>
    </I18nProvider>
  </StrictMode>,
)
