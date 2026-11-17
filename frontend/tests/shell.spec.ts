import { expect, test, type Page } from '@playwright/test';

let pageErrors: string[] = [];
test.beforeEach(({ page }) => {
  pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
});
test.afterEach(() => expect(pageErrors).toEqual([]));

async function mockSession(page: Page, theme: string, admin = true) {
  await page.addInitScript((value) => localStorage.setItem('ff-toolkit-theme', value), theme);
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const user = {
    id: 'demo-user',
    username: 'demo.operator.with.a.long.session.name',
    is_active: true,
    is_superuser: admin
  };
  let unlocked = false;
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/vault/unlock')) unlocked = true;
      if (path.endsWith('/vault/lock')) unlocked = false;
      const json = path.endsWith('/auth/refresh')
        ? { access_token: token }
        : path.endsWith('/me')
          ? user
          : path.endsWith('/users')
            ? [user]
            : path.includes('/vault/')
              ? { configured: true, unlocked, expires_at: unlocked ? '2099-01-01T12:00:00Z' : null }
              : path.endsWith('/system/version')
                ? { version: '0.3.13' }
                : [];
      await route.fulfill({ json });
    }
  );
}

for (const theme of ['wireframe', 'black']) {
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 390, height: 844 }
  ]) {
    test(`${theme} shell at ${viewport.width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await mockSession(page, theme);
      await page.goto('/users');
      const sidebar = page.locator('#app-navigation');
      const nav = page.getByRole('navigation', { name: 'Main navigation' });
      await expect(page.getByRole('heading', { level: 1 })).toHaveText('Administration / Users');
      await expect(page.getByRole('button', { name: 'Account menu' })).toBeVisible();
      await expect(
        page.getByRole('button', { name: 'Create user', exact: true }).first()
      ).toBeEnabled();
      await expect(sidebar.getByText('Toolkit · v0.3.13', { exact: true })).toHaveCount(1);
      if (viewport.width < 1024) {
        await page.screenshot({ path: testInfo.outputPath('mobile-page.png') });
        await page.getByRole('button', { name: 'Open navigation' }).click();
        await expect(
          page.getByRole('button', { name: 'Close navigation', exact: true })
        ).toBeFocused();
      }
      await expect(sidebar).toHaveCSS('width', '232px');
      await expect(sidebar.getByText('Toolkit · v0.3.13', { exact: true })).toBeVisible();
      await expect(nav.locator('[aria-current="page"]')).toHaveText('Users');
      const active = nav.getByRole('link', { name: 'Users', exact: true });
      await page.keyboard.press('Tab');
      await active.focus();
      await expect(active).toHaveCSS('outline-style', 'solid');
      await expect(active).toHaveCSS('outline-width', '2px');
      expect(await nav.getByRole('link').count()).toBe(11);
      await page.screenshot({ path: testInfo.outputPath('navigation-focus.png') });
      await nav.getByText('Transactions', { exact: true }).click();
      await expect(nav.getByRole('link', { name: 'Categorize', exact: true })).toBeHidden();
      await nav.getByText('Transactions', { exact: true }).click();
      await expect(nav.getByRole('link', { name: 'Categorize', exact: true })).toBeVisible();
      expect(
        await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
      ).toBe(true);
      await page.setViewportSize({ width: viewport.width, height: 400 });
      const lastLink = nav.getByRole('link', { name: 'Audit log', exact: true });
      await lastLink.focus();
      await expect(lastLink).toBeInViewport();
      await expect(sidebar.getByText('Toolkit · v0.3.13', { exact: true })).toBeInViewport();
      expect(await nav.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
      await page.screenshot({ path: testInfo.outputPath('short-navigation.png') });
      if (viewport.width < 1024) {
        await page.keyboard.press('Tab');
        await expect(sidebar.getByRole('link', { name: 'Firefly Toolkit' })).toBeFocused();
        await page.keyboard.press('Escape');
        await expect(page.getByRole('button', { name: 'Open navigation' })).toBeFocused();
        await page.getByRole('button', { name: 'Open navigation' }).click();
        await nav.getByRole('link', { name: 'Users', exact: true }).click();
        await expect(page.getByRole('button', { name: 'Open navigation' })).toHaveAttribute(
          'aria-expanded',
          'false'
        );
      }
    });
  }
}

test('global controls and Vault actions on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 640 });
  await mockSession(page, 'wireframe');
  await page.goto('/users');
  await page.getByRole('button', { name: 'Vault status' }).click();
  await expect(page.getByText('Vault locked', { exact: true })).toBeVisible();
  const panel = await page.locator('#vault-panel').boundingBox();
  expect(panel).not.toBeNull();
  expect(panel!.x).toBeGreaterThanOrEqual(0);
  expect(panel!.x + panel!.width).toBeLessThanOrEqual(320);
  expect(panel!.y + panel!.height).toBeLessThanOrEqual(640);
  await page.getByPlaceholder('Enter vault passphrase').fill('synthetic-test-passphrase');
  const unlockRequest = page.waitForRequest((req) => req.url().endsWith('/vault/unlock'));
  await page.getByRole('button', { name: 'Unlock', exact: true }).click();
  expect((await unlockRequest).postDataJSON()).toEqual({ passphrase: 'synthetic-test-passphrase' });
  await expect(page.getByText('Vault unlocked', { exact: true })).toBeVisible();
  await expect(page.getByText(/Vault session expires at/)).toBeVisible();
  await page.getByRole('button', { name: 'Lock', exact: true }).click();
  await expect(page.getByText('Vault locked', { exact: true })).toBeVisible();
  await page.locator('#vault-panel').getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Open settings' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true
  );
  await page.getByRole('button', { name: 'Vault status' }).click();
  await page.getByRole('button', { name: 'Switch to dark theme' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'black');
  await page.getByRole('button', { name: 'Account menu' }).click();
  await expect(page.getByRole('link', { name: 'Profile', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Secrets', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Log out', exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Profile', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Profile');
});

test('active route updates and regular users have no administration menu', async ({ page }) => {
  await mockSession(page, 'wireframe', false);
  await page.goto('/profile');
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(nav.getByText('Administration', { exact: true })).toHaveCount(0);
  await nav.getByRole('link', { name: 'Accounts', exact: true }).click();
  await expect(nav.locator('[aria-current="page"]')).toHaveText('Accounts');
  await nav.getByRole('link', { name: 'File Preview', exact: true }).click();
  await expect(nav.locator('[aria-current="page"]')).toHaveText('File Preview');
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
});
