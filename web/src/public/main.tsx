// The public build's entry (public.html): only /karaoke, laid out for a phone.
// The operator's screens are never imported here, so they are not in the bundle.

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router'
import { ErrorWordingContext } from '../components/ErrorNote'
import '../index.css'
import { phoneErrorMessage } from '../text'
import './public.css'
import { PhoneScreen } from './PhoneScreen'
import { PublicStateProvider } from './public-state'

const PHONE_WORDING = { message: phoneErrorMessage, showDetail: false }

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <ErrorWordingContext.Provider value={PHONE_WORDING}>
        <PublicStateProvider>
          <Routes>
            <Route path="/karaoke" element={<PhoneScreen />} />
            <Route path="*" element={<Navigate to="/karaoke" replace />} />
          </Routes>
        </PublicStateProvider>
      </ErrorWordingContext.Provider>
    </BrowserRouter>
  </StrictMode>,
)
