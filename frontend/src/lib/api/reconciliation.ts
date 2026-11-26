import { apiRequest } from './auth';
import { normalizeApiError } from './errors';
import type { components } from './schema';

export type ReconciliationPreview = components['schemas']['ReconciliationPreview'];
export type ReconciliationApplyResult = components['schemas']['ReconciliationApplyResult'];
export type ReconciliationGroup = components['schemas']['ReconciliationGroup'];
export type StatementPeriod = components['schemas']['StatementPeriod'];
export type Evidence = {
  file: File;
  accountId: string;
  from: string;
  to: string;
  tolerance: number;
};

const MAX_BATCH_SIZE = 100;

export function pendingGroupIds(group: ReconciliationGroup) {
  const eligible = new Set(group.eligible_ids);
  return group.firefly
    .filter((line) => eligible.has(line.id) && line.reconciled !== true)
    .map((line) => line.id);
}

export function reconciliationBatches(ids: number[], groups: ReconciliationGroup[]) {
  const batches: { ids: number[]; groupIds: string[] }[] = [];
  let batch: (typeof batches)[number] = { ids: [], groupIds: [] };
  let size = 0;

  function flush() {
    if (!size) return;
    batches.push(batch);
    batch = { ids: [], groupIds: [] };
    size = 0;
  }

  const grouped = new Set<number>();
  for (const group of groups) {
    if (size + group.eligible_ids.length > MAX_BATCH_SIZE) flush();
    for (const id of group.eligible_ids) grouped.add(id);
    batch.groupIds.push(group.id);
    size += group.eligible_ids.length;
  }
  for (const id of ids) {
    if (grouped.has(id)) continue;
    if (size >= MAX_BATCH_SIZE) flush();
    batch.ids.push(id);
    size += 1;
  }
  flush();
  return batches;
}

function form(evidence: Evidence) {
  const body = new FormData();
  body.set('file', evidence.file);
  body.set('account_id', evidence.accountId);
  body.set('date_from', evidence.from);
  body.set('date_to', evidence.to);
  body.set('tolerance_days', String(evidence.tolerance));
  return body;
}

export async function inspectStatementPeriod(file: File): Promise<StatementPeriod> {
  const body = new FormData();
  body.set('file', file);
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/reconciliation/statement-period', {
      headers,
      body: body as never,
      bodySerializer: (value) => value as unknown as FormData
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Could not read the statement period', response.status);
  return data;
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
  ids: number[],
  groupIds: string[],
  statementComplete: boolean
): Promise<ReconciliationApplyResult> {
  const body = form(evidence);
  body.set('revision', revision);
  body.set('selected_ids', JSON.stringify(ids));
  if (groupIds.length) body.set('selected_group_ids', JSON.stringify(groupIds));
  if (statementComplete) body.set('statement_complete', 'true');
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
