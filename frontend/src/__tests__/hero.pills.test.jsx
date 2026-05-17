import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'

import HeroStrip from '../components/HeroStrip.jsx'
import styles from '../components/HeroStrip.module.css'

describe('Hero pill labels/classes', () => {
  it('MEAN_REVERTING renders blue pill', () => {
    render(<HeroStrip status={{ cache_ready: true, regime: 'MEAN_REVERTING' }} />)
    const pill = screen.getByText('MEAN_REVERTING')
    expect(pill).toHaveClass(styles.pillBlue)
  })

  it('VOLATILITY_SHOCK renders orange pill', () => {
    render(<HeroStrip status={{ cache_ready: true, regime: 'VOLATILITY_SHOCK' }} />)
    const pill = screen.getByText('VOLATILITY_SHOCK')
    expect(pill).toHaveClass(styles.pillOrange)
  })

  it('NO_TRADE renders grey pill', () => {
    render(<HeroStrip status={{ cache_ready: true, regime: 'NO_TRADE' }} />)
    const pill = screen.getByText('NO_TRADE')
    expect(pill).toHaveClass(styles.pillGrey)
  })

  it('cache_ready=false renders PENDING muted/grey pill', () => {
    render(<HeroStrip status={{ cache_ready: false, regime: 'MEAN_REVERTING' }} />)
    const pill = screen.getByText('PENDING')
    expect(pill).toHaveClass(styles.pillMuted)
  })
})
