// Synthetic server-side reads for browser tests. Client-side mutations remain mocked
// by each test; no real backend or credentials are used.
import { createServer } from 'node:http';

/** @type {Record<string, unknown>} */
const responses = {
  '/api/me': {
    id: 'demo-user',
    username: 'demo.operator',
    is_active: true,
    is_superuser: true
  },
  '/api/system/health': { status: 'ok', database: 'ok' },
  '/api/system/version': { version: 'test' }
};
createServer((request, response) => {
  const payload = request.method === 'GET' ? responses[request.url ?? ''] : undefined;
  response.writeHead(payload ? 200 : 404, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(payload ?? { detail: 'Not mocked' }));
}).listen(4175, '127.0.0.1');
