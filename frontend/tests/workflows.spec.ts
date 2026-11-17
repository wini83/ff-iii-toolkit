import { expect, test, type Page } from '@playwright/test';

async function mockWorkflows(page: Page, theme: string) {
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  await page.addInitScript((value) => localStorage.setItem('ff-toolkit-theme', value), theme);
  const user = {
    id: 'synthetic-user',
    username: 'demo.operator',
    is_superuser: true,
    is_active: true
  };
  const metadata = {
    id: 'synthetic',
    short_id: 'demo',
    type: 'allegro',
    alias: 'Synthetic marketplace',
    external_username: 'demo.account',
    usage_count: 2,
    last_used_at: null,
    created_at: '2026-11-17T10:00:00Z'
  };
  const payment = {
    external_id: 'synthetic-payment',
    external_short_id: 'demo-payment',
    amount: 10,
    date: '2026-11-17',
    details: ['Synthetic order'],
    is_balanced: true,
    allegro_login: 'demo.account'
  };
  const state = { unlocked: true, configured: true, empty: false, fail: '', calls: [] as string[] };
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      state.calls.push(path);
      if (state.fail && path.includes(state.fail))
        return route.fulfill({ status: 503, json: { detail: 'Synthetic service unavailable' } });
      if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
      if (path.endsWith('/me')) return route.fulfill({ json: user });
      if (path.includes('/vault/'))
        return route.fulfill({
          json: { configured: state.configured, unlocked: state.unlocked, expires_at: null }
        });
      if (path.endsWith('/users/audit-log'))
        return route.fulfill({ json: { items: [], offset: 0, limit: 20 } });
      if (path.endsWith('/users')) return route.fulfill({ json: [user] });
      if (path.endsWith('/allegro/secrets') || path === '/api/user-secrets')
        return route.fulfill({ json: state.empty ? [] : [metadata] });
      if (path.endsWith('/payments')) return route.fulfill({ json: [payment] });
      if (path.endsWith('/matches'))
        return route.fulfill({
          json: {
            login: 'demo.account',
            file_id: 'synthetic',
            decoded_name: 'Synthetic file',
            records_in_file: 1,
            payments_fetched: 1,
            transactions_found: 1,
            transactions_not_matched: 0,
            transactions_with_one_match: 1,
            transactions_with_many_matches: 0,
            fetch_seconds: 0.1,
            unmatched_payments: [],
            content: [
              {
                status: 'new',
                tx: {
                  id: 1,
                  date: '2026-11-17',
                  amount: 10,
                  currency_symbol: 'PLN',
                  description: 'Synthetic transaction',
                  tags: [],
                  notes: null
                },
                matches: [
                  {
                    ...payment,
                    match_id: 'demo-match',
                    operation_amount: 10,
                    operation_currency: 'PLN',
                    recipient: 'Synthetic shop',
                    sender: 'Demo account',
                    recipient_account: '',
                    sender_account: '',
                    details: path.includes('allegro') ? payment.details : 'Synthetic order'
                  }
                ]
              }
            ]
          }
        });
      if (path === '/api/blik_files' && route.request().method() === 'POST')
        return route.fulfill({ json: { id: 'synthetic' } });
      if (path === '/api/blik_files/synthetic')
        return route.fulfill({
          json: {
            decoded_name: 'Synthetic file',
            size: 1,
            content: [
              {
                date: '2026-11-17',
                amount: 10,
                account_currency: 'PLN',
                recipient: 'Synthetic shop',
                details: 'Synthetic purchase'
              }
            ]
          }
        });
      if (path.endsWith('/velobank/files/synthetic'))
        return route.fulfill({
          json: {
            file_id: 'synthetic',
            expires_at: '2099-01-01T12:00:00Z',
            account_iban: 'DEMO-ACCOUNT',
            account_name: 'Demo bank',
            start_date: '2026-11-01',
            end_date: '2026-11-17',
            record_count: 1,
            totals: [{ currency: 'PLN', debits: '-10.00', credits: '0.00' }],
            warnings: [],
            preview: [
              {
                id: 'row-1',
                date: '2026-11-17',
                booking_date: '2026-11-17',
                amount: '-10.00',
                currency: 'PLN',
                payee: 'Synthetic shop',
                description: 'Synthetic purchase',
                source_account: 'Demo bank',
                destination_account: 'Demo shop',
                source_iban: 'DEMO-ACCOUNT',
                destination_iban: '',
                needs_review: false
              }
            ]
          }
        });
      if (path.endsWith('/velobank/mappings')) return route.fulfill({ json: { accounts: {} } });
      if (path.endsWith('/velobank/accounts')) return route.fulfill({ json: [] });
      return route.fulfill({ json: {} });
    }
  );
  return state;
}

