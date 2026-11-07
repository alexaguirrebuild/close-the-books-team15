import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  fullyParallel: false,
  reporter: 'line',
  use: {
    baseURL: 'http://localhost:5198',
    viewport: { width: 1440, height: 900 },
    acceptDownloads: true,
    channel: process.env.PW_CHANNEL,
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npx vite --port 5198 --strictPort',
    url: 'http://localhost:5198',
    reuseExistingServer: true,
    stdout: 'ignore',
    stderr: 'ignore',
  },
})
