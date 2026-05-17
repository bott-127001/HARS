import { act, render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mockAuthFetch = vi.fn()
const mockIsMarketHoursIST = vi.fn()

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js')
  return {
    ...actual,
    authFetch: (...args) => mockAuthFetch(...args),
  }
})

vi.mock('../market.js', async () => {
  const actual = await vi.importActual('../market.js')
  return {
    ...actual,
    isMarketHoursIST: (...args) => mockIsMarketHoursIST(...args),
  }
})

import LiveScanTable from '../components/LiveScanTable.jsx'
import TradeHistoryTable from '../components/TradeHistoryTable.jsx'

describe('Polling cadence', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue([])
  })

  it('/api/scan polls every 30s when market is open', async () => {
    mockIsMarketHoursIST.mockReturnValue(true)
    render(<LiveScanTable />)
    await act(async () => Promise.resolve())
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/scan')

    await act(async () => {
      vi.advanceTimersByTime(30_000)
      await Promise.resolve()
    })
    expect(mockAuthFetch).toHaveBeenCalledTimes(2)
  })

  it('/api/scan polls every 5 minutes when market is closed', async () => {
    mockIsMarketHoursIST.mockReturnValue(false)
    render(<LiveScanTable />)
    await act(async () => Promise.resolve())
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/scan')

    await act(async () => {
      vi.advanceTimersByTime(300_000)
      await Promise.resolve()
    })
    expect(mockAuthFetch).toHaveBeenCalledTimes(2)
  })

  it('/api/history polls every 60s', async () => {
    render(<TradeHistoryTable />)
    await act(async () => Promise.resolve())
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/history')

    await act(async () => {
      vi.advanceTimersByTime(60_000)
      await Promise.resolve()
    })
    expect(mockAuthFetch).toHaveBeenCalledTimes(2)
  })
})
