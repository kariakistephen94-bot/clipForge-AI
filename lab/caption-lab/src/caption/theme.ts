// Visual theme: fonts, palette, stroke, shadow, plate. Kept apart from behaviour (segmentation, importance,
// animation, layout) so a brand theme can be combined with any preset's behaviour.

import type { CaptionConfig } from './types.ts'

export type CaptionTheme = Pick<CaptionConfig, 'typography' | 'style' | 'colors'>

/** Fonts the lab offers. Google-hosted ones load in index.html; system fallbacks keep it working offline. */
export const FONT_CHOICES: { label: string; family: string; weights: number[] }[] = [
  { label: 'Montserrat', family: '"Montserrat", "Arial Black", sans-serif', weights: [700, 800, 900] },
  { label: 'Inter', family: '"Inter", "Helvetica Neue", Arial, sans-serif', weights: [600, 700, 800, 900] },
  { label: 'Poppins', family: '"Poppins", "Avenir Next", sans-serif', weights: [600, 700, 800, 900] },
  { label: 'Anton', family: '"Anton", Impact, sans-serif', weights: [400] },
  { label: 'Archivo Black', family: '"Archivo Black", "Arial Black", sans-serif', weights: [400] },
  { label: 'Bebas Neue', family: '"Bebas Neue", Impact, sans-serif', weights: [400] },
  { label: 'Arial Black (system)', family: '"Arial Black", sans-serif', weights: [900] },
  { label: 'Coolvetica (local)', family: '"Coolvetica Hv Comp", "Coolvetica", Impact, sans-serif', weights: [400] },
]

export const DEFAULT_THEME: CaptionTheme = {
  typography: {
    fontFamily: FONT_CHOICES[0].family,
    fontWeight: 900,
    baseSize: 76,
    scales: { normal: 1, medium: 1.15, strong: 1.4, hero: 2.1 },
    lineHeight: 1.02,
    letterSpacing: -0.02,
    textCase: 'upper',
  },
  style: {
    strokeWidth: 0,
    strokeColor: '#000000',
    shadowColor: '#000000',
    shadowBlur: 18,
    shadowOpacity: 0.55,
    shadowOffsetY: 5,
    plate: 'none',
    plateColor: '#000000',
    plateOpacity: 0.55,
    plateRadius: 18,
    platePadding: 16,
  },
  colors: {
    primary: '#FFFFFF',
    active: '#FFD60A',
    secondary: '#FF8A1F',
    accent: '#3DDC84',
    hero: 'primary',
    strong: 'primary',
    supportingOpacity: 1,
  },
}
