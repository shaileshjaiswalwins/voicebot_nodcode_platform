import type { View } from '../types';

export type RouteState =
  | { view: 'bots'; botId: string }
  | { view: 'flow' }
  | { view: 'campaigns'; campaignKey: string }
  | { view: 'phone_numbers' }
  | { view: 'number_mapping' }
  | { view: 'test' }
  | { view: 'transcripts'; transcriptId: string }
  | { view: 'analytics' }
  | { view: 'observability' }
  | { view: 'library' }
  | { view: 'settings' }
  | { view: 'audit_log' }
  | { view: 'admin' };

const VIEW_TO_SEGMENT: Record<View, string> = {
  bots: 'agents',
  builder: 'agents',
  flow: 'flow',
  campaigns: 'campaigns',
  phone_numbers: 'phone-numbers',
  number_mapping: 'number-mapping',
  test: 'test',
  transcripts: 'transcripts',
  analytics: 'analytics',
  observability: 'observability',
  library: 'library',
  settings: 'settings',
  audit_log: 'audit-log',
  admin: 'admin',
};

const SEGMENT_TO_VIEW: Record<string, View> = {
  agents: 'bots',
  flow: 'flow',
  campaigns: 'campaigns',
  'phone-numbers': 'phone_numbers',
  'number-mapping': 'number_mapping',
  test: 'test',
  transcripts: 'transcripts',
  analytics: 'analytics',
  observability: 'observability',
  library: 'library',
  settings: 'settings',
  'audit-log': 'audit_log',
  admin: 'admin',
};

export function buildPath(route: RouteState): string {
  const segment = VIEW_TO_SEGMENT[route.view];
  if (route.view === 'bots') return route.botId ? `/${segment}/${route.botId}` : `/${segment}`;
  if (route.view === 'campaigns') return route.campaignKey ? `/${segment}/${route.campaignKey}` : `/${segment}`;
  if (route.view === 'transcripts') return route.transcriptId ? `/${segment}/${route.transcriptId}` : `/${segment}`;
  return `/${segment}`;
}

export function parsePath(pathname: string): RouteState {
  const [, segment, id] = pathname.split('/');
  const view = SEGMENT_TO_VIEW[segment];
  if (!view) return { view: 'bots', botId: '' };
  if (view === 'bots') return { view: 'bots', botId: id || '' };
  if (view === 'campaigns') return { view: 'campaigns', campaignKey: id || '' };
  if (view === 'transcripts') return { view: 'transcripts', transcriptId: id || '' };
  return { view } as RouteState;
}
