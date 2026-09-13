import { createHash } from 'node:crypto'
import { test as base, expect, type ConsoleMessage, type Request, type Route } from '@playwright/test'
import {
  courses, parseResponse, planResponse, scraperStatus, sectionList, solveResponse, syntheticPdf,
} from './data'
import type {
  CourseResponse, GenerateResponse, GerCoursesResponse, ParseResponse,
  ProfessorResponse, ScraperStatusResponse, SectionResponse, SolveResponse,
} from '../lib/api'

type Handler = (route: Route) => Promise<void>

type SuccessResponses = {
  GET: {
    '/api/courses': CourseResponse[]
    '/api/scraper/status': ScraperStatusResponse
    '/api/plan/ger-courses': GerCoursesResponse
    [path: `/api/courses/${string}/sections`]: SectionResponse[]
    [path: `/api/professors/${string}`]: ProfessorResponse
  }
  POST: {
    '/api/schedule/solve': SolveResponse
    '/api/plan/parse': ParseResponse
    '/api/plan/generate': GenerateResponse
  }
}
type ErrorStatus = 400 | 401 | 403 | 404 | 408 | 413 | 422 | 429 | 500 | 502 | 503 | 504

class MockApi {
  private readonly overrides = new Map<string, Handler>()
  private readonly received: Request[] = []
  private readonly defaults = new Map<string, Handler>([
    ['GET /api/courses', async (route) => {
      const query = new URL(route.request().url()).searchParams.get('q') || ''
      const normalized = query.toUpperCase().replace(/\s+/g, '')
      await route.fulfill({ json: courses.filter((c) => c.course_code.includes(normalized)) })
    }],
    ...sectionList.map((section): [string, Handler] => [
      `GET /api/courses/${section.course_code}/sections`,
      async (route) => { await route.fulfill({ json: [section] }) },
    ]),
    ...sectionList.map((section): [string, Handler] => [
      `GET /api/professors/${section.professor_name}`,
      async (route) => { await route.fulfill({ status: 404, json: { detail: 'Professor not found in RMP cache.' } }) },
    ]),
    ['GET /api/scraper/status', async (route) => {
      await route.fulfill({ json: scraperStatus })
    }],
    ['POST /api/schedule/solve', async (route) => { await route.fulfill({ json: solveResponse }) }],
    ['POST /api/plan/parse', async (route) => {
      await route.fulfill({ json: {
        ...parseResponse,
        server_hash: createHash('sha256').update(syntheticPdf.buffer).digest('hex'),
      } })
    }],
    ['POST /api/plan/generate', async (route) => { await route.fulfill({ json: planResponse }) }],
  ])

  // Use handle() for delayed responses/race regressions in later goals.
  handle(method: string, pathname: string, handler: Handler) {
    this.overrides.set(`${method} ${pathname}`, handler)
  }

  respond<M extends keyof SuccessResponses, P extends keyof SuccessResponses[M] & string>(
    method: M, pathname: P, json: SuccessResponses[M][P], status?: 200,
  ): void
  respond(method: string, pathname: string, json: { detail: unknown }, status: ErrorStatus): void
  respond(method: string, pathname: string, json: unknown, status = 200) {
    this.handle(method, pathname, async (route) => { await route.fulfill({ status, json }) })
  }

  reset(method: string, pathname: string) {
    this.overrides.delete(`${method} ${pathname}`)
  }

  requests(method: string, pathname: string) {
    return this.received.filter((r) => r.method() === method && decodeURIComponent(new URL(r.url()).pathname) === pathname)
  }

  async dispatch(route: Route): Promise<boolean> {
    const request = route.request()
    this.received.push(request)
    const key = `${request.method()} ${decodeURIComponent(new URL(request.url()).pathname)}`
    const handler = this.overrides.get(key) || this.defaults.get(key)
    if (!handler) return false
    await handler(route)
    return true
  }
}

export const test = base.extend<{ api: MockApi }>({
  api: [async ({ context, baseURL }, run) => {
    if (!baseURL || new URL(baseURL).hostname !== '127.0.0.1') {
      throw new Error('Browser regressions require the managed loopback test server.')
    }
    // Playwright gives each test a new context. Assert that no stored student
    // profile, cookies, or scheduler state was inherited before visiting the UI.
    expect(await context.storageState()).toEqual({ cookies: [], origins: [] })
    const origin = new URL(baseURL).origin
    const unexpected: string[] = []
    const api = new MockApi()
    await context.route('**/*', async (route) => {
      const request = route.request()
      const url = new URL(request.url())
      if (url.origin === origin) {
        if (!url.pathname.startsWith('/api/')) {
          await route.continue()
          return
        }
        if (await api.dispatch(route)) return
      }
      unexpected.push(`${request.method()} ${url.origin}${url.pathname}`)
      await route.abort('blockedbyclient')
    })
    await run(api)
    expect(unexpected, 'Unexpected network requests; add an explicit synthetic mock.').toEqual([])
  }, { auto: true }],
  page: async ({ page, baseURL }, run) => {
    const errors: string[] = []
    const consoleErrors: ConsoleMessage[] = []
    const failedApiResponses = new Set<string>()
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message)
    })
    page.on('response', (response) => {
      const url = new URL(response.url())
      if (url.origin === baseURL && url.pathname.startsWith('/api/') && response.status() >= 400) {
        failedApiResponses.add(`${response.status()} ${response.url()}`)
      }
    })
    // Fix Date.now() without stopping browser timers, search debounce, or React.
    await page.clock.setFixedTime(new Date('2026-09-12T15:00:00Z'))
    await run(page)
    for (const message of consoleErrors) {
      // Chromium logs HTTP status errors even when the UI handles them. Allow
      // only those tied to an observed failing mock; keep React/Next errors.
      const status = /^Failed to load resource: the server responded with a status of (\d{3}) \(.*\)$/.exec(message.text())?.[1]
      if (status && failedApiResponses.has(`${status} ${message.location().url}`)) continue
      errors.push(message.text())
    }
    expect(errors, 'Uncaught browser errors').toEqual([])
  },
})

export { expect } from '@playwright/test'
