import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: [
    'matching-apply.spec.ts',
    'metric-transactions.spec.ts',
    'duplicates.spec.ts',
    'reconciliation.spec.ts',
    'statement-counterparties.spec.ts',
    'themes.spec.ts',
    'shell.spec.ts',
    'screening.spec.ts',
    'overview.spec.ts',
    'statistics.spec.ts',
    'workflows.spec.ts'
  ],
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: true,
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
  },
  webServer: [
    {
      command: 'node tests/theme-backend.mjs',
      url: 'http://127.0.0.1:4175/api/system/health',
      reuseExistingServer: false
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 4173 --strictPort',
      url: 'http://127.0.0.1:4173/login',
      reuseExistingServer: false,
      env: { INTERNAL_API_BASE: 'http://127.0.0.1:4175' }
    }
  ]
});
