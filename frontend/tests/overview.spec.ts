import { expect, test } from '@playwright/test';
import { mockOverview } from './overview-fixture';

let errors: string[] = [];
test.beforeEach(({ page }) => {
  errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
});
test.afterEach(() => expect(errors).toEqual([]));

for (const theme of ['wireframe', 'black']) {
  for (const width of [1440, 390]) {
    test(`${theme} overview at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
      const state = await mockOverview(page, theme);
      await page.goto('/');
      const workflows = page.getByRole('region', { name: 'Workflows', exact: true });
      await expect(page.getByRole('status', { name: 'Firefly connection' })).toContainText(
        'Connected'
      );
      await expect(workflows.getByRole('link', { name: '12 ready to categorize' })).toHaveAttribute(
        'href',
        '/tx/stats'
      );
      await expect(
        workflows.getByRole('link', { name: '0 unprocessed BLIK transactions' })
      ).toHaveAttribute('href', '/blik/stats');
      await expect(workflows.getByRole('article', { name: 'BLIK', exact: true })).toContainText(
        'No work in this snapshot.'
      );
      await expect(
        workflows.getByRole('link', { name: '7 unprocessed Allegro transactions' })
      ).toHaveAttribute('href', '/allegro/stats');
      await expect(
        workflows.getByRole('link', { name: 'Categorize', exact: true })
      ).toHaveAttribute('href', '/tx/categorize?year=2026&month=10');
      await expect(
        workflows.getByRole('link', { name: 'Upload file', exact: true })
      ).toHaveAttribute('href', '/blik/upload');
      await expect(
        workflows.getByRole('link', { name: 'Open accounts', exact: true })
      ).toHaveAttribute('href', '/allegro/accounts');
      await expect(
        workflows.getByRole('link', { name: 'Import PDF', exact: true })
      ).toHaveAttribute('href', '/tools/velobank');
      await expect(workflows.getByRole('article')).toHaveCount(4);
      await expect(page.getByText(/Amazon|Workspace areas/)).toHaveCount(0);
      await expect(page.locator('main a[href="#"]')).toHaveCount(0);
      await expect(page.getByRole('status', { name: 'Transaction data freshness' })).toContainText(
        'Fresh snapshot at last check'
      );
      await expect(
        page.getByRole('status', { name: 'Categorization count' }).locator('time')
      ).toHaveAttribute('datetime', state.timestamp);
      expect(state.writes.filter((path) => !path.endsWith('/auth/refresh'))).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true
      );
      const countLink = workflows.getByRole('link', { name: '12 ready to categorize' });
      await page.keyboard.press('Tab');
      await countLink.focus();
      await expect(countLink).toHaveCSS('outline-style', 'solid');
      await page.screenshot({ path: testInfo.outputPath('overview.png'), animations: 'disabled' });
      await page.getByText('About these counts', { exact: true }).click();
      await expect(
        page.getByText(/This is a transaction count, not a count of marketplace payments/)
      ).toBeVisible();
    });
  }

  test(`${theme}: loading, unavailable counts and connection recovery`, async ({
    page
  }, testInfo) => {
    const state = await mockOverview(page, theme);
    state.delay = 600;
    state.counts.tx = null;
    state.statuses.blik = 'failed';
    state.fail = [
      '/api/allegro/statistics',
      '/api/system/firefly',
      '/api/system/transaction-snapshot'
    ];
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Categorization count' })).toContainText(
      'Loading count'
    );
    await expect(page.getByRole('status', { name: 'Firefly connection' })).toContainText(
      'Checking connection'
    );
    await expect(page.getByRole('status', { name: 'Transaction data freshness' })).toContainText(
      'Checking data freshness'
    );
    await expect(page.getByRole('button', { name: 'Refresh overview' })).toBeDisabled();
    for (const name of ['Categorization', 'BLIK', 'Allegro']) {
      await expect(page.getByRole('status', { name: `${name} count`, exact: true })).toContainText(
        'Count unavailable'
      );
    }
    await expect(page.getByRole('status', { name: 'Firefly connection' })).toContainText(
      'Connection information unavailable'
    );
    await expect(page.getByRole('status', { name: 'Transaction data freshness' })).toContainText(
      'Data freshness unavailable'
    );
    await expect(page.getByText('No work in this snapshot.', { exact: true })).toHaveCount(0);
    await expect(
      page
        .getByRole('region', { name: 'Workflows' })
        .getByRole('link', { name: 'Categorize', exact: true })
    ).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('unavailable.png') });
    state.delay = 0;
    state.fail = [];
    state.counts = { tx: 0, blik: 0, allegro: 0 };
    state.statuses.blik = 'done';
    state.firefly = 'not_configured';
    state.hasSnapshot = false;
    await page.getByRole('button', { name: 'Refresh overview' }).click();
    await expect(page.getByText('No work in this snapshot.', { exact: true })).toHaveCount(3);
    await expect(page.getByRole('status', { name: 'Transaction data freshness' })).toContainText(
      'No stored snapshot'
    );
    await expect(page.getByRole('status', { name: 'Firefly connection' })).toContainText(
      'Connection not configured'
    );
    await expect(
      page
        .getByRole('region', { name: 'Workflows' })
        .getByRole('link', { name: 'Categorize', exact: true })
    ).toHaveAttribute('href', '/tx/categorize');
    state.firefly = 'error';
    await page.getByRole('button', { name: 'Refresh overview' }).click();
    await expect(page.getByRole('status', { name: 'Firefly connection' })).toContainText(
      'Connection unavailable'
    );
    state.firefly = 'ok';
    state.hasSnapshot = true;
    state.stale = true;
    await page.getByRole('button', { name: 'Refresh overview' }).click();
    await expect(page.getByRole('status', { name: 'Firefly connection' })).toContainText(
      'Connected'
    );
    await expect(page.getByRole('status', { name: 'Transaction data freshness' })).toContainText(
      'Stale snapshot at last check'
    );
  });

  test(`${theme}: pending jobs hide cached counts and update through GET polling`, async ({
    page
  }) => {
    const state = await mockOverview(page, theme);
    state.statuses.tx = 'running';
    state.statuses.blik = 'pending';
    state.counts.blik = 9;
    state.counts.allegro = -1;
    await page.goto('/');
    await expect(page.getByRole('status', { name: 'Categorization count' })).toContainText(
      'Calculating count'
    );
    await expect(page.getByRole('status', { name: 'BLIK count', exact: true })).toContainText(
      'Calculating count'
    );
    await expect(page.getByRole('status', { name: 'Allegro count', exact: true })).toContainText(
      'Count unavailable'
    );
    await expect(page.getByRole('link', { name: '12 ready to categorize' })).toHaveCount(0);
    state.statuses.tx = 'done';
    state.statuses.blik = 'done';
    await expect(page.getByRole('link', { name: '12 ready to categorize' })).toBeVisible();
    await expect(page.getByRole('link', { name: '9 unprocessed BLIK transactions' })).toBeVisible();
    expect(
      state.requests.filter((path) => path === '/api/tx/statistics').length
    ).toBeGreaterThanOrEqual(2);
    expect(state.writes.filter((path) => !path.endsWith('/auth/refresh'))).toEqual([]);
  });
}

test('leaving the overview stops pending metrics polling', async ({ page }) => {
  await page.clock.install();
  const state = await mockOverview(page);
  state.statuses = { tx: 'running', blik: 'running', allegro: 'running' };
  await page.goto('/');
  for (const source of ['Categorization', 'BLIK', 'Allegro']) {
    await expect(page.getByRole('status', { name: `${source} count`, exact: true })).toContainText(
      'Calculating count'
    );
  }
  await page.getByRole('button', { name: 'Account menu' }).click();
  await page.getByRole('link', { name: 'Profile', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Profile');
  const metricsRequests = () =>
    state.requests.filter((path) => path.endsWith('/statistics') || path.endsWith('/statistics_v2'))
      .length;
  const before = metricsRequests();
  await page.clock.fastForward(10_000);
  expect(metricsRequests()).toBe(before);
});
