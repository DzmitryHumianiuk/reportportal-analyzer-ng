// Thin typed fetch client. Every URL is RELATIVE so it resolves against the
// <base href> the backend injects, and the same bundle works at "/" (dev) and
// "/inspector/" (ingress). Never hardcode a leading slash here.

import type {
  AnalyzerHealthResponse,
  GroupsResponse,
  ItemsResponse,
  JourneyResponse,
  LaunchesResponse,
  LlmCacheResponse,
  LlmEventsResponse,
  LlmSummaryResponse,
  Modes3dResponse,
  ProjectsResponse,
  RpResponse,
  RubricResponse,
  SignatureHashResponse,
  SignaturesResponse,
  SummaryResponse,
  TemplatesResponse,
  TimelineResponse,
} from './types';

type QueryValue = string | number | boolean | null | undefined;

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body.detail || detail;
    } catch {
      /* body was not JSON — keep the status text */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return (await res.json()) as T;
}

/** Serialize a query object, dropping null and empty-string values. */
function qs(params: Record<string, QueryValue>): string {
  return Object.entries(params)
    .filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join('&');
}

export const api = {
  projects: (): Promise<ProjectsResponse> => get<ProjectsResponse>('api/projects'),

  rp: (project: number): Promise<RpResponse> => get<RpResponse>(`api/rp?${qs({ project })}`),

  launches: (project: number): Promise<LaunchesResponse> =>
    get<LaunchesResponse>(`api/launches?${qs({ project })}`),

  items: (project: number, launch: number): Promise<ItemsResponse> =>
    get<ItemsResponse>(`api/items?${qs({ project, launch })}`),

  journey: (project: number, itemId: number): Promise<JourneyResponse> =>
    get<JourneyResponse>(`api/item/${project}/${itemId}/journey`),

  rubric: (): Promise<RubricResponse> => get<RubricResponse>('api/rubric'),

  templates: (project: number, q?: string | null): Promise<TemplatesResponse> =>
    get<TemplatesResponse>(`api/templates?${qs({ project, q })}`),

  modes3d: (project: number): Promise<Modes3dResponse> =>
    get<Modes3dResponse>(`api/modes3d?${qs({ project })}`),

  groups: (project: number, launch?: number | null): Promise<GroupsResponse> =>
    get<GroupsResponse>(`api/groups?${qs({ project, launch })}`),

  timeline: (project: number): Promise<TimelineResponse> =>
    get<TimelineResponse>(`api/timeline?${qs({ project })}`),

  summary: (project: number): Promise<SummaryResponse> =>
    get<SummaryResponse>(`api/summary?${qs({ project })}`),

  signatures: (
    project: number,
    q?: string | null,
    conflicts?: boolean,
    offset?: number,
  ): Promise<SignaturesResponse> =>
    get<SignaturesResponse>(
      `api/signatures?${qs({ project, q, conflicts: conflicts ? 1 : '', offset })}`,
    ),

  signatureHash: (project: number, errorHash: string): Promise<SignatureHashResponse> =>
    get<SignatureHashResponse>(`api/signature-hash?${qs({ project, error_hash: errorHash })}`),

  analyzerHealth: (): Promise<AnalyzerHealthResponse> =>
    get<AnalyzerHealthResponse>('api/analyzer-health'),

  llmSummary: (project: number): Promise<LlmSummaryResponse> =>
    get<LlmSummaryResponse>(`api/llm/summary?${qs({ project })}`),

  llmEvents: (
    project: number,
    role: string,
    outcome: string,
    limit?: number,
  ): Promise<LlmEventsResponse> =>
    get<LlmEventsResponse>(`api/llm/events?${qs({ project, role, outcome, limit })}`),

  llmCache: (project: number, role?: string, limit?: number): Promise<LlmCacheResponse> =>
    get<LlmCacheResponse>(`api/llm/cache?${qs({ project, role, limit })}`),
};
