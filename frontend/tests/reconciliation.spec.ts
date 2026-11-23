import { expect, test, type Page } from '@playwright/test';

async function setup(page: Page, fail = false) {
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const writes: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const candidate = (id: number, extra = {}) => ({
    id,
    date: '2026-11-16',
    amount: '-30.04',
    currency: 'PLN',
    description: `Invoice ${id}`,
    type: 'withdrawal',
    reconciled: false,
    can_mark: true,
    restriction: null,
    ...extra
  });
  const row = (
    id: number,
    status: string,
    candidates: unknown[],
    reviewReason: string | null = null
  ) => ({
    bank: {
      row_id: id,
      date: '2026-11-16',
      amount: '-30.04',
      currency: 'PLN',
      description: `Invoice ${id}`,
      own_account: ''
    },
    status,
    review_reason: reviewReason,
    candidates,
    candidate_count: candidates.length
  });
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
      if (path.endsWith('/me'))
        return route.fulfill({
          json: {
            id: 'tester',
            username: 'tester',
            is_active: true,
            is_superuser: false
          }
        });
      if (path.endsWith('/vault/status'))
        return route.fulfill({
          json: {
            configured: true,
            unlocked: true,
            expires_at: null
          }
        });
      if (path.endsWith('/duplicates/options'))
        return route.fulfill({
          json: {
            accounts: [{ id: 1, name: 'Main bank', type: 'asset' }],
            date_from: '2026-11-01',
            date_to: '2026-11-30',
            snapshot_timestamp: 'stamp',
            skipped_multipart: 0
          }
        });
      if (path.endsWith('/reconciliation/preview'))
        return route.fulfill({
          json: {
            revision: 'a'.repeat(64),
            rows: [
              row(1, 'matched', [candidate(1)]),
              row(2, 'review', [candidate(2), candidate(3)], 'ambiguous'),
              row(3, 'bank_only', []),
              row(4, 'matched', [
                candidate(4, {
                  type: 'transfer',
                  can_mark: false,
                  restriction: 'Transfer: review manually'
                })
              ]),
              row(5, 'matched', [candidate(5, { reconciled: true })]),
              row(
                7,
                'review',
                [
                  candidate(7, {
                    description: 'Imported description',
                    date: '2026-11-17',
                    title_matches: false
                  })
                ],
                'title_mismatch'
              )
            ],
            firefly_only: [candidate(6)],
            totals: [
              {
                currency: 'PLN',
                bank_total: '-150.20',
                firefly_total: '-150.200000000000',
                difference: '0.000000000000'
              }
            ],
            skipped_multipart: 2,
            skipped_invalid: 0,
            excluded_foreign_transfers: 1
          }
        });
      if (path.endsWith('/reconciliation/apply')) {
        writes.push(route.request().postData() ?? '');
        return fail
          ? route.fulfill({ status: 409, json: { detail: 'Comparison changed. Compare again.' } })
          : route.fulfill({ json: { outcomes: [{ id: 1, status: 'reconciled' }] } });
      }
      return route.fulfill({ json: {} });
    }
  );
  return { writes, errors };
}

async function compare(page: Page) {
  await page.goto('/tx/reconcile');
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('1');
  await page.getByLabel('Period from').fill('2026-11-01');
  await page.getByLabel('Period to').fill('2026-11-30');
  await page.getByLabel('Date tolerance (days)').fill('1');
  await page.getByLabel('Alior CSV', { exact: false }).setInputFiles({
    name: 'statement.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('Synthetic statement')
  });
  await page.getByLabel('This file includes all booked operations', { exact: false }).check();
  await page.getByRole('button', { name: 'Compare statement', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Statement comparison results' })).toBeVisible();
}

test('only unambiguous supported matches can be marked; apply consumes the preview', async ({
  page
}) => {
  const mock = await setup(page);
  await compare(page);
  const results = page.getByRole('region', { name: 'Statement comparison results' });
  await expect(results.getByText('Bank only: 1')).toBeVisible();
  await expect(results.getByText('Firefly only: 1')).toBeVisible();
  await expect(page.getByLabel('Reconcile transaction #2', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Reconcile transaction #4', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Reconcile transaction #5', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Mark selected', exact: false })).toBeDisabled();
  expect(mock.writes).toEqual([]);
  await page.getByLabel('Select verified matches').check();
  await page.getByRole('button', { name: 'Mark selected as reconciled (1)', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Reconciliation outcomes' })).toContainText(
    '#1: reconciled'
  );
  await expect(results).toHaveCount(0);
  expect(mock.writes).toHaveLength(1);
  expect(mock.writes[0]).toContain('name="selected_ids"\r\n\r\n[1]');
  expect(mock.writes[0]).toContain('name="revision"');
  expect(mock.writes[0]).toContain('filename="statement.csv"');
  expect(mock.errors).toEqual([]);
});

test('a changed comparison cannot be retried until a new preview', async ({ page }) => {
  const mock = await setup(page, true);
  await compare(page);
  await page.getByLabel('Reconcile transaction #1', { exact: true }).check();
  await page.getByRole('button', { name: 'Mark selected', exact: false }).click();
  await expect(page.getByRole('alert')).toContainText('Comparison changed');
  await expect(page.getByRole('button', { name: 'Mark selected', exact: false })).toHaveCount(0);
  expect(mock.writes).toHaveLength(1);
  await page.getByRole('button', { name: 'Compare statement', exact: true }).click();
  await expect(page.getByLabel('Reconcile transaction #1', { exact: true })).not.toBeChecked();
  expect(mock.errors).toEqual([]);
});

test('changing evidence clears matches and mobile layout stays within the viewport', async ({
  page
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const mock = await setup(page);
  await compare(page);
  const totals = page.getByRole('region', { name: 'Movement totals in PLN' });
  await expect(totals.getByText('0.00', { exact: true })).toBeVisible();
  await page.getByLabel('Show').selectOption('title_mismatch');
  const comparison = page.getByRole('list', { name: 'Bank statement comparison' });
  await expect(comparison.getByRole('listitem')).toHaveCount(1);
  await expect(comparison).toContainText('Invoice 7');
  await expect(comparison).toContainText('Imported description');
  await expect(comparison).toContainText('Different title');
  await expect(comparison).toContainText('Different date');
  await expect(comparison.getByRole('checkbox')).toHaveCount(0);
  await page.getByLabel('Show').selectOption('firefly_only');
  await expect(comparison).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Firefly-only transactions' })).toContainText(
    'Invoice 6'
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
  await page.getByLabel('Date tolerance (days)').fill('2');
  await page.getByRole('button', { name: 'Compare statement', exact: true }).focus();
  await expect(page.getByRole('region', { name: 'Statement comparison results' })).toHaveCount(0);
  expect(mock.writes).toEqual([]);
  expect(mock.errors).toEqual([]);
});
