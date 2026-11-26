import { apiRequest } from './auth';
import { normalizeApiError } from './errors';
import type { components } from './schema';

export type StatementAccount = components['schemas']['StatementAccountOption'];
export type CounterpartyPreview = components['schemas']['CounterpartyPreview'];
export type CounterpartyCandidate = components['schemas']['CounterpartyCandidate'];
export type CounterpartyJob = components['schemas']['CounterpartyJobResponse'];
export type AccountChoice = number | 'create';
export type StatementEvidence = { file: File; accountId: string; singleAccountConfirmed: boolean };

function statementForm({ file, accountId, singleAccountConfirmed }: StatementEvidence) {
  const body = new FormData();
  body.set('file', file);
  body.set('account_id', accountId);
  body.set('single_account_confirmed', String(singleAccountConfirmed));
  return body;
}

export async function listStatementAccounts(): Promise<StatementAccount[]> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/statement-counterparties/accounts', { headers })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Could not load Firefly accounts', response.status);
  return data;
}

export async function previewCounterparties(
  evidence: StatementEvidence
): Promise<CounterpartyPreview> {
  const body = statementForm(evidence);
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/statement-counterparties/preview', {
      headers,
      body: body as never,
      bodySerializer: (value) => value as unknown as FormData
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Could not prepare the preview', response.status);
  return data;
}

export async function startCounterpartyJob(
  evidence: StatementEvidence,
  revision: string,
  accountChoices: Record<string, AccountChoice> = {}
): Promise<CounterpartyJob> {
  const body = statementForm(evidence);
  body.set('revision', revision);
  body.set('account_choices', JSON.stringify(accountChoices));
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/statement-counterparties/apply', {
      headers,
      body: body as never,
      bodySerializer: (value) => value as unknown as FormData
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Could not start the job', response.status);
  return data;
}

export async function getCounterpartyJob(jobId: string): Promise<CounterpartyJob> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/statement-counterparties/jobs/{job_id}', {
      headers,
      params: { path: { job_id: jobId } }
    })
  );
  if (!response.ok || error || !data)
    throw normalizeApiError(error, 'Could not load job status', response.status);
  return data;
}
