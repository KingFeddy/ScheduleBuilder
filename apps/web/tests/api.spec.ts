import { test as base, expect } from '@playwright/test'
import * as api from '../lib/api'
import { parsedDegree, solveResponse } from '../e2e/data'

type FetchHandler = typeof fetch
type FetchCall = { url: string; init?: RequestInit }

const test = base.extend<{ transport: { calls: FetchCall[]; respond: (handler: FetchHandler) => void } }>({
  transport: [async ({}, run) => {
    const original = globalThis.fetch
    const calls: FetchCall[] = []
    let handler: FetchHandler = async () => { throw new Error('No synthetic fetch response configured.') }
    globalThis.fetch = (input, init) => {
      calls.push({ url: String(input), init })
      return handler(input, init)
    }
    try {
      await run({ calls, respond: (next) => { handler = next } })
    } finally {
      globalThis.fetch = original
    }
  }, { auto: true }],
})

// No test can reach a real API, even when a developer has API variables set.
async function rejection(promise: Promise<unknown>): Promise<api.ApiError> {
  const error = await promise.then(() => { throw new Error('Expected a rejected request.') }, (error: unknown) => error)
  expect(error).toBeInstanceOf(api.ApiError)
  return error as api.ApiError
}

test('preserves field-level validation details without rendering submitted input', async ({ transport }) => {
  const detail = [{ loc: ['body', 'preferences', 'credits_per_semester'], msg: 'Must be greater than zero', type: 'greater_than', input: 'synthetic-private-input' }]
  transport.respond(async () => Response.json({ detail }, { status: 422 }))
  const error = await rejection(api.generatePlan(parsedDegree, { courses: [], credits_per_semester: 0 }))
  expect(error.kind).toBe('http')
  expect(error.status).toBe(422)
  expect(error.detail).toEqual(detail)
  expect(error.message).toContain('preferences.credits_per_semester: Must be greater than zero')
  expect(error.message).not.toContain('synthetic-private-input')
  expect(api.getApiErrorMessage(error, 'Fallback')).toBe(error.message)
  expect(transport.calls).toHaveLength(1)
})

for (const status of [400, 413, 422, 500, 503]) {
  test(`retains HTTP ${status} and its detail`, async ({ transport }) => {
    transport.respond(async () => Response.json({ detail: 'Synthetic server explanation' }, { status }))
    const error = await rejection(api.parsePlan('synthetic-pdf', 'synthetic-hash'))
    expect(error.status).toBe(status)
    expect(error.detail).toBe('Synthetic server explanation')
    expect(error.message).toBe('Synthetic server explanation')
    expect(error.retryAfter).toBeNull()
    expect(error.retryAfterMs).toBeNull()
    if (status >= 500) expect(api.getApiErrorMessage(error, 'Please retry this action.')).toBe('Please retry this action.')
  })
}

for (const [header, milliseconds] of [['30', 30_000], ['0', 0], ['1.5', null], ['-2', null], ['invalid', null]] as const) {
  test(`preserves Retry-After ${header} without guessing invalid delays`, async ({ transport }) => {
    transport.respond(async () => Response.json({ error: 'Rate limit exceeded' }, { status: 429, headers: { 'Retry-After': header } }))
    const error = await rejection(api.solveSchedule({ course_codes: ['CS280'], term: '202690' }))
    expect(error.status).toBe(429)
    expect(error.detail).toBe('Rate limit exceeded')
    expect(error.retryAfter).toBe(header)
    expect(error.retryAfterMs).toBe(milliseconds)
    expect(api.getApiErrorMessage(error, 'Fallback')).toContain('Too many requests')
    if (milliseconds === 30_000) expect(api.getApiErrorMessage(error, 'Fallback')).toContain('30 seconds')
    expect(transport.calls).toHaveLength(1) // No automatic replay of expensive requests.
  })
}

