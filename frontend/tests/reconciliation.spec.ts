import { expect, test, type Page } from '@playwright/test';

function readSelection<T>(body: string, field: string): T[] {
  const match = body.match(new RegExp(`name="${field}"\\r\\n\\r\\n([^\\r\\n]+)`));
  return match ? JSON.parse(match[1]) : [];
}

async function setup(
  page: Page,
  fail = false,
  groupOptions?: { additional?: number; failBatch?: number }
) {
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const writes: string[] = [];
  const previews: string[] = [];
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
  const evidence = (id: number, description: string, date = '2026-11-16') => ({
    id,
    date,
    description
  });
  const group = (
    id: string,
    amount: string,
    bank: ReturnType<typeof evidence>[],
    firefly: ReturnType<typeof candidate>[],
    eligibleIds: number[],
    exactCount: number,
    containsTransfer = false
  ) => ({
    id,
    date: '2026-11-16',
    amount,
    currency: 'PLN',
    bank,
    firefly,
    eligible_ids: eligibleIds,
    exact_description_count: exactCount,
    contains_transfer: containsTransfer
  });
  const committed = new Set<number>();
  const groups = groupOptions
    ? [
        group(
          'b'.repeat(64),
          '-30.04',
          [evidence(20, 'VEMAT'), evidence(21, 'VEMAT')],
          [candidate(20, { description: 'VEMAT' }), candidate(21, { description: 'VEMAT' })],
          [20, 21],
          2
        ),
        group(
          'c'.repeat(64),
          '-25.00',
          [evidence(22, 'Fikcyjny zakup')],
          [candidate(22, { description: 'Płatność BLIK w internecie', amount: '-25.00' })],
          [22],
          0
        ),
        group(
          'd'.repeat(64),
          '-15.00',
          [evidence(23, 'Fikcyjny sklep')],
          [candidate(23, { amount: '-15.00' }), candidate(24, { amount: '-15.00' })],
          [],
          0
        ),
        group(
          'e'.repeat(64),
          '-10.00',
          [evidence(25, 'Przelew własny')],
          [candidate(25, { amount: '-10.00', type: 'transfer', can_mark: false })],
          [],
          0,
          true
        )
      ]
    : [];
  for (let index = 0; index < (groupOptions?.additional ?? 0); index += 1) {
    const id = 1000 + index;
    const amount = `-${id}.00`;
    groups.push(
      group(
        id.toString(16).padStart(64, '0'),
        amount,
        [evidence(id, 'Fikcyjna operacja')],
        [candidate(id, { amount })],
        [id],
        0
      )
    );
  }
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
      if (path.endsWith('/reconciliation/statement-period'))
        return route.fulfill({
          json: { date_from: '2026-11-01', date_to: '2026-11-30', source: 'transactions' }
        });
      if (path.endsWith('/reconciliation/preview')) {
        previews.push(route.request().postData() ?? '');
        return route.fulfill({
          json: {
            revision: 'a'.repeat(64),
            reconciliation_groups: groups.map((item) => ({
              ...item,
              firefly: item.firefly.map((line) => ({ ...line, reconciled: committed.has(line.id) }))
            })),
            operation_groups: [
              {
                amount: '-6.00',
                currency: 'PLN',
                reason: 'card_prefix',
                compared_key: 'fikcyjny sklep k.2',
                bank: [
                  evidence(9, 'Fikcyjny sklep K.2 GDAŃSK PL'),
                  evidence(10, 'Fikcyjny sklep K.2 GDAŃSK PL')
                ],
                firefly: [
                  evidence(9, 'Transakcja kartą debetową, Fikcyjny sklep K.2'),
                  evidence(10, 'Transakcja kartą debetową, Fikcyjny sklep K.2')
                ]
              },
              {
                amount: '-12.00',
                currency: 'PLN',
                reason: 'exact',
                excess_count: 1,
                bank: [evidence(11, 'Fikcyjny sklep B')],
                firefly: [evidence(11, 'Fikcyjny sklep B'), evidence(12, 'Fikcyjny sklep B')]
              },
              {
                amount: '-20.00',
                currency: 'PLN',
                reason: 'date_shift',
                bank: [evidence(12, 'Fikcyjny sklep C')],
                firefly: [evidence(13, 'Fikcyjny sklep C', '2026-11-17')]
              }
            ],
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
              ),
              row(
                8,
                'review',
                [
                  candidate(8, {
                    description: 'Transakcja kartą debetową, Invoice 8 POZNAŃ PL',
                    title_matches: false,
                    can_mark: false,
                    normalized_description_key: 'invoice 8'
                  })
                ],
                'description_normalization'
              )
            ],
            firefly_only: [candidate(6)],
            totals: [
              {
                currency: 'PLN',
                bank_total: '-150.20',
                firefly_total: '-150.200000000000',
                difference: '0E-12'
              }
            ],
            skipped_multipart: 2,
            skipped_invalid: 0,
            skipped_zero_amount: 1,
            excluded_foreign_transfers: 1
          }
        });
      }
      if (path.endsWith('/reconciliation/apply')) {
        const body = route.request().postData() ?? '';
        writes.push(body);
        if (fail || writes.length === groupOptions?.failBatch) {
          return route.fulfill({
            status: 409,
            json: { detail: 'Comparison changed. Compare again.' }
          });
        }
        const ids = readSelection<number>(body, 'selected_ids');
        const groupIds = readSelection<string>(body, 'selected_group_ids');
        const targets = new Set([
          ...ids,
          ...groups
            .filter((item) => groupIds.includes(item.id))
            .flatMap((item) => item.eligible_ids)
        ]);
        for (const id of targets) committed.add(id);
        return route.fulfill({
          json: { outcomes: Array.from(targets, (id) => ({ id, status: 'reconciled' })) }
        });
      }
      return route.fulfill({ json: {} });
    }
  );
  return { writes, errors, previews };
}

