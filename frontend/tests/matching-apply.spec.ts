import { expect, test, type Page } from '@playwright/test';

type Feature = 'blik' | 'allegro';
type Decision = { transaction_id: number; selected_match_id?: string };
const interruptionReason =
  'Execution interrupted; verify results and run a new preview before retrying; transaction 2 may have been updated';

async function matchingScreen(
  page: Page,
  feature: Feature,
  options: {
    cacheMiss?: boolean;
    failedJob?: boolean;
    refreshFails?: boolean;
    polling?: boolean;
    interruptedJob?: boolean;
  } = {}
) {
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const state = {
    previews: 0,
    history: [] as string[],
    requests: 0,
    jobs: 0,
    cacheReady: false,
    applied: new Set<number>()
  };
  let job: Record<string, unknown>;
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
      if (path.endsWith('/me'))
        return route.fulfill({
          json: { id: 'test-user', username: 'tester', is_active: true, is_superuser: false }
        });
      if (path.endsWith('/vault/status'))
        return route.fulfill({ json: { configured: true, unlocked: true, expires_at: null } });
      if (path.endsWith('/matches')) {
        state.previews += 1;
        state.history.push('preview');
        if (options.refreshFails && state.previews > 1)
          return route.fulfill({ status: 503, json: { detail: 'Matching service unavailable' } });
        state.cacheReady = true;
        return route.fulfill({
          json: {
            login: 'synthetic',
            file_id: 'synthetic',
            decoded_name: 'synthetic',
            records_in_file: 2,
            payments_fetched: 2,
            transactions_found: 2,
            transactions_not_matched: 0,
            transactions_with_one_match: 2,
            transactions_with_many_matches: 0,
            fetch_seconds: 0,
            unmatched_payments: [],
            content: [1, 2].map((id) => ({
              status: state.applied.has(id) ? 'already_processed' : 'new',
              tx: {
                id,
                date: '2026-01-01',
                amount: 10,
                currency_symbol: 'PLN',
                description: `Transaction ${id}`,
                tags: [],
                notes: null
              },
              matches: [
                {
                  match_id: `match-${id}`,
                  external_id: `external-${id}`,
                  external_short_id: `payment-${id}`,
                  amount: 10,
                  date: '2026-01-01',
                  details: feature === 'allegro' ? ['Synthetic payment'] : 'Synthetic payment',
                  recipient: 'Synthetic shop',
                  sender: '',
                  recipient_account: '',
                  sender_account: '',
                  operation_amount: 10,
                  operation_currency: 'PLN',
                  is_balanced: true
                }
              ]
            }))
          }
        });
      }
      if (path.endsWith('/apply')) {
        state.requests += 1;
        state.history.push('apply');
        if (!state.cacheReady || (options.cacheMiss && state.requests === 1))
          return route.fulfill({
            status: 400,
            json: { detail: 'No match data found; run preview first' }
          });
        state.cacheReady = false;
        state.jobs += 1;
        const { decisions } = route.request().postDataJSON() as { decisions: Decision[] };
        if (!options.failedJob && !options.polling)
          for (const decision of decisions) state.applied.add(decision.transaction_id);
        job = {
          id: `job-${state.jobs}`,
          file_id: 'synthetic',
          secret_id: 'synthetic',
          total: decisions.length,
          status: options.failedJob ? 'failed' : 'done',
          applied: options.failedJob ? 0 : decisions.length,
          failed: options.failedJob ? decisions.length : 0,
          started_at: '2026-01-01T00:00:00Z',
          finished_at: '2026-01-01T00:00:01Z',
          results: decisions.map((d) => ({
            transaction_id: d.transaction_id,
            selected_match_id: d.selected_match_id,
            status: options.failedJob ? 'failed' : 'success',
            reason: options.failedJob ? 'Synthetic failure' : null
          }))
        };
        if (options.interruptedJob) {
          job.results = [
            { transaction_id: 1, status: 'failed', reason: 'Synthetic failure' },
            { transaction_id: -1, status: 'failed', reason: interruptionReason }
          ];
        }
        return route.fulfill({
          json: options.polling
            ? { ...job, status: 'pending', results: [], applied: 0, failed: 0 }
            : job
        });
      }
      if (path.includes('/apply-jobs/')) {
        state.history.push('poll');
        if (!options.failedJob) {
          for (const result of job.results as Decision[]) state.applied.add(result.transaction_id);
        }
        return route.fulfill({ json: job });
      }
      return route.fulfill({ json: {} });
    }
  );
  await page.goto(feature === 'blik' ? '/blik/file/synthetic/match' : '/allegro/synthetic/matches');
  const choices = page
    .locator('label')
    .filter({ hasText: 'Select candidate' })
    .locator('input:enabled');
  try {
    await expect(choices).toHaveCount(2);
  } catch (error) {
    console.error('Matching screen diagnostic', {
      url: page.url(),
      previews: state.previews,
      pageErrors,
      body: (await page.locator('body').innerText()).slice(0, 4000),
      choices: await page.locator('input[type="checkbox"]').evaluateAll((inputs) =>
        inputs.map((input) => ({
          disabled: (input as HTMLInputElement).disabled,
          html: input.outerHTML
        }))
      )
    });
    throw error;
  }
  return { state, choices, apply: page.getByRole('button', { name: /Apply selected/ }) };
}