test('handles future and expired HTTP-date Retry-After values', async ({ transport }) => {
  const future = new Date(Date.now() + 60_000).toUTCString()
  transport.respond(async () => Response.json({ detail: 'Busy' }, { status: 503, headers: { 'Retry-After': future } }))
  const error = await rejection(api.getScraperStatus())
  expect(error.retryAfter).toBe(future)
  expect(error.retryAfterMs).toBeGreaterThan(55_000)
  expect(error.retryAfterMs).toBeLessThanOrEqual(60_000)
  transport.respond(async () => new Response('', { status: 503, headers: { 'Retry-After': 'Wed, 01 Jan 2020 00:00:00 GMT' } }))
  expect((await rejection(api.getScraperStatus())).retryAfterMs).toBe(0)
})

for (const body of ['', '<html>Proxy unavailable</html>', '{invalid json']) {
  test(`preserves status when the error body is not JSON: ${body || 'empty'}`, async ({ transport }) => {
    transport.respond(async () => new Response(body, { status: 502, headers: { 'Content-Type': 'text/html' } }))
    const error = await rejection(api.getGerCourses())
    expect(error.status).toBe(502)
    expect(error.kind).toBe('http')
    expect(api.getApiErrorMessage(error, 'Failed to load GER courses.')).toBe('Failed to load GER courses.')
  })
}

test('reports malformed successful JSON as an invalid response', async ({ transport }) => {
  transport.respond(async () => new Response('<html>Proxy page</html>', { status: 200 }))
  const error = await rejection(api.getCourses({ q: 'CS' }))
  expect(error.kind).toBe('invalid-response')
  expect(error.status).toBe(200)
  expect(error.message).not.toContain('<html>')
})

test('distinguishes network failure and preserves its cause without leaking it to the UI', async ({ transport }) => {
  const cause = new TypeError('Synthetic transport failure')
  transport.respond(async () => { throw cause })
  const error = await rejection(api.getCourses({}))
  expect(error.kind).toBe('network')
  expect(error.status).toBeNull()
  expect(error.cause).toBe(cause)
  expect(api.getApiErrorMessage(error, 'Fallback')).toContain('connection')
  expect(api.getApiErrorMessage(error, 'Fallback')).not.toContain(cause.message)
})

test('retains HTTP status and retry headers when reading an error body fails', async ({ transport }) => {
  transport.respond(async () => {
    const response = new Response('', { status: 503, headers: { 'Retry-After': '12' } })
    response.text = async () => { throw new TypeError('Synthetic interrupted body') }
    return response
  })
  const error = await rejection(api.getScraperStatus())
  expect(error.status).toBe(503)
  expect(error.kind).toBe('http')
  expect(error.retryAfterMs).toBe(12_000)
})

test('only a professor HTTP 404 means not found', async ({ transport }) => {
  transport.respond(async () => Response.json({ detail: 'Professor not found in RMP cache.' }, { status: 404 }))
  expect(await api.getProfessor('Synthetic Professor')).toBeNull()
  transport.respond(async () => Response.json({ detail: 'Unavailable' }, { status: 503 }))
  expect((await rejection(api.getProfessor('Synthetic Professor'))).status).toBe(503)
  transport.respond(async () => { throw new TypeError('Offline') })
  expect((await rejection(api.getProfessor('Synthetic Professor'))).kind).toBe('network')
  transport.respond(async () => Response.json(null))
  expect((await rejection(api.getProfessor('Synthetic Professor'))).kind).toBe('invalid-response')
})

const callers: [string, (options: api.ApiRequestOptions) => Promise<unknown>][] = [
  ['catalog coverage', (options) => api.getCatalogCoverage('202690', options)],
  ['courses', (options) => api.getCourses({ q: 'CS 280' }, options)],
  ['sections', (options) => api.getCoursesSections('CS280', '202690', options)],
  ['professor', (options) => api.getProfessor('Synthetic Professor', options)],
  ['solve', (options) => api.solveSchedule({ course_codes: ['CS280'], term: '202690' }, options)],
  ['parse', (options) => api.parsePlan('synthetic-pdf', 'synthetic-hash', options)],
  ['generate', (options) => api.generatePlan(parsedDegree, { courses: [], credits_per_semester: 15 }, options)],
  ['GER', (options) => api.getGerCourses(options)],
  ['scraper status', (options) => api.getScraperStatus(options)],
]

