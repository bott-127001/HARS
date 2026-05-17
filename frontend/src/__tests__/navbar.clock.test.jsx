import { act, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import Navbar from '../components/Navbar.jsx'

describe('Navbar clock', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-10T09:30:00.000Z'))
  })

  it('shows HH:MM:SS AM/PM IST format and updates every second', async () => {
    let tickCb = null
    vi.spyOn(globalThis, 'setInterval').mockImplementation((cb, _ms) => {
      tickCb = cb
      return 1
    })
    vi.spyOn(globalThis, 'clearInterval').mockImplementation(() => {})

    render(<Navbar onLogout={() => {}} />)
    const first = screen.getByText(/\d{2}:\d{2}:\d{2}\s(?:am|pm|AM|PM)\sIST/)
    const firstText = first.textContent
    expect(setInterval).toHaveBeenCalledWith(expect.any(Function), 1000)
    expect(tickCb).toBeTypeOf('function')

    await act(async () => {
      vi.setSystemTime(new Date('2026-05-10T09:30:01.000Z'))
      tickCb()
    })

    const second = screen.getByText(/\d{2}:\d{2}:\d{2}\s(?:am|pm|AM|PM)\sIST/)
    expect(second.textContent).not.toBe(firstText)
  })
})
