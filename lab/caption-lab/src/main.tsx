import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import CaptionLab from './lab/CaptionLab.tsx'
import './lab/lab.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CaptionLab />
  </StrictMode>,
)
