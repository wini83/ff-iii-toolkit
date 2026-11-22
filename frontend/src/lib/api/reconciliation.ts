import { apiRequest } from './auth';
import { normalizeApiError } from './errors';
import type { components } from './schema';

export type ReconciliationPreview = components['schemas']['ReconciliationPreview'];
export type ReconciliationApplyResult = components['schemas']['ReconciliationApplyResult'];
export type Evidence = {
  file: File;
  accountId: string;
  from: string;
  to: string;
  tolerance: number;
};

function form(evidence: Evidence) {
  const body = new FormData();
  body.set('file', evidence.file);
  body.set('account_id', evidence.accountId);
  body.set('date_from', evidence.from);
  body.set('date_to', evidence.to);
  body.set('tolerance_days', String(evidence.tolerance));
  return body;
}

export async function compareStatement(evidence: Evidence): Promise<ReconciliationPreview> {
  const body = form(evidence);
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/reconciliation/preview', {
      headers,
      body: body as never,
      bodySerializer: (value) => value as unknown as FormData
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Statement comparison failed', response.status);
  return data;
}

export async function reconcileMatches(
  evidence: Evidence,
  revision: string,
  ids: number[]
): Promise<ReconciliationApplyResult> {
  const body = form(evidence);
  body.set('revision', revision);
  body.set('selected_ids', JSON.stringify(ids));
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/reconciliation/apply', {
      headers,
      body: body as never,
      bodySerializer: (value) => value as unknown as FormData
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(
      error,
      'Could not verify reconciliation outcome; compare again',
      response.status
    );
  return data;
}