const routes = [
  ['/login', 'Login'],
  ['/set-password', 'Set password'],
  ['/set-password?token=synthetic-invite', 'Set password'],
  ['/blik/upload', 'Wyślij plik Alior BLIK (.csv)'],
  ['/blik/file', 'File Preview'],
  ['/allegro/accounts', 'Allegro accounts'],
  ['/allegro/synthetic/payments', 'Allegro payments'],
  ['/allegro/synthetic/matches', 'Allegro matches'],
  ['/blik/file/synthetic/match', 'Blik matches'],
  ['/tools/velobank', 'VeloBank Import'],
  ['/tools/velobank?file_id=synthetic', 'VeloBank Import'],
  ['/settings/secrets', 'User Secrets'],
  ['/profile', 'Profile'],
  ['/users', 'Users'],
  ['/audit-log', 'Audit log']
] as const;

for (const theme of ['wireframe', 'black']) {
  for (const width of [1440, 390]) {
    test(`remaining workflows in ${theme} at ${width}px`, async ({ page }, testInfo) => {
      test.setTimeout(120000);
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await mockWorkflows(page, theme);
      for (const [route, heading] of routes) {
        await page.goto(route);
        await expect(
          page.getByRole('heading', { name: heading, exact: true }).first()
        ).toBeVisible();
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        // Let async forms/tables settle rather than recording their skeletons.
        await expect(page.locator('.skeleton:visible')).toHaveCount(0);
        await expect(
          page.locator('[class*="bg-gradient-"], [class*="shadow-xl"], [class*="shadow-2xl"]')
        ).toHaveCount(0);
        const content = page.locator('#main-content');
        const surface = (await content.count()) ? content : page.locator('body');
        expect
          .soft(
            await surface.evaluate((element) => element.scrollWidth <= element.clientWidth + 1),
            route
          )
          .toBe(true);
        await page.screenshot({
          path: testInfo.outputPath(`${route.replace(/[^a-z0-9]+/gi, '-')}.png`),
          fullPage: true,
          animations: 'disabled'
        });
      }
      // Real client navigation exercises preview loading without a live backend.
      await page.goto('/blik/file');
      await page.getByPlaceholder('Enter ID').fill('synthetic');
      await page.getByRole('button', { name: 'Go', exact: true }).click();
      await expect(page.getByText('Synthetic purchase', { exact: true })).toBeVisible();
      await expect(page.locator('.steps')).toBeVisible();
      await expect(
        page.locator('.steps').getByRole('link', { name: 'Upload', exact: true })
      ).toHaveAttribute('href', '/blik/upload');
      await expect(
        page.getByRole('button', { name: 'Match Transactions (1)', exact: true }).first()
      ).toBeVisible();
      await page.screenshot({
        path: testInfo.outputPath('blik-preview.png'),
        fullPage: true,
        animations: 'disabled'
      });
      expect(errors).toEqual([]);
    });
  }
}

for (const theme of ['wireframe', 'black']) {
  test(`vault, import errors and account states in ${theme}`, async ({ page }, testInfo) => {
    const state = await mockWorkflows(page, theme);
    state.unlocked = false;
    await page.goto('/settings/secrets');
    await expect(page.getByRole('heading', { name: 'Unlock vault', exact: true })).toBeVisible();
    await page.locator('main').getByPlaceholder('Enter vault passphrase').fill('synthetic-only');
    await expect(page.locator('main').getByPlaceholder('Enter vault passphrase')).toHaveAttribute(
      'type',
      'password'
    );
    await page.screenshot({
      path: testInfo.outputPath('vault-locked.png'),
      animations: 'disabled'
    });
    state.configured = false;
    await page.reload();
    await expect(page.getByRole('button', { name: 'Configure vault', exact: true })).toBeVisible();
    state.configured = true;
    state.unlocked = true;
    await page.reload();
    await page.getByRole('button', { name: 'Create secret', exact: true }).click();
    await expect(page.locator('dialog[open]')).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath('secret-dialog.png'),
      animations: 'disabled'
    });
    await page.locator('dialog[open]').getByRole('button', { name: 'Cancel', exact: true }).click();
    await page
      .getByRole('button', { name: 'Delete', exact: true })
      .filter({ visible: true })
      .first()
      .click();
    await expect(page.locator('dialog[open]')).toBeVisible();
    await expect(
      page.locator('dialog[open]').getByRole('button', { name: 'Delete', exact: true })
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath('delete-confirmation.png'),
      animations: 'disabled'
    });
    await page.locator('dialog[open]').getByRole('button', { name: 'Cancel', exact: true }).click();
    state.empty = true;
    await page.goto('/allegro/accounts');
    await expect(
      page.getByRole('heading', { name: 'No Allegro secrets yet', exact: true })
    ).toBeVisible();
    state.fail = '/allegro/secrets';
    await page.reload();
    await expect(
      page.getByRole('alert').filter({ hasText: 'Synthetic service unavailable' })
    ).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath('account-error.png'),
      animations: 'disabled'
    });
    state.fail = '/blik_files';
    await page.goto('/blik/upload');
    await page.locator('input[type=file]').setInputFiles({
      name: 'synthetic.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('synthetic')
    });
    await page.getByRole('button', { name: 'Wyślij plik', exact: true }).click();
    await expect(page.getByText('Synthetic service unavailable', { exact: true })).toBeVisible();
    await page.screenshot({
      path: testInfo.outputPath('upload-error.png'),
      animations: 'disabled'
    });
  });
}
