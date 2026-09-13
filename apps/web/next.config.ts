import type { NextConfig } from 'next'

const isDev = process.env.NODE_ENV === 'development'
const RAILWAY_API_URL = process.env.RAILWAY_API_URL
const testMode = process.env.E2E_TEST_MODE
if (testMode && testMode !== 'development' && testMode !== 'production') {
  throw new Error('E2E_TEST_MODE must be development or production.')
}

if (!isDev && !RAILWAY_API_URL) {
  throw new Error(
    'RAILWAY_API_URL is not set. Add it to Vercel environment variables before deploying.'
  )
}

const nextConfig: NextConfig = {
  // Tests own their build output and cannot proxy a missed mock to a real API.
  distDir: testMode ? `.next/e2e-${testMode}` : '.next',
  async rewrites() {
    if (testMode) return []
    return [
      {
        source: '/api/:path*',
        destination: isDev
          ? 'http://localhost:8000/api/:path*'
          : `${RAILWAY_API_URL}/api/:path*`,
      },
    ]
  },

  allowedDevOrigins: ['localhost:3000', '127.0.0.1:3000'],
}

export default nextConfig
