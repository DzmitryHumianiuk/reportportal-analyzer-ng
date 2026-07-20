// Thin fetch wrapper. Relative URLs resolve against <base href> injected by the
// backend, so the same bundle works at "/" (dev) and "/inspector/" (ingress).

async function get(path) {
  const res = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* ignore */ }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}
const qs = (o) => Object.entries(o)
  .filter(([, v]) => v != null && v !== '')
  .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');

export const api = {
  projects: () => get('api/projects'),
  rp: (project) => get(`api/rp?${qs({ project })}`),
  launches: (project) => get(`api/launches?${qs({ project })}`),
  items: (project, launch) => get(`api/items?${qs({ project, launch })}`),
  journey: (project, itemId) => get(`api/item/${project}/${itemId}/journey`),
  templates: (project, q) => get(`api/templates?${qs({ project, q })}`),
  modes3d: (project) => get(`api/modes3d?${qs({ project })}`),
  groups: (project, launch) => get(`api/groups?${qs({ project, launch })}`),
  timeline: (project) => get(`api/timeline?${qs({ project })}`),
  summary: (project) => get(`api/summary?${qs({ project })}`),
  signatures: (project, q, conflicts, offset) =>
    get(`api/signatures?${qs({ project, q, conflicts: conflicts ? 1 : '', offset })}`),
  signatureHash: (project, errorHash) =>
    get(`api/signature-hash?${qs({ project, error_hash: errorHash })}`),
  analyzerHealth: () => get('api/analyzer-health'),
  llmSummary: (project) => get(`api/llm/summary?${qs({ project })}`),
  llmEvents: (project, role, outcome, limit) =>
    get(`api/llm/events?${qs({ project, role, outcome, limit })}`),
  llmCache: (project, role, limit) => get(`api/llm/cache?${qs({ project, role, limit })}`),
};
