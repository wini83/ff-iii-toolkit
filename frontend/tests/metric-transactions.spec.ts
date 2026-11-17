import { expect, test } from '@playwright/test';

for (const provider of ['allegro', 'blik'] as const) {
  test(`${provider}: inspect metrics, pagination, month, sorting and changed snapshot`, async ({
    page
  }) => {
    const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
    await page
      .context()
      .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
    let timestamp = '2026-11-17T10:00:00Z';
    let changed = false;
    const queries: URLSearchParams[] = [];
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route(
      (url) => url.pathname.startsWith('/api/'),
      async (route) => {
        const url = new URL(route.request().url());
        const path = url.pathname;
        if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
        if (path.endsWith('/me'))
          return route.fulfill({
            json: { id: 'tester', username: 'tester', is_active: true, is_superuser: false }
          });
        if (path.endsWith('/vault/status'))
          return route.fulfill({ json: { configured: true, unlocked: true, expires_at: null } });
        if (path.endsWith('/statistics') || path.endsWith('/statistics_v2'))
          return route.fulfill({
            json: {
              status: 'done',
              result: {
                total_transactions: 30,
                single_part_transactions: 30,
                uncategorized_transactions: 30,
                allegro_transactions: 30,
                not_processed__allegro_transactions: 26,
                filtered_by_description_exact: 26,
                not_processed_transactions: 26,
                not_processed_by_month: { '2026-10': 26 },
                time_stamp: timestamp,
                fetch_seconds: 0.1
              },
              progress: null,
              error: null
            }
          });
        if (path.endsWith('/statistics/transactions')) {
          queries.push(url.searchParams);
          if (changed) {
            changed = false;
            timestamp = '2026-11-17T11:00:00Z';
            return route.fulfill({ status: 409, json: { detail: 'Snapshot changed' } });
          }
          const offset = Number(url.searchParams.get('offset'));
          const empty = url.searchParams.get('month') === '2026-09';
          const count = empty ? 0 : offset ? 1 : 25;
          return route.fulfill({
            json: {
              items: Array.from({ length: count }, (_, index) => ({
                id: offset + index + 1,
                date: '2026-10-05',
                amount: 10.12,
                currency_code: 'PLN',
                description: `Payment ${offset + index + 1}`,
                tags: ['bank'],
                source_account: { id: 1, name: 'My bank' },
                destination_account: { id: 2, name: 'Shop' }
              })),
              total: empty ? 0 : 26,
              limit: 25,
              offset,
              snapshot_timestamp: timestamp,
              firefly_url: 'https://firefly.example'
            }
          });
        }
        return route.fulfill({ json: {} });
      }
    );
    await page.goto(`/${provider}/stats`);
    await page.getByRole('button', { name: 'Show unprocessed transactions' }).click();
    const table = page.getByRole('region', { name: 'Unprocessed transactions' });
    await expect(table.getByText('Total: 26', { exact: true })).toBeVisible();
    await expect(table.getByRole('link', { name: 'Open #1', exact: true })).toHaveAttribute(
      'href',
      'https://firefly.example/transactions/show/1'
    );
    await table.getByRole('button', { name: 'Next', exact: true }).click();
    await expect(table.getByText('Payment 26', { exact: true })).toBeVisible();
    expect(queries.at(-1)?.get('offset')).toBe('25');
    await table.getByRole('combobox').selectOption('date_asc');
    await expect(table.getByText('Payment 1', { exact: true })).toBeVisible();
    expect(queries.at(-1)?.get('offset')).toBe('0');
    expect(queries.at(-1)?.get('sort')).toBe('date_asc');

    // The chart contains exactly one bar. Wait for its animation before clicking its center.
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    await canvas.click({ position: { x: box!.width / 2, y: box!.height / 2 } });
    await expect(table.getByRole('heading')).toContainText('2026-10');
    expect(queries.at(-1)?.get('month')).toBe('2026-10');
    changed = true;
    await table.getByRole('button', { name: 'Next', exact: true }).click();
    await expect(table.getByRole('alert')).toContainText('Statistics changed');
    await expect(table.getByRole('table')).toHaveCount(0);
    await table.getByRole('button', { name: 'Reload statistics and transactions' }).click();
    await expect(table.getByText('Total: 26', { exact: true })).toBeVisible();
    expect(queries.at(-1)?.get('snapshot_timestamp')).toBe(timestamp);
    expect(queries.at(-1)?.get('offset')).toBe('0');
    expect(errors).toEqual([]);
  });
}
