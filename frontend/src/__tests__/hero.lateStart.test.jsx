import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

import HeroStrip from '../components/HeroStrip.jsx'

const BANNER_TEXT =
  'Pre-market window missed — Hurst computed late. Signals valid but treat with caution.'

describe('late_start banner', () => {
  it('shows warning when late_start is true', () => {
    render(
      <HeroStrip
        status={{
          cache_ready: true,
          regime: 'MEAN_REVERTING',
          late_start: true,
        }}
      />,
    )
    expect(screen.getByText(new RegExp(BANNER_TEXT))).toBeTruthy()
  })

  it('does not render banner when late_start is false', () => {
    render(
      <HeroStrip
        status={{
          cache_ready: true,
          regime: 'MEAN_REVERTING',
          late_start: false,
        }}
      />,
    )
    expect(screen.queryByText(new RegExp(BANNER_TEXT))).toBeNull()
  })
})
