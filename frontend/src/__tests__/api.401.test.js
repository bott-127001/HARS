import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { authFetch, clearToken, setToken } from '../api.js'

describe('authFetch 401', () => {
  const originalLocation = window.location

  beforeEach(() => {
    setToken('fake')
    delete window.location
    window.location = { ...originalLocation, href: '' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }))
  })

  afterEach(() => {
    window.location = originalLocation
    vi.unstubAllGlobals()
    clearToken()
  })

  it('clears token and navigates to login', async () => {
    await expect(authFetch('/api/status')).rejects.toThrow()
    expect(localStorage.getItem('hars_jwt')).toBeNull()
    expect(window.location.href.endsWith('/login')).toBe(true)
  })
})
