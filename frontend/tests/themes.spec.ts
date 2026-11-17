import { expect, test } from '@playwright/test';

for (const [stored, expected] of [
  ['light', 'wireframe'],
  ['dark', 'black'],
  ['wireframe', 'wireframe'],
  ['black', 'black']
] as const) {
  test(`migrate ${stored}, switch and persist across public routes`, async ({ page }) => {
    await page.addInitScript((value) => {
      if (!sessionStorage.getItem('theme-seeded')) {
        localStorage.setItem('ff-toolkit-theme', value);
        sessionStorage.setItem('theme-seeded', 'yes');
      }
    }, stored);
    const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
    await page
      .context()
      .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
    await page.route(
      (url) => url.pathname.startsWith('/api/'),
      (route) => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
        return route.fulfill({
          json: path.endsWith('/me')
            ? { id: 'tester', username: 'tester', is_active: true, is_superuser: false }
            : path.endsWith('/vault/status')
              ? { configured: true, unlocked: false, expires_at: null }
              : {}
        });
      }
    );
    await page.goto('/');
    await expect(page.locator('html')).toHaveAttribute('data-theme', expected);
    expect(await page.evaluate(() => localStorage.getItem('ff-toolkit-theme'))).toBe(expected);
    await page.getByRole('button', { name: /Switch to .* theme/ }).click();
    const next = expected === 'black' ? 'wireframe' : 'black';
    await expect(page.locator('html')).toHaveAttribute('data-theme', next);
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', next);
    await page.goto('/login');
    await expect(page.locator('html')).toHaveAttribute('data-theme', next);
  });
}

for (const colorScheme of ['light', 'dark'] as const) {
  test(`system ${colorScheme} works with blocked storage`, async ({ page }) => {
    await page.emulateMedia({ colorScheme });
    await page.addInitScript(() => {
      Storage.prototype.getItem = () => {
        throw new Error('Storage blocked');
      };
      Storage.prototype.setItem = () => {
        throw new Error('Storage blocked');
      };
    });
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto('/login');
    await expect(page.locator('html')).toHaveAttribute(
      'data-theme',
      colorScheme === 'dark' ? 'black' : 'wireframe'
    );
    expect(errors).toEqual([]);
  });
}

for (const theme of ['wireframe', 'black'] as const) {
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 390, height: 844 }
  ]) {
    test(`${theme} components at ${viewport.width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await page.addInitScript((value) => localStorage.setItem('ff-toolkit-theme', value), theme);
      const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
      await page
        .context()
        .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
      const user = {
        id: 'demo-user',
        username: 'demo.operator',
        is_active: true,
        is_superuser: true
      };
      await page.route(
        (url) => url.pathname.startsWith('/api/'),
        (route) => {
          const path = new URL(route.request().url()).pathname;
          if (path.endsWith('/auth/refresh'))
            return route.fulfill({ json: { access_token: token } });
          if (route.request().method() === 'POST') {
            return route.fulfill({ status: 400, json: { detail: 'Example validation error' } });
          }
          return route.fulfill({
            json: path.endsWith('/me')
              ? user
              : path.endsWith('/users')
                ? [user]
                : path.endsWith('/vault/status')
                  ? { configured: true, unlocked: false, expires_at: null }
                  : {}
          });
        }
      );
      await page.goto('/users');
      await expect(
        page.getByText('demo.operator', { exact: true }).filter({ visible: true })
      ).toBeVisible();
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await page.screenshot({ path: testInfo.outputPath('table.png'), fullPage: true });
      await page.getByRole('button', { name: 'Create user', exact: true }).first().click();
      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      const input = dialog.getByPlaceholder('e.g. john.doe');
      await input.fill('example.operator.with.a.long.name');
      await input.focus();
      await page.screenshot({ path: testInfo.outputPath('form-focus.png'), fullPage: true });
      await dialog.getByRole('button', { name: 'Create user', exact: true }).click();
      await expect(dialog.getByRole('alert')).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath('form-error.png'), fullPage: true });
    });
  }
}
