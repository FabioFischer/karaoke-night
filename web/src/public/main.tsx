// The public build's entry (public.html): only /karaoke, laid out for a phone.
// The operator's screens are never imported here, so they are not in the bundle.

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import '../index.css'
import './public.css'
import { PhoneScreen } from './PhoneScreen'
import { PublicStateProvider } from './public-state'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <PublicStateProvider>
        <Routes>
          <Route path="/karaoke" element={<PhoneScreen />} />
          <Route path="*" element={<Navigate to="/karaoke" replace />} />
        </Routes>
      </PublicStateProvider>
    </BrowserRouter>
  </StrictMode>,
)