for (const [label, call] of callers) {
  test(`${label} forwards its signal and rejects cancellation`, async ({ transport }) => {
    const controller = new AbortController()
    transport.respond(async (_url, init) => {
      expect(init?.signal).toBe(controller.signal)
      controller.abort()
      throw controller.signal.reason
    })
    const error = await call({ signal: controller.signal }).catch((error: unknown) => error)
    expect(api.isAbortError(error)).toBe(true)
    expect(error).not.toBeInstanceOf(api.ApiError)
    expect(api.getApiErrorMessage(error, 'Should not appear')).toBeNull()
    expect(transport.calls).toHaveLength(1)
  })
}

test('an already-cancelled request never starts fetching', async ({ transport }) => {
  const controller = new AbortController()
  controller.abort('Superseded synthetic request')
  const error = await api.getCourses({}, { signal: controller.signal }).catch((error: unknown) => error)
  expect(api.isAbortError(error)).toBe(true)
  expect(api.getApiErrorMessage(error, 'Should not appear')).toBeNull()
  expect(transport.calls).toHaveLength(0)
})

for (const status of [200, 404, 422]) {
  test(`cancellation while reading HTTP ${status} is not converted to data or an HTTP error`, async ({ transport }) => {
    const controller = new AbortController()
    transport.respond(async () => {
      const response = Response.json(solveResponse, { status })
      response.text = async () => {
        controller.abort()
        throw controller.signal.reason
      }
      return response
    })
    const error = await api.getProfessor('Synthetic Professor', { signal: controller.signal }).catch((error: unknown) => error)
    expect(api.isAbortError(error)).toBe(true)
    expect(api.getApiErrorMessage(error, 'Should not appear')).toBeNull()
  })
}

test('does not publish a body that finished after cancellation', async ({ transport }) => {
  const controller = new AbortController()
  transport.respond(async () => {
    const response = Response.json([])
    response.text = async () => {
      controller.abort(new Error('Superseded'))
      return '[]'
    }
    return response
  })
  const error = await api.getCourses({}, { signal: controller.signal }).catch((error: unknown) => error)
  expect(api.isAbortError(error)).toBe(true)
})

test('timeouts remain failures rather than silent user cancellation', async ({ transport }) => {
  const controller = new AbortController()
  transport.respond(async () => {
    controller.abort(new DOMException('Synthetic timeout', 'TimeoutError'))
    throw controller.signal.reason
  })
  const error = await api.getCourses({}, { signal: controller.signal }).catch((error: unknown) => error)
  expect(api.isAbortError(error)).toBe(false)
  expect(api.getApiErrorMessage(error, 'Request timed out.')).not.toBeNull()
})

test('retains received status and retry information when body reading times out', async ({ transport }) => {
  const controller = new AbortController()
  transport.respond(async () => {
    const response = new Response('', { status: 503, headers: { 'Retry-After': '12' } })
    response.text = async () => {
      controller.abort(new DOMException('Synthetic body timeout', 'TimeoutError'))
      throw controller.signal.reason
    }
    return response
  })
  const error = await rejection(api.getScraperStatus({ signal: controller.signal }))
  expect(error.status).toBe(503)
  expect(error.retryAfterMs).toBe(12_000)
  expect(api.isAbortError(error)).toBe(false)
})

test('HTTP status controls the message, not digits inside a validation explanation', async ({ transport }) => {
  transport.respond(async () => Response.json({ detail: 'Choose a different course than TEST429.' }, { status: 422 }))
  const error = await rejection(api.generatePlan(parsedDegree, { courses: ['TEST429'], credits_per_semester: 15 }))
  expect(api.getApiErrorMessage(error, 'Fallback')).toBe('Choose a different course than TEST429.')
})
