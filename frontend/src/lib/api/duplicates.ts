import { apiRequest } from './auth';
import { normalizeApiError } from './errors';
import type { components } from './schema';

export type DuplicateOptions = components['schemas']['DuplicateOptions'];
export type DuplicateResults = components['schemas']['DuplicateResults'];
export type DuplicateQuery = {
  account_id: number;
  date_from: string;
  date_to: string;
  tolerance_days: number;
  snapshot_timestamp: string;
};

export async function getDuplicateOptions(): Promise<DuplicateOptions> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/tx/duplicates/options', { headers })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Failed to load transactions', response.status);
  return data;
}

export async function refreshDuplicateOptions(): Promise<DuplicateOptions> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/tx/duplicates/options/refresh', { headers })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Failed to refresh transactions', response.status);
  return data;
}

export async function getDuplicateResults(
  query: DuplicateQuery,
  offset = 0
): Promise<DuplicateResults> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/tx/duplicates', {
      headers,
      params: { query: { ...query, offset, limit: 25 } }
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Failed to find potential duplicates', response.status);
  return data;
}
