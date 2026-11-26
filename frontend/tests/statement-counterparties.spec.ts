import { expect, test } from '@playwright/test';

test('previews assignments once, then shows persisted results on the job page', async ({
  page
}) => {
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const jobId = '95fb48fc-5e5a-421c-bfa7-6dca3be86842';
  const revision = 'a'.repeat(64);
  const choiceGroup = 'b'.repeat(64);
  let writes = 0;
  let reads = 0;
  await page.route(/\/api\//, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
    if (path.endsWith('/me')) {
      return route.fulfill({
        json: { id: 'tester', username: 'tester', is_active: true, is_superuser: false }
      });
    }
    if (path.endsWith('/statement-counterparties/accounts')) {
      return route.fulfill({ json: [{ id: 1, name: 'Checking' }] });
    }
    if (path.endsWith('/statement-counterparties/preview')) {
      expect(route.request().postData()).toContain('name="single_account_confirmed"\r\n\r\ntrue');
      return route.fulfill({
        json: {
          revision,
          ready: 1,
          review: 3,
          skipped: 0,
          omitted: 0,
          skipped_multipart: 0,
          skipped_invalid: 0,
          rows: [
            {
              row_id: 1,
              date: '2026-11-16',
              amount: '-5.00',
              currency: 'PLN',
              description: 'Shop K.1 Poznan PL',
              status: 'ready',
              transaction_id: 11,
              current_account: '(unknown destination account)',
              proposed_account: 'Shop K.1',
              reason: 'Matched to an existing expense account by card merchant.'
            },
            {
              row_id: 2,
              date: '2026-11-16',
              amount: '-10.00',
              currency: 'PLN',
              description: 'Savings',
              status: 'review',
              reason: 'Possible transfer between your accounts.'
            },
            ...[3, 4].map((rowId) => ({
              row_id: rowId,
              date: '2026-11-16',
              amount: '-20.00',
              currency: 'PLN',
              description: 'Allegro Poznan PL',
              status: 'review',
              reason: 'Several Firefly accounts match this card merchant.',
              card_merchant: 'Allegro',
              choice_group: choiceGroup,
              candidates: [
                { id: 21, name: 'Allegro', match: 'merchant', prior_assignments: 4 },
                { id: 22, name: 'Allegro Poznan PL', match: 'statement', prior_assignments: 0 }
              ]
            }))
          ]
        }
      });
    }
    if (path.endsWith('/statement-counterparties/apply')) {
      writes += 1;
      expect(route.request().postData()).toContain(revision);
      expect(route.request().postData()).toContain(JSON.stringify({ [choiceGroup]: 22 }));
      expect(route.request().postData()).toContain('name="single_account_confirmed"\r\n\r\ntrue');
      return route.fulfill({ status: 202, json: { id: jobId } });
    }
    if (path.endsWith(`/statement-counterparties/jobs/${jobId}`)) {
      reads += 1;
      return route.fulfill({
        json: {
          id: jobId,
          status: 'done',
          total: 3,
          applied: 3,
          failed: 0,
          firefly_url: 'https://firefly.example',
          results: [{ transaction_id: 11, status: 'success' }]
        }
      });
    }
    return route.continue();
  });

  await page.goto('/blik/upload');
  await page.getByRole('link', { name: 'Assign counterparties from an Alior statement' }).click();
  await page.getByLabel('Firefly account').selectOption('1');
  await page.getByLabel('Alior CSV').setInputFiles({
    name: 'test.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('statement')
  });
  await page.getByRole('checkbox', { name: /this export contains only/i }).check();
  await page.getByRole('button', { name: 'Preview changes' }).click();
  await expect(
    page.getByText('Matched to an existing expense account by card merchant.')
  ).toBeVisible();
  await expect(
    page.getByText('Possible transfer between your accounts.', { exact: true })
  ).toBeVisible();
  expect(writes).toBe(0);
  await page.getByText('Reasons for review', { exact: true }).click();
  await expect(
    page.getByText('2 · Several Firefly accounts match this card merchant.')
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Apply 1 change', exact: true })).toBeVisible();
  const group = page.getByRole('combobox', { name: 'Allegro · 2 payments' });
  await expect(group.getByRole('option', { name: /Allegro · #21.*4 existing/ })).toHaveCount(1);
  await group.selectOption('22');
  await expect(page.getByText('1 to review', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Apply 3 changes', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(jobId));
  await expect(page.getByText('3 applied · 0 failed')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Transaction #11' })).toHaveAttribute(
    'href',
    'https://firefly.example/transactions/show/11'
  );
  await page.reload();
  await expect(page.getByText('3 applied · 0 failed')).toBeVisible();
  expect(reads).toBeGreaterThanOrEqual(2);
  expect(writes).toBe(1);
});
