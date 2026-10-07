import { describe, expect, it } from 'vitest'
import { isProtectedPath, matchRoute } from '../app/router'

describe('Prompt 8 direct product and console route mappings', () => {
  it.each([
    ['/phone-numbers', 'PhoneNumbersPage'],
    ['/dashboard/phone-numbers', 'PhoneNumbersPage'],
    ['/app/phone-numbers', 'PhoneNumbersPage'],
  ])('%s resolves to the authenticated phone-number console', (path, component) => {
    const match = matchRoute(path)
    expect(match?.route.component).toBe(component)
    expect(isProtectedPath(path)).toBe(true)
  })

  it.each([
    ['/campaigns', 'LegacyCampaignsPage', '/campaigns'],
    ['/settings', 'LegacySettingsPage', '/security-settings'],
    ['/billing', 'LegacyBillingPage', '/billing'],
  ])('%s bridges to its operator-console hash route', (path, component, legacyPath) => {
    const match = matchRoute(path)
    expect(match?.route.component).toBe(component)
    expect(match?.route.legacyPath).toBe(legacyPath)
    expect(isProtectedPath(path)).toBe(true)
  })

  it.each([
    ['/calls', {}],
    ['/calls/call-001', { id: 'call-001' }],
    ['/dashboard/calls', {}],
    ['/dashboard/calls/call-001', { id: 'call-001' }],
  ])('%s resolves to the operator call console', (path, params) => {
    const match = matchRoute(path)
    expect(match?.route.component).toBe('CallLogConsole')
    expect(match?.params).toMatchObject(params)
    expect(isProtectedPath(path)).toBe(true)
  })

  it('keeps the public analytics overview separate from protected data-backed analytics', () => {
    const publicAnalytics = matchRoute('/product/analytics')
    const dashboardAnalytics = matchRoute('/dashboard/analytics')
    const publicStatus = matchRoute('/status')

    expect(publicAnalytics?.route.component).toBe('AnalyticsPage')
    expect(isProtectedPath('/product/analytics')).toBe(false)
    expect(dashboardAnalytics?.route.component).toBe('AnalyticsPage')
    expect(dashboardAnalytics?.route.legacyPath).toBe('/analytics')
    expect(isProtectedPath('/dashboard/analytics')).toBe(true)
    expect(publicStatus?.route.component).toBe('StatusPage')
    expect(isProtectedPath('/status')).toBe(false)
  })

  it('keeps public metadata aligned with pages that disclose missing verified content', () => {
    const careers = matchRoute('/careers')
    const team = matchRoute('/team')
    const blog = matchRoute('/blog')
    const article = matchRoute('/blog/no-verified-article')
    const privacy = matchRoute('/privacy')

    expect(careers?.route.description).toMatch(/No verified job openings/i)
    expect(team?.route.description).toMatch(/No named leadership roster/i)
    expect(blog?.route.description).toMatch(/No verified .* articles are currently published/i)
    expect(article?.route.description).toMatch(/not generated from a URL slug/i)
    expect(privacy?.route.description).toMatch(/Request approved privacy/i)
  })
})