async function closeProgress(page: Page) {
  const close = page.getByRole('button', { name: 'Close', exact: true });
  if (await close.isVisible()) await close.click();
}

for (const feature of ['blik', 'allegro'] as const) {
  test(`${feature}: applies two successive selections after consuming the cache`, async ({
    page
  }) => {
    const { state, choices, apply } = await matchingScreen(page, feature, { polling: true });
    await choices.first().check();
    await apply.click();
    await expect.poll(() => state.previews).toBe(2);
    await closeProgress(page);
    await expect(choices).toHaveCount(1);
    await choices.first().check();
    await apply.click();
    await expect.poll(() => state.previews).toBe(3);
    expect(state.jobs).toBe(2);
    expect([...state.applied]).toEqual([1, 2]);
    expect(state.history).toEqual([
      'preview',
      'apply',
      'poll',
      'preview',
      'apply',
      'poll',
      'preview'
    ]);
  });

  test(`${feature}: cache miss refreshes selections without retrying a mutation`, async ({
    page
  }) => {
    const { state, choices, apply } = await matchingScreen(page, feature, { cacheMiss: true });
    await choices.first().check();
    await apply.click();
    await expect.poll(() => state.previews).toBe(2);
    await closeProgress(page);
    await expect(apply).toBeDisabled();
    expect(state.requests).toBe(1);
    expect(state.jobs).toBe(0);
    await choices.first().check();
    await apply.click();
    await expect.poll(() => state.jobs).toBe(1);
    expect(state.requests).toBe(2);
  });

  test(`${feature}: failed terminal job also refreshes matches`, async ({ page }) => {
    const { state, choices, apply } = await matchingScreen(page, feature, { failedJob: true });
    await choices.first().check();
    await apply.click();
    await expect.poll(() => state.previews).toBe(2);
    await closeProgress(page);
    await expect(choices).toHaveCount(2);
    await expect(apply).toBeDisabled();
  });

  test(`${feature}: failed refresh removes stale choices`, async ({ page }) => {
    const { state, choices, apply } = await matchingScreen(page, feature, { refreshFails: true });
    await choices.first().check();
    await apply.click();
    await expect.poll(() => state.previews).toBe(2);
    await closeProgress(page);
    await expect(choices).toHaveCount(0);
    await expect(apply).toBeDisabled();
    expect(state.jobs).toBe(1);
  });
}

for (const polling of [false, true]) {
  test(`allegro: preserves interruption details after refresh (polling=${polling})`, async ({
    page
  }) => {
    const { state, choices, apply } = await matchingScreen(page, 'allegro', {
      failedJob: true,
      interruptedJob: true,
      polling
    });
    await choices.nth(0).check();
    await choices.nth(1).check();
    await apply.click();
    await expect.poll(() => state.previews).toBe(2);
    const warning = page.getByRole('alert').filter({ hasText: interruptionReason });
    await expect(warning).toBeVisible();
    await expect(warning).toContainText('Synthetic failure');
    await expect(warning).toContainText('transaction 2 may have been updated');
    await expect(choices).toHaveCount(2);
    await expect(apply).toBeDisabled();
    expect(state.requests).toBe(1);
    expect(state.jobs).toBe(1);
    expect(state.history).toEqual(
      polling ? ['preview', 'apply', 'poll', 'preview'] : ['preview', 'apply', 'preview']
    );
  });
}
