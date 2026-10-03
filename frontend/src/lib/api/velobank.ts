import { apiRequest } from './auth';
import { normalizeApiError } from './errors';
import type { components } from './schema';

export type VeloBankPreview = components['schemas']['VeloBankPreviewResponse'];
export type AccountOption = components['schemas']['VeloBankAccountOption'];
export type AccountMappings = Record<string, string>;

export async function uploadPdf(file: File): Promise<VeloBankPreview> {
  const form = new FormData();
  form.append('file', file);
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/tools/velobank/upload', {
      body: form as unknown as never,
      bodySerializer: () => form,
      headers
    })
  );
  if (!response.ok || !data)
    throw normalizeApiError(error, 'Cannot read this PDF', response.status);
  return data;
}

export async function getPreview(fileId: string): Promise<VeloBankPreview> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/tools/velobank/files/{file_id}', {
      params: { path: { file_id: fileId } },
      headers
    })
  );
  if (!response.ok || !data) throw normalizeApiError(error, 'Preview unavailable', response.status);
  return data;
}

export async function configurePreview(
  fileId: string,
  accounts: AccountMappings
): Promise<VeloBankPreview> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/tools/velobank/files/{file_id}/preview', {
      params: { path: { file_id: fileId } },
      body: { accounts },
      headers
    })
  );
  if (!response.ok || !data)
    throw normalizeApiError(error, 'Cannot update account mapping', response.status);
  return data;
}

export async function getMappings(): Promise<AccountMappings> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/tools/velobank/mappings', { headers })
  );
  if (!response.ok || !data)
    throw normalizeApiError(error, 'Cannot load saved mappings', response.status);
  return data.accounts ?? {};
}

export async function saveMappings(accounts: AccountMappings): Promise<AccountMappings> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.PUT('/api/tools/velobank/mappings', { body: { accounts }, headers })
  );
  if (!response.ok || !data)
    throw normalizeApiError(error, 'Cannot save mappings', response.status);
  return data.accounts ?? {};
}

export async function getAccounts(): Promise<AccountOption[]> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.GET('/api/tools/velobank/accounts', { headers })
  );
  if (!response.ok || !data)
    throw normalizeApiError(error, 'Cannot load Firefly accounts', response.status);
  return data;
}

export async function downloadCsv(
  fileId: string,
  accounts: AccountMappings,
  chunkSize?: number
): Promise<{ blob: Blob; filename: string }> {
  const { data, error, response } = await apiRequest((api, headers) =>
    api.POST('/api/tools/velobank/files/{file_id}/export-csv', {
      params: { path: { file_id: fileId }, query: { chunk_size: chunkSize } },
      body: { accounts },
      headers,
      parseAs: 'blob'
    })
  );
  if (!response.ok || !data)
    throw normalizeApiError(error, 'Cannot export this preview', response.status);
  const filename =
    response.headers.get('content-disposition')?.match(/filename="([^"]+)"/)?.[1] ?? 'velobank.csv';
  return { blob: data, filename };
}

export async function discardPreview(fileId: string): Promise<void> {
  const { error, response } = await apiRequest((api, headers) =>
    api.DELETE('/api/tools/velobank/files/{file_id}', {
      params: { path: { file_id: fileId } },
      headers
    })
  );
  if (!response.ok && response.status !== 404)
    throw normalizeApiError(error, 'Cannot discard preview', response.status);
}
