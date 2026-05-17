import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'

const mockGetToken = vi.fn()
const mockAuthFetch = vi.fn()

vi.mock('../api.js', async () => {
  const actual = await vi.importActual('../api.js')
  return {
    ...actual,
    getToken: (...args) => mockGetToken(...args),
    authFetch: (...args) => mockAuthFetch(...args),
  }
})

import App from '../App.jsx'

describe('Auth redirects', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAuthFetch.mockResolvedValue({
      cache_ready: true,
      regime: 'MEAN_REVERTING',
      nifty_price: 24000,
      vix_price: 12,
      h_idx: 0.4,
      h_vix: 0.4,
    })
  })

  it('unauthenticated visit to /dashboard redirects to /login', async () => {
    mockGetToken.mockReturnValue(null)
    window.history.pushState({}, '', '/dashboard')
    render(<App />)
    await waitFor(() => {
      expect(window.location.pathname).toBe('/login')
    })
  })

  it('authenticated visit to /login redirects to /dashboard', async () => {
    mockGetToken.mockReturnValue('jwt-token')
    window.history.pushState({}, '', '/login')
    render(<App />)
    await waitFor(() => {
      expect(window.location.pathname).toBe('/dashboard')
    })
  })
})
