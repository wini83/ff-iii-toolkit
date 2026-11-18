import { expect, test, type Page } from '@playwright/test';

async function setup(page: Page, result: object | null, theme = 'wireframe') {
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  await page.addInitScript((value) => localStorage.setItem('ff-toolkit-theme', value), theme);
  const state = { result, failed: false, pendingReads: 0 };
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
      if (path.endsWith('/me'))
        return route.fulfill({
          json: { id: 'tester', username: 'tester', is_active: true, is_superuser: false }
        });
      if (path.endsWith('/vault/status'))
        return route.fulfill({ json: { configured: true, unlocked: false, expires_at: null } });
      if (path.endsWith('/statistics/transactions'))
        return route.fulfill({
          json: {
            items: [],
            total: 0,
            limit: 50,
            offset: 0,
            snapshot_timestamp: '2026-11-17T10:00:00Z',
            firefly_url: null
          }
        });
      if (path.includes('/statistics'))
        return route.fulfill({
          json: {
            status: state.failed ? 'failed' : state.pendingReads-- > 0 ? 'pending' : 'done',
            result: state.failed ? null : state.result,
            error: state.failed ? 'Synthetic metrics failure' : null,
            progress: null
          }
        });
      return route.fulfill({ json: {} });
    }
  );
  return state;
}
const amounts = { PLN: '1234567890123.45', EUR: '-287.30', CZK: '1808.00' };
const txResult = {
  single_part_transactions: 987654321,
  uncategorized_transactions: 123,
  action_req: 12,
  categorizable: 5,
  blik_not_ok: 2,
  allegro_not_ok: 3,
  single_part_amount: amounts,
  uncategorized_amount: amounts,
  action_req_amount: {},
  categorizable_amount: amounts,
  blik_not_ok_amount: { PLN: '0.00' },
  allegro_not_ok_amount: { EUR: '-1.00' },
  categorizable_by_month: { '2026-11': 3, '2026-10': 2 },
  categorizable_amount_by_month: {
    '2026-11': { PLN: '100.25', EUR: '-2.50' },
    '2026-10': { PLN: '200.50', CZK: '1808.00' }
  },
  time_stamp: '2026-11-17T10:00:00Z',
  fetch_seconds: 15.42
};

