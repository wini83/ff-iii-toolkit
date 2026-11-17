import type { Page } from '@playwright/test';
import type { components } from '../src/lib/api/schema';

type JobStatus = components['schemas']['JobStatus'];
export async function mockOverview(page: Page, theme = 'wireframe') {
  await page.addInitScript((value) => localStorage.setItem('ff-toolkit-theme', value), theme);
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const state = {
    counts: { tx: 12 as number | null, blik: 0 as number | null, allegro: 7 as number | null },
    statuses: { tx: 'done' as JobStatus, blik: 'done' as JobStatus, allegro: 'done' as JobStatus },
    firefly: 'ok' as 'ok' | 'error' | 'not_configured',
    hasSnapshot: true,
    stale: false,
    fail: [] as string[],
    delay: 0,
    timestamp: '2026-11-17T10:00:00Z',
    requests: [] as string[],
    writes: [] as string[]
  };
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      state.requests.push(path);
      if (route.request().method() !== 'GET') state.writes.push(path);
      if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
      if (path.endsWith('/me'))
        return route.fulfill({
          json: { id: 'tester', username: 'demo.operator', is_active: true, is_superuser: false }
        });
      if (path.endsWith('/vault/status'))
        return route.fulfill({ json: { configured: true, unlocked: false, expires_at: null } });
      if (path.endsWith('/system/version')) return route.fulfill({ json: { version: '0.3.13' } });
      if (state.delay) await new Promise((done) => setTimeout(done, state.delay));
      if (state.fail.includes(path))
        return route.fulfill({ status: 503, json: { detail: 'Service unavailable' } });
      if (path.endsWith('/system/firefly'))
        return route.fulfill({
          json: {
            status: state.firefly,
            version: '6.4.0',
            api_version: '2.1.0',
            timestamp: state.timestamp
          }
        });
      if (path.endsWith('/transaction-snapshot'))
        return route.fulfill({
          json: {
            ttl_seconds: 3600,
            has_snapshot: state.hasSnapshot,
            snapshot_fetched_at: state.hasSnapshot ? state.timestamp : null,
            expires_at: state.hasSnapshot ? '2099-01-01T00:00:00Z' : null,
            is_stale: state.stale,
            transaction_count: state.hasSnapshot ? 200 : null
          }
        });
      const source =
        path === '/api/tx/statistics'
          ? 'tx'
          : path === '/api/blik_files/statistics_v2'
            ? 'blik'
            : path === '/api/allegro/statistics'
              ? 'allegro'
              : null;
      if (source)
        return route.fulfill({
          json: {
            status: state.statuses[source],
            progress: null,
            error: state.statuses[source] === 'failed' ? 'Calculation failed' : null,
            result: {
              categorizable: state.counts.tx,
              categorizable_by_month: { '2026-10': 12 },
              not_processed_transactions: state.counts.blik,
              not_processed__allegro_transactions: state.counts.allegro,
              time_stamp: state.timestamp
            }
          }
        });
      return route.fulfill({ json: {} });
    }
  );
  return state;
}
