import React from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import LiveScanTable from '../components/LiveScanTable.jsx'

vi.mock('../api.js', () => ({
  authFetch: vi.fn().mockResolvedValue([
    {
      symbol: 'SIG',
      rvol: 2.5,
      atr_pct: 5.0,
      gap_pct: 1.0,
      momentum_15m: 0.1,
      compliance_score: 2,
      result: 'SIGNAL',
      signal_time: '09:25 IST',
    },
    {
      symbol: 'W1',
      rvol: 3.0,
      atr_pct: 4.0,
      gap_pct: 0.5,
      momentum_15m: 0.2,
      compliance_score: 3,
      result: 'WATCH',
      signal_time: null,
    },
    {
      symbol: 'W2',
      rvol: 2.8,
      atr_pct: 3.5,
      gap_pct: 0.4,
      momentum_15m: 0.15,
      compliance_score: 2,
      result: 'WATCH',
      signal_time: null,
    },
  ]),
}))

vi.mock('../market.js', () => ({ isMarketHoursIST: () => false }))

describe('LiveScanTable signal time column', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shows signal_time in accent blue for SIGNAL rows and em-dash for others', async () => {
    render(<LiveScanTable />)

    const signalCell = await screen.findByText('09:25 IST')
    expect(signalCell).toBeInTheDocument()
    expect(signalCell.className).toMatch(/resSignal/)

    const w1Row = screen.getByText('W1').closest('tr')
    expect(w1Row).not.toBeNull()
    const w1Cells = w1Row.querySelectorAll('td')
    const signalTimeCell = w1Cells[w1Cells.length - 1]
    expect(signalTimeCell.textContent).toBe('—')
    expect(signalTimeCell.className).toMatch(/resGrey/)
  })
})
