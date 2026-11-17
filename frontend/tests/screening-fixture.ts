import type { Page } from '@playwright/test';
import type { components } from '../src/lib/api/schema';

type Transaction = components['schemas']['SimplifiedTx'];
export const longCategory =
  'Household expenses / Groceries and everyday supplies for the whole family';
export const transactions: Transaction[] = [
  {
    id: 101,
    date: '2026-10-05',
    amount: 123.45,
    currency_code: 'PLN',
    currency_symbol: 'zł',
    type: 'withdrawal',
    description:
      'Weekly groceries and household supplies — payment at the neighbourhood supermarket',
    tags: ['blik_done', 'shopping'],
    notes: 'Receipt reference: DEMO-2026-101\nWeekly household shopping',
    category: null,
    source_account: {
      id: 11,
      name: 'Everyday household account shared with family',
      type: 'asset',
      iban: 'PL00111122223333444455556666'
    },
    destination_account: {
      id: 12,
      name: 'Neighbourhood supermarket and household supplies',
      type: 'expense',
      iban: null
    },
    fx_amount: 28.5,
    fx_currency: 'EUR'
  },
  {
    id: 102,
    date: '2026-10-06',
    amount: 42,
    currency_code: 'PLN',
    currency_symbol: 'zł',
    type: 'withdrawal',
    description: 'Public transport monthly ticket',
    tags: [],
    notes: '',
    category: 'Transport',
    source_account: null,
    destination_account: null
  },
  {
    id: 103,
    date: '2026-10-07',
    amount: 89,
    currency_code: 'EUR',
    currency_symbol: '€',
    type: 'withdrawal',
    description: 'Home maintenance supplies',
    tags: ['allegro_done'],
    notes: '',
    category: null
  }
];

export async function mockScreening(page: Page, theme = 'wireframe') {
  await page.addInitScript((value) => localStorage.setItem('ff-toolkit-theme', value), theme);
  const token = `e30.${Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test`;
  await page
    .context()
    .addCookies([{ name: 'access_token', value: token, url: 'http://127.0.0.1:4173' }]);
  const state = {
    failUpdate: false,
    empty: false,
    failLoad: false,
    failSuggestions: false,
    delay: 0,
    transactions: structuredClone(transactions),
    writes: [] as string[],
    months: [] as string[]
  };
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      if (path.endsWith('/auth/refresh')) return route.fulfill({ json: { access_token: token } });
      if (path.endsWith('/me'))
        return route.fulfill({
          json: { id: 'tester', username: 'demo.operator', is_active: true, is_superuser: false }
        });
      if (path.endsWith('/vault/status'))
        return route.fulfill({ json: { configured: true, unlocked: true, expires_at: null } });
      if (path.endsWith('/system/version')) return route.fulfill({ json: { version: '0.3.13' } });
      if (path.endsWith('/screening')) {
        state.months.push(`${url.searchParams.get('year')}-${url.searchParams.get('month')}`);
        if (state.delay) await new Promise((done) => setTimeout(done, state.delay));
        if (state.failLoad)
          return route.fulfill({ status: 503, json: { detail: 'Screening unavailable' } });
        if (state.empty) return route.fulfill({ status: 204 });
        return route.fulfill({
          json: {
            year: 2026,
            month: 10,
            remaining: state.transactions.length,
            transactions: state.transactions,
            categories: [
              { id: 1, name: longCategory },
              { id: 2, name: 'Transport' }
            ]
          }
        });
      }
      if (path.endsWith('/category-suggestions')) {
        if (state.failSuggestions)
          return route.fulfill({ status: 503, json: { detail: 'Suggestions unavailable' } });
        const txId = Number(path.split('/')[3]);
        return route.fulfill({
          json: {
            suggestions: [
              {
                category_id: txId === 102 ? '2' : '1',
                category_name: txId === 102 ? 'Transport' : longCategory,
                score: 0.92,
                reason: 'Similar previously categorized transactions'
              }
            ]
          } satisfies components['schemas']['CategorySuggestionsResponse']
        });
      }
      if (route.request().method() === 'POST') {
        state.writes.push(`${path}${url.search}`);
        if (state.delay) await new Promise((done) => setTimeout(done, state.delay));
        if (state.failUpdate)
          return route.fulfill({ status: 503, json: { detail: 'Update unavailable' } });
        const txId = Number(path.split('/')[3]);
        if (path.includes('/category/') || url.searchParams.get('tag') === 'action_req') {
          state.transactions = state.transactions.filter((transaction) => transaction.id !== txId);
        }
        return route.fulfill({ status: 204 });
      }
      return route.fulfill({ json: {} });
    }
  );
  return state;
}
