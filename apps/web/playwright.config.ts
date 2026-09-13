import { defineConfig, devices } from '@playwright/test'

const mode = process.env.E2E_TEST_MODE || 'development'
if (mode !== 'development' && mode !== 'production') {
  throw new Error('E2E_TEST_MODE must be development or production.')
}
const production = mode === 'production'
const port = production ? 3101 : 3100
const baseURL = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 2,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  outputDir: `test-results/${mode}`,
  reporter: [
    ['list'],
    ['html', { outputFolder: `playwright-report/${mode}`, open: 'never' }],
  ],
  use: {
    baseURL,
    storageState: { cookies: [], origins: [] },
    serviceWorkers: 'block',
    locale: 'en-US',
    timezoneId: 'America/New_York',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } }],
  webServer: {
    command: production
      ? `pnpm build && pnpm start --hostname 127.0.0.1 --port ${port}`
      : `pnpm dev --hostname 127.0.0.1 --port ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 180_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5_000 },
    env: {
      NODE_ENV: mode,
      E2E_TEST_MODE: mode,
      NEXT_TELEMETRY_DISABLED: '1',
      // Override both inherited shell variables and Next's .env file values.
      NEXT_PUBLIC_API_URL: '',
      NEXT_PUBLIC_SENTRY_DSN: '',
      SENTRY_DSN: '',
      SENTRY_AUTH_TOKEN: '',
      RAILWAY_API_URL: 'http://127.0.0.1:9',
    },
  },
})