async function uploadStatement(page: Page) {
  const input = page.getByLabel('Alior CSV', { exact: false });
  await expect(input).toBeEnabled();
  await input.setInputFiles({
    name: 'statement.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('Synthetic statement')
  });
}

async function compare(page: Page) {
  await page.goto('/tx/reconcile');
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('1');
  await uploadStatement(page);
  await expect(page.getByRole('region', { name: 'Statement comparison results' })).toBeVisible();
}

test('shows the inferred period before account selection and supports an override', async ({
  page
}) => {
  const mock = await setup(page);
  await page.goto('/tx/reconcile');
  await uploadStatement(page);
  await expect(page.getByText('Od pierwszej do ostatniej operacji w CSV')).toBeVisible();
  expect(mock.previews).toHaveLength(0);

  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('1');
  const results = page.getByRole('region', { name: 'Statement comparison results' });
  await expect(results).toBeVisible();
  expect(mock.previews[0]).toContain('name="date_from"\r\n\r\n2026-11-01');
  await page.getByText('Zmień okres lub tolerancję', { exact: true }).click();
  await page.getByLabel('Period from').fill('2026-11-05');
  await expect(results).toHaveCount(0);
  await page.getByRole('button', { name: 'Compare statement', exact: true }).click();
  await expect(results).toContainText('2026-11-05 – 2026-11-30');
  expect(mock.previews[1]).toContain('name="date_from"\r\n\r\n2026-11-05');
  expect(mock.writes).toEqual([]);
});

