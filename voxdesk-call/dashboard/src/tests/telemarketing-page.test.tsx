import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TelemarketingPage } from '../pages/product/telemarketing/TelemarketingPage'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('public outbound campaign overview', () => {
  it('renders without inventing operational records or requiring an API fixture', () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    render(<TelemarketingPage />)

    expect(screen.getByRole('heading', { name: 'Plan campaign work. Keep live dialing under operator control.' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open campaign console' })).toHaveAttribute('href', '/campaigns')
    expect(screen.getByRole('link', { name: 'Explore voice agents' })).toHaveAttribute('href', '/product/voice-agents')
    expect(screen.getByText(/does not display sample customers, fabricated call totals, or provider connection badges/)).toBeInTheDocument()
    expect(screen.getByText(/The campaign API defaults to dry-run mode/)).toBeInTheDocument()
    expect(screen.queryByText(/Verified|Real Backend|John Smith|1,243/)).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
