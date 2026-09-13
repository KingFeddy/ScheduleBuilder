import { defineConfig } from '@playwright/test'

// Reuse the installed test runner without launching a browser or an app server.
export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.ts',
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 10_000,
  reporter: 'list',
  outputDir: 'test-results/unit',
})
