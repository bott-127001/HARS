import React from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import LiveScanTable from '../components/LiveScanTable.jsx'

vi.mock('../api.js', () => ({
  authFetch: vi.fn().mockResolvedValue([]),
}))

vi.mock('../market.js', () => ({ isMarketHoursIST: () => false }))

describe('LiveScanTable info bar', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows the rulebook schedule string', () => {
    render(<LiveScanTable />)
    expect(
      screen.getByText(
        'Pre-market data fetch at 8:45 AM IST. Gap data at 9:18 AM IST. First scan at 9:20:15 AM IST.',
      ),
    ).toBeInTheDocument()
  })
})
