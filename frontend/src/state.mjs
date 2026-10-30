const pages = new Set(['overview', 'duplicates', 'classification', 'review', 'export']);
const reviewId = /^[a-f0-9]{64}$/;
const exportId = /^[a-f0-9]{32}$/;

export function parseRoute(hash) {
  const value = hash.startsWith('#') ? hash.slice(1) : hash;
  const segments = value.split('/').filter(Boolean);
  if (segments.length === 0 || !pages.has(segments[0]) || segments.length > 2) {
    return {page: 'overview', detail: null};
  }
  const page = segments[0];
  if (segments.length === 1) return {page, detail: null};
  const detail = segments[1];
  if ((page === 'review' && reviewId.test(detail)) ||
      (page === 'export' && exportId.test(detail))) return {page, detail};
  return {page, detail: null};
}

export function routeHash(page, detail = null) {
  if (!pages.has(page)) return '#/overview';
  if (detail && ((page === 'review' && reviewId.test(detail)) ||
                 (page === 'export' && exportId.test(detail)))) return `#/${page}/${detail}`;
  return `#/${page}`;
}

export function createLatestGate() {
  let active = null;
  return {
    next() {
      active?.abort();
      const current = new AbortController();
      active = current;
      return {signal: current.signal, isCurrent: () => active === current && !current.signal.aborted};
    },
    cancel() {active?.abort(); active = null;}
  };
}

export async function fetchJson(url, init) {
  const response = await fetch(url, init);
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) {
    throw new Error(`Unexpected server response for ${url} (HTTP ${response.status}; ${type || 'no content type'}).`);
  }
  let data;
  try {data = await response.json();}
  catch {throw new Error(`Invalid JSON from ${url} (HTTP ${response.status}).`);}
  if (!response.ok) {
    const message = typeof data?.detail === 'string' ? data.detail : `HTTP ${response.status}`;
    throw new Error(`${message} (${url})`);
  }
  return data;
}

export function makeScope(filters, total, digest) {
  if (!Number.isSafeInteger(total) || total < 1 || !/^[a-f0-9]{64}$/.test(digest)) {
    throw new Error('Invalid selection snapshot');
  }
  return Object.freeze({filters: Object.freeze({...filters}), total, digest, key: JSON.stringify(filters)});
}

export function sameScope(scope, filters) {
  return scope !== null && scope.key === JSON.stringify(filters);
}
