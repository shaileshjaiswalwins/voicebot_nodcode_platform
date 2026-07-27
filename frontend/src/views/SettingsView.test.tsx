import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SettingsView } from './SettingsView';
import type { PlatformSettings } from '../api';

function makePlatformSettings(overrides: Partial<PlatformSettings> = {}): PlatformSettings {
  return {
    active_environment: 'dev',
    dev: { mis_api_base: 'http://old-dev:8000' },
    prod: { mis_api_base: 'http://old-prod:8000' },
    default_inactivity_phrase: '',
    default_close_markers: [],
    ...overrides,
  };
}

describe('SettingsView admin settings persistence', () => {
  it('saves admin settings through onUpdatePlatformSettings, not localStorage', async () => {
    const user = userEvent.setup();
    const setItemSpy = vi.spyOn(window.localStorage, 'setItem');
    const onUpdatePlatformSettings = vi.fn().mockResolvedValue(undefined);

    render(
      <SettingsView
        runtimeSettings={null}
        onUpdateRuntime={vi.fn()}
        platformSettings={makePlatformSettings()}
        onUpdatePlatformSettings={onUpdatePlatformSettings}
      />
    );

    const misInput = screen.getByLabelText(/MIS API base URL/);
    await user.clear(misInput);
    await user.type(misInput, 'http://new-dev:9000');

    await user.click(screen.getByRole('button', { name: /Save admin settings/ }));

    await waitFor(() => expect(onUpdatePlatformSettings).toHaveBeenCalledTimes(1));
    const payload = onUpdatePlatformSettings.mock.calls[0][0] as PlatformSettings;
    expect(payload.dev.mis_api_base).toBe('http://new-dev:9000');

    const adminSettingsWrites = setItemSpy.mock.calls.filter(([key]) => key === 'adminPlatformSettings');
    expect(adminSettingsWrites).toHaveLength(0);

    await waitFor(() => expect(screen.getByRole('button', { name: /Saved/ })).toBeInTheDocument());
  });

  it('shows a retry state when the save fails', async () => {
    const user = userEvent.setup();
    const onUpdatePlatformSettings = vi.fn().mockRejectedValue(new Error('network down'));

    render(
      <SettingsView
        runtimeSettings={null}
        onUpdateRuntime={vi.fn()}
        platformSettings={makePlatformSettings()}
        onUpdatePlatformSettings={onUpdatePlatformSettings}
      />
    );

    await user.click(screen.getByRole('button', { name: /Save admin settings/ }));

    await waitFor(() => expect(screen.getByRole('button', { name: /Retry save/ })).toBeInTheDocument());
  });
});

describe('SettingsView URL validation', () => {
  it('disables save and shows an error when the MIS API base URL is invalid', async () => {
    const user = userEvent.setup();
    const onUpdatePlatformSettings = vi.fn().mockResolvedValue(undefined);

    render(
      <SettingsView
        runtimeSettings={null}
        onUpdateRuntime={vi.fn()}
        platformSettings={makePlatformSettings()}
        onUpdatePlatformSettings={onUpdatePlatformSettings}
      />
    );

    const misInput = screen.getByLabelText(/MIS API base URL/);
    await user.clear(misInput);
    await user.type(misInput, 'not a url');

    expect(screen.getByText('Must be a valid http(s) URL.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Save admin settings/ })).toBeDisabled();
  });

  it('disables the runtime save button and shows an error when the LiveKit API URL is invalid', async () => {
    const user = userEvent.setup();
    render(
      <SettingsView
        runtimeSettings={{ livekit_api_url: '', livekit_browser_url: '', livekit_agent_name: '' }}
        onUpdateRuntime={vi.fn()}
        platformSettings={makePlatformSettings()}
        onUpdatePlatformSettings={vi.fn()}
      />
    );

    const apiUrlInput = screen.getByLabelText(/LiveKit API URL/);
    await user.type(apiUrlInput, 'garbage');

    expect(screen.getByText('Must be a valid http(s) URL.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Save runtime settings/ })).toBeDisabled();
  });

  it('allows saving runtime settings again once the URL is corrected', async () => {
    const user = userEvent.setup();
    const onUpdateRuntime = vi.fn();
    render(
      <SettingsView
        runtimeSettings={{ livekit_api_url: '', livekit_browser_url: '', livekit_agent_name: '' }}
        onUpdateRuntime={onUpdateRuntime}
        platformSettings={makePlatformSettings()}
        onUpdatePlatformSettings={vi.fn()}
      />
    );

    const apiUrlInput = screen.getByLabelText(/LiveKit API URL/);
    await user.type(apiUrlInput, 'garbage');
    expect(screen.getByRole('button', { name: /Save runtime settings/ })).toBeDisabled();

    await user.clear(apiUrlInput);
    await user.type(apiUrlInput, 'https://livekit.example.com');
    expect(screen.getByRole('button', { name: /Save runtime settings/ })).not.toBeDisabled();

    await user.click(screen.getByRole('button', { name: /Save runtime settings/ }));
    expect(onUpdateRuntime).toHaveBeenCalledWith(expect.objectContaining({ livekit_api_url: 'https://livekit.example.com' }));
  });
});

describe('SettingsView cleanup', () => {
  it('does not show the removed speculative "Coming soon: role-based access" callout', () => {
    render(
      <SettingsView
        runtimeSettings={null}
        onUpdateRuntime={vi.fn()}
        platformSettings={makePlatformSettings()}
        onUpdatePlatformSettings={vi.fn()}
      />
    );
    expect(screen.queryByText(/Coming soon: role-based access/)).not.toBeInTheDocument();
  });
});