for (const theme of ['wireframe', 'black']) {
  for (const width of [1440, 390]) {
    test(`amounts stay separate and fit at ${width}px in ${theme}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await setup(page, txResult, theme);
      await page.goto('/tx/stats');
      await expect(
        page.getByRole('region', { name: 'Single-part transactions', exact: true })
      ).toBeVisible();
      await page.getByRole('button', { name: 'Amount', exact: true }).click();
      const card = page.getByRole('region', { name: 'Single-part transactions', exact: true });
      await expect(card.getByText('PLN', { exact: true })).toBeVisible();
      await expect(card.getByText('EUR', { exact: true })).toBeVisible();
      await expect(card.getByText('-287,30', { exact: true })).toBeVisible();
      const chart = page.locator('canvas');
      await expect(chart).toHaveAttribute('aria-label', 'Categorizable by month in PLN');
      await expect(chart).toHaveText('2026-10: 200.5; 2026-11: 100.25');
      await page.getByLabel('Chart currency', { exact: true }).selectOption('EUR');
      await expect(chart).toHaveText('2026-10: no data; 2026-11: -2.5');
      await expect(chart).toHaveAttribute('aria-label', 'Categorizable by month in EUR');
      expect(
        await page
          .locator('[data-metric-card]')
          .evaluateAll((cards) => cards.every((card) => card.scrollWidth <= card.clientWidth))
      ).toBe(true);
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
      ).toBe(true);
      await page.screenshot({
        path: testInfo.outputPath('statistics-amounts.png'),
        fullPage: true,
        animations: 'disabled'
      });
      await page.getByRole('button', { name: 'Count', exact: true }).click();
      await expect(chart).toHaveText('2026-10: 2; 2026-11: 3');
      await page.getByRole('button', { name: /Switch to .* theme/ }).click();
      await expect(page.locator('html')).toHaveAttribute(
        'data-theme',
        theme === 'black' ? 'wireframe' : 'black'
      );
      await expect(chart).toHaveText('2026-10: 2; 2026-11: 3');
      expect(errors).toEqual([]);
    });
  }
}

test('refresh removes unavailable currency and clears stale values on failure', async ({
  page
}) => {
  const state = await setup(page, txResult);
  await page.goto('/tx/stats');
  await expect(
    page.getByRole('region', { name: 'Single-part transactions', exact: true })
  ).toBeVisible();
  await page.getByRole('button', { name: 'Amount', exact: true }).click();
  await page.getByLabel('Chart currency', { exact: true }).selectOption('EUR');
  state.result = {
    ...txResult,
    single_part_amount: { CZK: '0.00' },
    uncategorized_amount: {},
    categorizable_amount: {},
    categorizable_amount_by_month: {}
  };
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByLabel('Chart currency', { exact: true })).toHaveValue('CZK');
  await expect(page.getByText('No monthly data for CZK.')).toBeVisible();
  state.failed = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(
    page.getByRole('alert').filter({ hasText: 'Synthetic metrics failure' })
  ).toBeVisible();
  await expect(page.locator('[data-metric-card]')).toHaveCount(0);
});

for (const provider of ['allegro', 'blik']) {
  test(`${provider} keeps count metrics and explains legacy amount snapshots`, async ({
    page
  }, testInfo) => {
    await setup(
      page,
      {
        total_transactions: 30,
        single_part_transactions: 30,
        uncategorized_transactions: 4,
        allegro_transactions: 20,
        not_processed__allegro_transactions: 5,
        not_processed_transactions: 5,
        filtered_by_description_exact: 3,
        not_processed_by_month: { '2026-11': 5 },
        time_stamp: '2026-11-17T10:00:00Z',
        fetch_seconds: 0.1
      },
      'black'
    );
    await page.goto(`/${provider}/stats`);
    await expect(page.getByRole('button', { name: 'Show unprocessed transactions' })).toBeVisible();
    await expect(page.locator('canvas')).toHaveText('2026-11: 5');
    await page.getByRole('button', { name: 'Amount', exact: true }).click();
    await expect(
      page.getByText('Amount statistics are unavailable for this snapshot.', { exact: false })
    ).toBeVisible();
    await page.getByRole('button', { name: 'Count', exact: true }).click();
    await page.screenshot({
      path: testInfo.outputPath(`${provider}-statistics.png`),
      fullPage: true,
      animations: 'disabled'
    });
  });
}

test('pending statistics are polled until the toolbar becomes usable', async ({ page }) => {
  const state = await setup(page, txResult);
  state.pendingReads = 1;
  await page.goto('/tx/stats');
  await expect(
    page.getByRole('region', { name: 'Single-part transactions', exact: true })
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
});

const providerAmounts = {
  eligible_amount: { PLN: '100.30', EUR: '0.00' },
  processed_amount: { PLN: '100.10', EUR: '0.00' },
  not_processed_amount: { PLN: '0.20', EUR: '0.00' },
  processing_coverage: { PLN: '99.80059820538384845463609172', EUR: null },
  not_processed_amount_by_month: {
    '2026-10': { PLN: '0.20' },
    '2026-11': { EUR: '0.00' }
  }
};

for (const provider of ['allegro', 'blik']) {
  for (const theme of ['wireframe', 'black']) {
    test(`${provider} amount coverage and currencies in ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 900 });
      const state = await setup(
        page,
        {
          total_transactions: 30,
          single_part_transactions: 30,
          uncategorized_transactions: 4,
          allegro_transactions: 20,
          not_processed__allegro_transactions: 2,
          not_processed_transactions: 2,
          filtered_by_description_exact: 2,
          not_processed_by_month: { '2026-10': 1, '2026-11': 1 },
          amount_metrics: providerAmounts,
          time_stamp: '2026-11-17T10:00:00Z',
          fetch_seconds: 0.1
        },
        theme
      );
      await page.goto(`/${provider}/stats`);
      await page.getByRole('button', { name: 'Amount', exact: true }).click();
      await expect(
        page.getByRole('region', { name: 'Eligible amount', exact: true })
      ).toContainText('100,30');
      await expect(
        page.getByRole('region', { name: 'Processed amount', exact: true })
      ).toContainText('100,10');
      await expect(
        page.getByRole('region', { name: 'Not processed amount', exact: true })
      ).toContainText('0,20');
      const coverage = page.getByRole('region', { name: 'Amount processing coverage' });
      await expect(coverage).toContainText('99,8%');
      await expect(coverage).toContainText('n/a');
      const chart = page.locator('canvas');
      await expect(chart).toHaveAttribute('aria-label', 'Not processed amount by month in PLN');
      await expect(chart).toHaveText('2026-10: 0.2; 2026-11: no data');
      await page.getByLabel('Chart currency', { exact: true }).selectOption('EUR');
      await expect(chart).toHaveText('2026-10: no data; 2026-11: 0');
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
      ).toBe(true);
      await page.getByRole('button', { name: 'Show unprocessed transactions' }).click();
      await expect(page.getByRole('heading', { name: 'Unprocessed transactions' })).toBeVisible();
      await page.getByRole('button', { name: 'Count', exact: true }).click();
      await expect(chart).toHaveText('2026-10: 1; 2026-11: 1');
      state.failed = true;
      await page.getByRole('button', { name: 'Refresh', exact: true }).click();
      await expect(page.getByRole('region', { name: 'Eligible amount', exact: true })).toHaveCount(
        0
      );
      await expect(
        page.getByRole('alert').filter({ hasText: 'Synthetic metrics failure' })
      ).toBeVisible();
    });
  }
}