test('only unambiguous supported matches can be marked; apply consumes the preview', async ({
  page
}) => {
  const mock = await setup(page);
  await compare(page);
  const results = page.getByRole('region', { name: 'Statement comparison results' });
  await expect(results.getByText('Bank only: 1')).toBeVisible();
  await expect(results.getByText('Pominięto operacje CSV o kwocie 0 (1).')).toBeVisible();
  await expect(results.getByText('Firefly only: 1')).toBeVisible();
  await expect(page.getByLabel('Reconcile transaction #2', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Reconcile transaction #4', { exact: true })).toHaveCount(0);
  await expect(page.getByLabel('Reconcile transaction #5', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Mark selected', exact: false })).toBeDisabled();
  expect(mock.writes).toEqual([]);
  await page.getByLabel('Select verified matches').check();
  await expect(page.getByRole('button', { name: 'Mark selected as reconciled (1)' })).toBeEnabled();
  await page.getByLabel('Show').selectOption('firefly_only');
  await expect(page.getByLabel('Select verified matches')).toBeDisabled();
  await expect(
    page.getByRole('button', { name: 'Mark selected as reconciled (0)' })
  ).toBeDisabled();
  expect(mock.writes).toEqual([]);
  await page.getByLabel('Show').selectOption('matched');
  await expect(page.getByLabel('Reconcile transaction #1', { exact: true })).not.toBeChecked();
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

test('description suggestions show the evidence and cannot be selected', async ({ page }) => {
  const mock = await setup(page);
  await compare(page);
  const filter = page.getByLabel('Show');
  await expect(filter.getByRole('option', { name: 'Zgodny punkt sprzedaży (1)' })).toHaveCount(1);
  await filter.selectOption('description_normalization');
  const comparison = page.getByRole('list', { name: 'Bank statement comparison' });
  await expect(comparison.getByRole('listitem')).toHaveCount(1);
  await expect(comparison.getByText('Invoice 8', { exact: true })).toBeVisible();
  await expect(comparison).toContainText('Transakcja kartą debetową, Invoice 8 POZNAŃ PL');
  await expect(comparison).toContainText('removing the debit-card prefix');
  await expect(comparison).toContainText('Compared key invoice 8');
  await expect(comparison.getByRole('checkbox')).toHaveCount(0);
  await expect(page.getByLabel('Select verified matches')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Mark selected', exact: false })).toBeDisabled();
  expect(mock.writes).toEqual([]);
});

test('operation balances show quantities, duplicate candidates and date shifts', async ({
  page
}) => {
  const mock = await setup(page);
  await compare(page);
  const balance = page.getByRole('region', { name: 'Bilans operacji w podglądzie' });
  await expect(balance).toContainText('Nadmiarowe w Firefly (1)');
  await expect(balance).toContainText('Możliwy duplikat');
  await expect(balance).toContainText('Zgodna liczba operacji · Niejednoznaczne pary');
  await balance.getByText('Bank: 2 × 6 zł · Firefly: 2 × 6 zł', { exact: true }).click();
  await expect(
    balance.getByText('Fikcyjny sklep K.2 GDAŃSK PL', { exact: true }).first()
  ).toBeVisible();
  await expect(
    balance.getByText('Transakcja kartą debetową, Fikcyjny sklep K.2', { exact: true }).first()
  ).toBeVisible();
  await balance.getByRole('combobox', { name: 'Grupy operacji' }).selectOption('date_shift');
  await expect(balance.getByRole('listitem')).toHaveCount(1);
  await expect(balance).toContainText('Kandydaci z przesunięciem daty · Do sprawdzenia');
  await expect(balance.getByRole('checkbox')).toHaveCount(0);
  expect(mock.writes).toEqual([]);
});

test('selects balanced VEMAT and BLIK groups and submits their IDs through the existing button', async ({
  page
}) => {
  const mock = await setup(page, false, {});
  await compare(page);
  const groups = page.getByRole('region', { name: 'Uzgadnianie grup operacji' });
  await expect(groups.getByRole('checkbox')).toHaveCount(0);
  await expect(groups).toContainText('Nadmiarowe w Firefly (1)');
  await expect(groups).toContainText('Transfer w grupie');
  await groups.getByRole('combobox', { name: 'Pokaż grupy' }).selectOption('all');
  await expect(groups.getByRole('checkbox')).toHaveCount(2);
  await expect(groups).toContainText('Dokładna zgodność opisów (2)');
  await expect(groups.getByText(/Zgodna liczba i kwota/)).toHaveCount(3);
  await groups.getByRole('checkbox', { name: /-30\.04/ }).check();
  await groups.getByRole('checkbox', { name: /-25\.00/ }).check();
  const reconcile = page.getByRole('button', { name: 'Mark selected as reconciled (3)' });
  const confirmation = page.getByRole('dialog', { name: 'Potwierdź kompletność wyciągu' });
  await reconcile.click();
  await expect(confirmation).toContainText('2026-11-01 – 2026-11-30');
  await confirmation.getByRole('button', { name: 'Anuluj' }).click();
  await expect(confirmation).not.toBeVisible();
  expect(mock.writes).toEqual([]);
  await reconcile.click();
  await confirmation.getByRole('button', { name: 'Potwierdzam, uzgodnij (3)' }).click();
  await expect(page.getByRole('region', { name: 'Reconciliation outcomes' })).toBeVisible();
  expect(mock.writes).toHaveLength(1);
  expect(mock.writes[0]).toContain('name="selected_ids"\r\n\r\n[]');
  expect(mock.writes[0]).toContain('name="statement_complete"\r\n\r\ntrue');
  expect(mock.writes[0]).toContain(
    `name="selected_group_ids"\r\n\r\n${JSON.stringify(['b'.repeat(64), 'c'.repeat(64)])}`
  );
});

test('bulk selection saves more than 100 matching transactions and refreshes the review list', async ({
  page
}) => {
  const mock = await setup(page, false, { additional: 120 });
  await compare(page);
  const groups = page.getByRole('region', { name: 'Uzgadnianie grup operacji' });
  await expect(groups.getByRole('listitem')).toHaveCount(2);
  const selectAll = page.getByRole('checkbox', { name: 'Zaznacz wszystkie zgodne (123)' });
  await selectAll.check();
  await expect(
    page.getByRole('button', { name: 'Mark selected as reconciled (123)' })
  ).toBeEnabled();
  await selectAll.uncheck();
  await expect(
    page.getByRole('button', { name: 'Mark selected as reconciled (0)' })
  ).toBeDisabled();
  expect(mock.writes).toEqual([]);
  await selectAll.check();
  await page.getByRole('button', { name: 'Mark selected as reconciled (123)' }).click();
  await page.getByRole('button', { name: 'Potwierdzam, uzgodnij (123)' }).click();

  await expect(page.getByRole('region', { name: 'Reconciliation outcomes' })).toContainText(
    'Uzgodnione (123)'
  );
  await expect(page.getByRole('checkbox', { name: 'Zaznacz wszystkie zgodne (0)' })).toBeDisabled();
  await expect(groups.getByRole('listitem')).toHaveCount(2);
  const batches = mock.writes.map((body) => readSelection<string>(body, 'selected_group_ids'));
  expect(batches.map((batch) => batch.length)).toEqual([99, 23]);
  expect(new Set(batches.flat()).size).toBe(122);
  expect(batches.flat()).not.toContain('d'.repeat(64));
  expect(batches.flat()).not.toContain('e'.repeat(64));
});

test('bulk reconciliation stops after a stale batch and preserves confirmed outcomes', async ({
  page
}) => {
  const mock = await setup(page, false, { additional: 202, failBatch: 2 });
  await compare(page);
  await page.getByRole('checkbox', { name: 'Zaznacz wszystkie zgodne (205)' }).check();
  await page.getByRole('button', { name: 'Mark selected as reconciled (205)' }).click();
  await page.getByRole('button', { name: 'Potwierdzam, uzgodnij (205)' }).click();

  await expect(page.getByRole('alert')).toContainText('Comparison changed');
  await expect(page.getByRole('region', { name: 'Reconciliation outcomes' })).toContainText(
    'Uzgodnione (100)'
  );
  await expect(page.getByText('Nie wysłano kolejnych partii (1)', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: /Mark selected as reconciled/ })).toHaveCount(0);
  expect(mock.writes).toHaveLength(2);
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
  await page.getByText('Zmień okres lub tolerancję', { exact: true }).click();
  await page.getByLabel('Date tolerance (days)').fill('2');
  await page.getByRole('button', { name: 'Compare statement', exact: true }).focus();
  await expect(page.getByRole('region', { name: 'Statement comparison results' })).toHaveCount(0);
  expect(mock.writes).toEqual([]);
  expect(mock.errors).toEqual([]);
});
