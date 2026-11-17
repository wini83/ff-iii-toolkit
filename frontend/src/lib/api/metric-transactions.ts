import { apiRequest } from './auth';
import { normalizeApiError } from './errors';
import type { components } from './schema';

export type MetricTransactions = components['schemas']['MetricTransactionsResponse'];
export type MetricSort = 'date_desc' | 'date_asc' | 'amount_desc' | 'amount_asc';

export async function getMetricTransactions(
  provider: 'allegro' | 'blik',
  snapshot_timestamp: string,
  month: string | null,
  offset: number,
  sort: MetricSort
): Promise<MetricTransactions> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/{provider}/statistics/transactions', {
      headers,
      params: {
        path: { provider },
        query: { snapshot_timestamp, month, offset, limit: 25, sort }
      }
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Failed to load transactions', response.status);
  return data;
}
