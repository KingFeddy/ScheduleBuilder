export type ApiErrorKind = 'http' | 'network' | 'invalid-response'

function retryDelay(value: string | null): number | null {
  if (!value) return null
  const trimmed = value.trim()
  if (/^\d+$/.test(trimmed)) {
    const milliseconds = Number(trimmed) * 1000
    return Number.isFinite(milliseconds) ? milliseconds : null
  }
  // Date.parse also accepts bare numbers/decimals that are not HTTP dates.
  if (!/[a-z]/i.test(trimmed)) return null
  const date = Date.parse(trimmed)
  return Number.isNaN(date) ? null : Math.max(0, date - Date.now())
}

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly detail: unknown
  readonly retryAfter: string | null
  readonly retryAfterMs: number | null

  constructor(kind: ApiErrorKind, message: string, options: {
    status?: number
    detail?: unknown
    retryAfter?: string | null
    cause?: unknown
  } = {}) {
    super(message, { cause: options.cause })
    this.name = 'ApiError'
    this.kind = kind
    this.status = options.status ?? null
    this.detail = options.detail ?? null
    this.retryAfter = options.retryAfter ?? null
    this.retryAfterMs = retryDelay(this.retryAfter)
  }
}

function hasName(error: unknown, name: string): boolean {
  return typeof error === 'object' && error !== null && 'name' in error && error.name === name
}

export function isAbortError(error: unknown): boolean {
  return hasName(error, 'AbortError')
}

export function throwIfCancelled(signal?: AbortSignal | null, error?: unknown, response?: Response): void {
  if (isAbortError(error)) throw error
  if (!signal?.aborted) return
  // A timeout is a failure the user may need to retry, not a dismissed request.
  if (hasName(signal.reason, 'TimeoutError')) {
    throw new ApiError('network', 'The request timed out. Please try again.', {
      status: response?.status, retryAfter: response?.headers.get('Retry-After'), cause: signal.reason,
    })
  }
  // Browsers can reject with arbitrary custom abort reasons. Normalize these so
  // every consumer can recognize cancellation without inspecting error text.
  throw isAbortError(signal.reason)
    ? signal.reason
    : new DOMException('The request was cancelled.', 'AbortError')
}

function detailMessage(detail: unknown): string | null {
  if (typeof detail === 'string' && detail.trim() && !detail.trim().startsWith('<')) return detail
  if (!Array.isArray(detail)) return null
  const messages = detail.flatMap((item: unknown) => {
    if (typeof item !== 'object' || item === null || !('msg' in item) || typeof item.msg !== 'string') return []
    const location = 'loc' in item && Array.isArray(item.loc)
      ? item.loc.filter((part: unknown) => typeof part === 'string' || typeof part === 'number')
      : []
    if (location[0] === 'body') location.shift()
    return [location.length ? `${location.join('.')}: ${item.msg}` : item.msg]
  })
  // FastAPI validation details can contain submitted input/ctx. Keep those in
  // detail for structured handling, but never stringify them into UI messages.
  return messages.length ? messages.join('; ') : null
}

export function httpError(response: Response, body: unknown, cause?: unknown): ApiError {
  let detail = body
  if (typeof body === 'object' && body !== null) {
    if ('detail' in body) detail = body.detail
    else if ('error' in body) detail = body.error // SlowAPI's rate-limit response.
  }
  return new ApiError('http', detailMessage(detail) || `Request failed (${response.status}).`, {
    status: response.status, detail, retryAfter: response.headers.get('Retry-After'), cause,
  })
}

/** A null message means cancellation: callers should not present an error. */
export function getApiErrorMessage(error: unknown, fallback: string): string | null {
  if (isAbortError(error)) return null
  if (!(error instanceof ApiError)) return fallback
  if (error.status === 429) {
    if (error.retryAfterMs !== null && error.retryAfterMs > 0) {
      const seconds = Math.ceil(error.retryAfterMs / 1000)
      return `Too many requests — try again in ${seconds} second${seconds === 1 ? '' : 's'}.`
    }
    return 'Too many requests — please wait a moment and try again.'
  }
  if (error.kind === 'invalid-response' || (error.status !== null && error.status >= 500)) return fallback
  return error.message
}
