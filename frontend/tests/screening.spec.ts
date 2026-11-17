import { expect, test } from '@playwright/test';
import { longCategory, mockScreening, transactions } from './screening-fixture';

let errors: string[] = [];
test.beforeEach(({ page }) => {
  errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
});
test.afterEach(() => expect(errors).toEqual([]));

for (const theme of ['wireframe', 'black']) {
  for (const width of [1440, 390]) {
    test(`${theme} screening at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: width === 1440 ? 900 : 844 });
      await mockScreening(page, theme);
      await page.goto('/tx/categorize?year=2026&month=10');
      await expect(page.getByText(transactions[0].description, { exact: false })).toBeVisible();
      await expect(
        page.getByRole('button', { name: `Apply suggestion: ${longCategory}`, exact: true })
      ).toBeEnabled();
      const category = page.getByRole('combobox', { name: 'Category', exact: true });
      await category.selectOption('1');
      await page.keyboard.press('Tab');
      await category.focus();
      await expect(category).toHaveCSS('outline-style', 'solid');
      await expect(page.getByText(`Selected: ${longCategory}`, { exact: true })).toBeVisible();
      const apply = await page
        .getByRole('button', { name: 'Apply category', exact: true })
        .boundingBox();
      expect(apply!.y + apply!.height).toBeLessThanOrEqual(width === 1440 ? 900 : 844);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true
      );
      await page.screenshot({ path: testInfo.outputPath('screening.png'), animations: 'disabled' });
      await page.getByText(/Source ·/).click();
      await expect(page.getByText('PL00111122223333444455556666', { exact: true })).toBeVisible();
      await page.getByText('Notes (2)', { exact: true }).click();
      await expect(page.getByText('Receipt reference: DEMO-2026-101')).toBeVisible();
    });
  }
}

for (const theme of ['wireframe', 'black']) {
  test(`${theme}: save, retry, tags and queue counters`, async ({ page }) => {
    const state = await mockScreening(page, theme);
    await page.goto('/tx/categorize?year=2026&month=10');
    const summary = page.getByRole('status', { name: 'Queue summary' });
    const category = page.getByRole('combobox', { name: 'Category', exact: true });
    await expect(summary).toContainText('3 remaining');
    await category.selectOption('1');
    await page.getByRole('button', { name: 'Next transaction' }).click();
    await expect(page.getByText('Position 2 of 3 in queue')).toBeVisible();
    await page.getByRole('button', { name: 'Previous transaction' }).click();
    await expect(category).toHaveValue('1');
    state.failUpdate = true;
    state.delay = 500;
    await page.getByRole('button', { name: 'Apply category', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Next transaction' })).toBeDisabled();
    await expect(page.getByRole('button', { name: 'Action Required', exact: true })).toBeDisabled();
    await expect(page.getByRole('alert')).toContainText('Update unavailable');
    await expect(category).toHaveValue('1');
    await expect(summary).toContainText('3 remaining');
    await expect(page.getByText('Transaction #101', { exact: true })).toBeVisible();
    state.failUpdate = false;
    state.delay = 0;
    await page.getByRole('button', { name: 'Apply category', exact: true }).click();
    await expect(page.getByText('Transaction #102', { exact: true })).toBeVisible();
    await expect(summary).toContainText('2 remaining');
    await expect(summary).toContainText('1 completed of 3 loaded');
    await expect(page.getByText('Position 1 of 2 in queue')).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Apply suggestion: Transport', exact: true })
    ).toBeVisible();
    await page.getByRole('button', { name: 'Rule Potential', exact: true }).click();
    await expect(
      page.getByLabel('Transaction tags').getByText('rule_potential', { exact: true })
    ).toBeVisible();
    await expect(summary).toContainText('2 remaining');
    state.failUpdate = true;
    await page.getByRole('button', { name: 'Action Required', exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Update unavailable');
    await expect(summary).toContainText('2 remaining');
    state.failUpdate = false;
    await page.getByRole('button', { name: 'Action Required', exact: true }).click();
    await expect(page.getByText('Transaction #103', { exact: true })).toBeVisible();
    await expect(summary).toContainText('1 remaining');
    await expect(summary).toContainText('2 completed of 3 loaded');
    await page
      .getByRole('button', { name: `Apply suggestion: ${longCategory}`, exact: true })
      .click();
    await expect(page.getByRole('heading', { name: 'No transactions left' })).toBeVisible();
    await expect(summary).toContainText('0 remaining');
    await expect(summary).toContainText('3 completed of 3 loaded');
    expect(state.writes).toEqual([
      '/api/tx/101/category/1',
      '/api/tx/101/category/1',
      '/api/tx/102/tag/?tag=rule_potential',
      '/api/tx/102/tag/?tag=action_req',
      '/api/tx/102/tag/?tag=action_req',
      '/api/tx/103/category/1'
    ]);
  });

  test(`${theme}: loading, failed queue, suggestions retry and empty month`, async ({
    page
  }, testInfo) => {
    const state = await mockScreening(page, theme);
    state.delay = 800;
    state.failLoad = true;
    await page.goto('/tx/categorize?year=2026&month=10');
    await expect(page.getByRole('status', { name: 'Loading transactions' })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('loading.png') });
    await expect(page.getByRole('alert')).toContainText('Screening unavailable');
    state.delay = 0;
    state.failLoad = false;
    state.failSuggestions = true;
    await page.getByRole('button', { name: 'Retry queue' }).click();
    await expect(page.getByText('Transaction #101', { exact: true })).toBeVisible();
    await expect(page.getByRole('alert')).toContainText('Suggestions unavailable');
    await expect(page.getByRole('combobox', { name: 'Category', exact: true })).toBeEnabled();
    state.failSuggestions = false;
    await page.getByRole('button', { name: 'Retry suggestions' }).click();
    await expect(
      page.getByRole('button', { name: `Apply suggestion: ${longCategory}`, exact: true })
    ).toBeEnabled();
    state.empty = true;
    await page.getByRole('button', { name: 'Next month', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'No transactions left' })).toBeVisible();
    await expect(page.getByRole('status', { name: 'Queue summary' })).toContainText(
      '0 completed of 0 loaded'
    );
    await page.screenshot({ path: testInfo.outputPath('empty.png') });
    await page.getByRole('combobox', { name: 'Month', exact: true }).selectOption('1');
    await page.getByRole('spinbutton', { name: 'Year', exact: true }).fill('2027');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await expect(page).toHaveURL(/year=2027&month=1/);
    await expect.poll(() => state.months.at(-1)).toBe('2027-1');
    await expect(page.getByRole('button', { name: 'Previous month', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: 'Previous month', exact: true }).click();
    await expect(page).toHaveURL(/year=2026&month=12/);
    await expect.poll(() => state.months.at(-1)).toBe('2026-12');
  });
}

test('long unbroken descriptions and account names wrap on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const state = await mockScreening(page);
  state.transactions[0].description = 'LongDescription'.repeat(30);
  state.transactions[0].amount = 0.12345678;
  state.transactions[0].currency_code = 'BTC';
  state.transactions[0].currency_symbol = '₿';
  state.transactions[0].source_account!.name = 'AccountName'.repeat(20);
  state.transactions[0].tags = ['LongTag'.repeat(20)];
  await page.goto('/tx/categorize?year=2026&month=10');
  await expect(page.getByRole('heading', { level: 3 })).toHaveText(
    state.transactions[0].description
  );
  await page.getByText(/Source ·/).click();
  await expect(page.getByText('PL00111122223333444455556666', { exact: true })).toBeVisible();
  const heading = await page.getByRole('heading', { level: 3 }).boundingBox();
  expect(heading!.x + heading!.width).toBeLessThanOrEqual(320);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
