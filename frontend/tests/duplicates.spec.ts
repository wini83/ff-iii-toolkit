import { expect, test, type Page } from '@playwright/test';

async function setup(page: Page, empty = false, overlap = false, preMarked: number[] = []) {
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const queries: URLSearchParams[] = [];
  const errors: string[] = [];
  const refreshes: string[] = [];
  const tagRequests: { id: number; tag: string | null; method: string }[] = [];
  const failTags = new Set<number>();
  const marked = new Set(preMarked);
  let changed = false;
  let fail = false;
  let stamp = '2026-11-18T10:00:00Z';
  page.on('pageerror', (error) => errors.push(error.message));
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
      const tagging = path.match(/^\/api\/tx\/(\d+)\/tag\/$/);
      if (tagging) {
        const id = Number(tagging[1]);
        tagRequests.push({
          id,
          tag: url.searchParams.get('tag'),
          method: route.request().method()
        });
        if (failTags.delete(id))
          return route.fulfill({ status: 502, json: { detail: 'Firefly unavailable' } });
        marked.add(id);
        return route.fulfill({ status: 204, body: '' });
      }
      if (path.endsWith('/duplicates/options/refresh')) {
        refreshes.push(route.request().method());
        stamp = '2026-11-18T12:00:00Z';
      }
      if (path.endsWith('/duplicates/options') || path.endsWith('/duplicates/options/refresh'))
        return route.fulfill({
          json: {
            accounts: empty
              ? []
              : [
                  { id: 1, name: 'Main bank', type: 'asset' },
                  { id: 2, name: 'Savings', type: 'asset' }
                ],
            date_from: empty ? null : '2026-01-01',
            date_to: empty ? null : '2026-11-18',
            snapshot_timestamp: stamp,
            skipped_multipart: 3
          }
        });
      if (path.endsWith('/duplicates')) {
        queries.push(url.searchParams);
        if (changed) {
          changed = false;
          stamp = '2026-11-18T11:00:00Z';
          return route.fulfill({ status: 409, json: { detail: 'Snapshot changed' } });
        }
        if (fail) {
          fail = false;
          return route.fulfill({ status: 503, json: { detail: 'Snapshot unavailable' } });
        }
        const offset = Number(url.searchParams.get('offset'));
        const noResults = url.searchParams.get('account_id') === '2';
        return route.fulfill({
          json: {
            items: noResults
              ? []
              : Array.from({ length: overlap ? 3 : offset ? 1 : 25 }, (_, i) => {
                  const [firstId, secondId] = overlap
                    ? [
                        [1, 2],
                        [1, 3],
                        [2, 3]
                      ][i]
                    : [2 * (offset + i) + 1, 2 * (offset + i) + 2];
                  const tx = (id: number) => ({
                    id,
                    date: '2026-11-10',
                    amount: 12.34,
                    currency_code: 'PLN',
                    description: `Bank payment ${id}`,
                    type: id % 2 ? 'deposit' : 'transfer',
                    tags: [
                      ...(id % 2 ? ['bank', 'shared'] : ['csv', 'shared']),
                      ...(marked.has(id) ? ['do-usuniecia'] : [])
                    ],
                    source_account: { id: 3, name: 'Other account' },
                    destination_account: { id: 1, name: 'Main bank' }
                  });
                  return {
                    id: `${firstId}:${secondId}`,
                    first: tx(firstId),
                    second: tx(secondId),
                    amount: '12.34',
                    currency_code: 'PLN',
                    direction: 'incoming',
                    date_difference_days: 2
                  };
                }),
            total: noResults ? 0 : overlap ? 3 : 26,
            offset,
            limit: 25,
            snapshot_timestamp: stamp,
            firefly_url: 'https://firefly.example/subpath',
            skipped_multipart: 3
          }
        });
      }
      return route.fulfill({ json: {} });
    }
  );
  return {
    queries,
    tagRequests,
    failTag: (id: number) => failTags.add(id),
    errors,
    refreshes,
    change: () => {
      changed = true;
    },
    fail: () => {
      fail = true;
    }
  };
}

