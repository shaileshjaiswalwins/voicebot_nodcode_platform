import { parsePath, buildPath } from './routes';

describe('buildPath', () => {
  it('builds the plain list path for each top-level view', () => {
    expect(buildPath({ view: 'bots', botId: '' })).toBe('/agents');
    expect(buildPath({ view: 'flow' })).toBe('/flow');
    expect(buildPath({ view: 'campaigns', campaignKey: '' })).toBe('/campaigns');
    expect(buildPath({ view: 'test' })).toBe('/test');
    expect(buildPath({ view: 'transcripts', transcriptId: '' })).toBe('/transcripts');
    expect(buildPath({ view: 'analytics' })).toBe('/analytics');
    expect(buildPath({ view: 'observability' })).toBe('/observability');
    expect(buildPath({ view: 'library' })).toBe('/library');
    expect(buildPath({ view: 'settings' })).toBe('/settings');
  });

  it('builds a detail path when a bot/campaign/transcript id is present', () => {
    expect(buildPath({ view: 'bots', botId: 'bot-1' })).toBe('/agents/bot-1');
    expect(buildPath({ view: 'campaigns', campaignKey: 'ck-1' })).toBe('/campaigns/ck-1');
    expect(buildPath({ view: 'transcripts', transcriptId: 't-1' })).toBe('/transcripts/t-1');
  });
});

describe('parsePath', () => {
  it('parses each top-level list path back into a view', () => {
    expect(parsePath('/agents')).toEqual({ view: 'bots', botId: '' });
    expect(parsePath('/flow')).toEqual({ view: 'flow' });
    expect(parsePath('/campaigns')).toEqual({ view: 'campaigns', campaignKey: '' });
    expect(parsePath('/test')).toEqual({ view: 'test' });
    expect(parsePath('/transcripts')).toEqual({ view: 'transcripts', transcriptId: '' });
    expect(parsePath('/analytics')).toEqual({ view: 'analytics' });
    expect(parsePath('/observability')).toEqual({ view: 'observability' });
    expect(parsePath('/library')).toEqual({ view: 'library' });
    expect(parsePath('/settings')).toEqual({ view: 'settings' });
  });

  it('parses detail paths with an id segment', () => {
    expect(parsePath('/agents/bot-1')).toEqual({ view: 'bots', botId: 'bot-1' });
    expect(parsePath('/campaigns/ck-1')).toEqual({ view: 'campaigns', campaignKey: 'ck-1' });
    expect(parsePath('/transcripts/t-1')).toEqual({ view: 'transcripts', transcriptId: 't-1' });
  });

  it('defaults to the agents list for unknown or root paths', () => {
    expect(parsePath('/')).toEqual({ view: 'bots', botId: '' });
    expect(parsePath('/nonsense')).toEqual({ view: 'bots', botId: '' });
  });

  it('round-trips buildPath -> parsePath for every route shape', () => {
    const routes: ReturnType<typeof parsePath>[] = [
      { view: 'bots', botId: '' },
      { view: 'bots', botId: 'bot-9' },
      { view: 'flow' },
      { view: 'campaigns', campaignKey: '' },
      { view: 'campaigns', campaignKey: 'ck-9' },
      { view: 'transcripts', transcriptId: '' },
      { view: 'transcripts', transcriptId: 't-9' },
      { view: 'settings' },
    ];
    for (const route of routes) {
      expect(parsePath(buildPath(route))).toEqual(route);
    }
  });
});