test('search filters, pair details, pinned pagination, stale snapshot and retry', async ({
  page
}) => {
  const mock = await setup(page);
  await page.goto('/tx/duplicates');
  await expect(page.getByRole('heading', { name: 'Potential duplicates' })).toBeVisible();
  await expect(page.getByLabel('Date tolerance (days)')).toHaveValue('2');
  await expect(page.getByRole('combobox', { name: 'Account', exact: true })).toHaveValue('');
  await expect(page.getByText('Split groups excluded: 3.', { exact: false })).toBeVisible();
  expect(mock.queries).toHaveLength(0);
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('1');
  await page.getByRole('button', { name: 'Find candidates' }).click();
  const results = page.getByRole('region', { name: 'Duplicate candidates' });
  await expect(results.getByRole('heading', { name: 'Candidate pairs: 26' })).toBeVisible();
  const pair = results.getByRole('article', { name: 'Pair 1:2', exact: true });
  await expect(pair.getByText('deposit', { exact: true })).toBeVisible();
  await expect(pair.getByText('transfer', { exact: true })).toBeVisible();
  await expect(pair.getByRole('link', { name: 'Open #1', exact: true })).toHaveAttribute(
    'href',
    'https://firefly.example/subpath/transactions/show/1'
  );
  expect(mock.queries[0].get('tolerance_days')).toBe('2');
  expect(mock.queries[0].get('date_from')).toBe('2026-01-01');
  // Editing form controls does not change the submitted search during pagination.
  await page.getByLabel('Date tolerance (days)').fill('0');
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('2');
  await results.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(results.getByText('Bank payment 51', { exact: true })).toBeVisible();
  expect(mock.queries.at(-1)?.get('offset')).toBe('25');
  expect(mock.queries.at(-1)?.get('account_id')).toBe('1');
  expect(mock.queries.at(-1)?.get('tolerance_days')).toBe('2');
  mock.change();
  await results.getByRole('button', { name: 'Previous', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('snapshot changed');
  await expect(results).toHaveCount(0);
  await page.getByRole('button', { name: 'Reload transactions and filters' }).click();
  await page.getByRole('button', { name: 'Find candidates' }).click();
  await expect(page.getByText('No potential duplicates for these filters.')).toBeVisible();
  expect(mock.queries.at(-1)?.get('snapshot_timestamp')).toBe('2026-11-18T11:00:00Z');
  expect(mock.queries.at(-1)?.get('tolerance_days')).toBe('0');
  mock.fail();
  await page.getByRole('button', { name: 'Find candidates' }).click();
  await expect(page.getByRole('alert')).toContainText('Snapshot unavailable');
  await page.getByRole('button', { name: 'Retry search' }).click();
  await expect(page.getByText('No potential duplicates for these filters.')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Account', exact: true })).toHaveValue('');
  await expect(page.getByLabel('Date tolerance (days)')).toHaveValue('2');
  expect(mock.errors).toEqual([]);
});

test('mobile results remain inside viewport and work in both themes', async ({
  page
}, testInfo) => {
  const mock = await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/tx/duplicates');
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('1');
  await page.getByLabel('Date from', { exact: true }).fill('2026-11-01');
  await page.getByLabel('Date to', { exact: true }).fill('2026-11-18');
  await page.getByRole('button', { name: 'Find candidates' }).click();
  await expect(page.getByRole('heading', { name: 'Candidate pairs: 26' })).toBeVisible();
  for (const theme of ['wireframe', 'black']) {
    await page.evaluate(
      (theme) => document.documentElement.setAttribute('data-theme', theme),
      theme
    );
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    ).toBe(true);
    await expect(page.getByRole('article', { name: 'Pair 1:2', exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`duplicates-${theme}-mobile.png`) });
  }
  expect(mock.errors).toEqual([]);
});

test('empty snapshot does not allow a search', async ({ page }) => {
  const mock = await setup(page, true);
  await page.goto('/tx/duplicates');
  await expect(page.getByText('No asset accounts in the transaction snapshot.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Find candidates' })).toBeDisabled();
  expect(mock.queries).toHaveLength(0);
});

test('reload forces a refresh and searches with the replacement snapshot', async ({ page }) => {
  const mock = await setup(page);
  await page.goto('/tx/duplicates');
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('1');
  await page.getByRole('button', { name: 'Find candidates' }).click();
  await expect(page.getByRole('heading', { name: 'Candidate pairs: 26' })).toBeVisible();
  expect(mock.refreshes).toEqual([]);
  await page.getByRole('button', { name: 'Reload transactions', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Duplicate candidates' })).toHaveCount(0);
  await expect(page.getByText('Snapshot: 2026-11-18T12:00:00Z.', { exact: false })).toBeVisible();
  expect(mock.refreshes).toEqual(['POST']);
  await page.getByRole('button', { name: 'Find candidates' }).click();
  await expect(page.getByRole('heading', { name: 'Candidate pairs: 26' })).toBeVisible();
  expect(mock.queries.at(-1)?.get('snapshot_timestamp')).toBe('2026-11-18T12:00:00Z');
  expect(mock.errors).toEqual([]);
});

async function searchCandidates(page: Page) {
  await page.goto('/tx/duplicates');
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('1');
  await page.getByRole('button', { name: 'Find candidates', exact: true }).click();
  await expect(page.getByRole('article', { name: 'Pair 1:2', exact: true })).toBeVisible();
}

test('explicit keep decisions, keep both, preview and cancel before adding deletion tags', async ({
  page
}) => {
  const mock = await setup(page);
  await searchCandidates(page);
  const pair = page.getByRole('article', { name: 'Pair 1:2', exact: true });
  const review = page.getByRole('button', { name: 'Review decisions (0)', exact: true });
  await expect(review).toBeDisabled();
  await pair.getByRole('button', { name: 'Keep both', exact: true }).click();
  await expect(page.getByText('Keep: 2 · To mark: 0', { exact: true })).toBeVisible();
  await expect(review).toBeDisabled();
  await pair.getByRole('button', { name: 'Keep transaction #1', exact: true }).click();
  await expect(pair.getByText('To mark for deletion', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Find candidates', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Review decisions (1)', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Review deletion markers' });
  await expect(dialog.getByRole('list', { name: 'Transactions to mark' })).toContainText('#2');
  await expect(dialog).toContainText('Bank payment 2');
  await expect(dialog).toContainText('Existing tags: csv, shared');
  expect(mock.tagRequests).toEqual([]);
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(mock.tagRequests).toEqual([]);
  await page.getByRole('button', { name: 'Review decisions (1)', exact: true }).click();
  await dialog.getByRole('button', { name: 'Confirm marking (1)', exact: true }).click();
  await expect(pair.getByText('Marked for deletion', { exact: true })).toBeVisible();
  expect(mock.tagRequests).toEqual([{ id: 2, tag: 'do-usuniecia', method: 'POST' }]);
  await expect(
    pair.getByRole('button', { name: 'Keep transaction #1', exact: true })
  ).toBeVisible();
  await expect(pair.getByText('csv', { exact: true })).toBeVisible();
  expect(mock.errors).toEqual([]);
});

test('overlapping pairs share decisions, reject conflicts and retry only failed unique IDs', async ({
  page
}) => {
  const mock = await setup(page, false, true);
  await searchCandidates(page);
  const pair12 = page.getByRole('article', { name: 'Pair 1:2', exact: true });
  const pair13 = page.getByRole('article', { name: 'Pair 1:3', exact: true });
  const pair23 = page.getByRole('article', { name: 'Pair 2:3', exact: true });
  await pair12.getByRole('button', { name: 'Keep transaction #1', exact: true }).click();
  await pair13.getByRole('button', { name: 'Keep transaction #1', exact: true }).click();
  await expect(page.getByText('Keep: 1 · To mark: 2', { exact: true })).toBeVisible();
  await pair23.getByRole('button', { name: 'Keep transaction #2', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('kept in another pair');
  await expect(page.getByText('Keep: 1 · To mark: 2', { exact: true })).toBeVisible();
  mock.failTag(3);
  await page.getByRole('button', { name: 'Review decisions (2)', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Review deletion markers' });
  await expect(dialog.getByRole('listitem')).toHaveCount(2);
  await dialog.getByRole('button', { name: 'Confirm marking (2)', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('#3');
  await expect(pair12.getByText('Marked for deletion', { exact: true })).toBeVisible();
  await expect(pair23.getByText('Marked for deletion', { exact: true })).toBeVisible();
  await expect(page.getByText('Keep: 1 · To mark: 1', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Review decisions (1)', exact: true }).click();
  await dialog.getByRole('button', { name: 'Confirm marking (1)', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByText('Keep: 1 · To mark: 0', { exact: true })).toBeVisible();
  expect(mock.tagRequests.map((r) => r.id)).toEqual([2, 3, 3]);
  await page.getByRole('button', { name: 'Clear decisions', exact: true }).click();
  await expect(page.getByText('Keep: 1 · To mark: 0', { exact: true })).toBeVisible();
  expect(mock.errors).toEqual([]);
});

test('already marked transactions cannot be chosen as the kept copy', async ({ page }) => {
  const mock = await setup(page, false, false, [2]);
  await searchCandidates(page);
  const pair = page.getByRole('article', { name: 'Pair 1:2', exact: true });
  await expect(pair.getByText('Marked for deletion', { exact: true })).toBeVisible();
  await expect(pair.getByRole('button', { name: 'Keep transaction #2', exact: true })).toHaveCount(
    0
  );
  await expect(pair.getByRole('button', { name: 'Keep both', exact: true })).toBeDisabled();
  await pair.getByRole('button', { name: 'Keep transaction #1', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Review decisions (0)', exact: true })
  ).toBeDisabled();
  expect(mock.tagRequests).toEqual([]);
});

test('a confirmed kept copy stays protected after starting another search', async ({ page }) => {
  const mock = await setup(page, false, true);
  await searchCandidates(page);
  const pair12 = page.getByRole('article', { name: 'Pair 1:2', exact: true });
  await pair12.getByRole('button', { name: 'Keep transaction #1', exact: true }).click();
  await page.getByRole('button', { name: 'Review decisions (1)', exact: true }).click();
  await page
    .getByRole('dialog')
    .getByRole('button', { name: 'Confirm marking (1)', exact: true })
    .click();
  await expect(pair12.getByText('Marked for deletion', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Find candidates', exact: true }).click();
  const pair13 = page.getByRole('article', { name: 'Pair 1:3', exact: true });
  await pair13.getByRole('button', { name: 'Keep transaction #3', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('confirmed batch');
  await expect(
    page.getByRole('button', { name: 'Review decisions (0)', exact: true })
  ).toBeDisabled();
  expect(mock.tagRequests.map((r) => r.id)).toEqual([2]);
});
