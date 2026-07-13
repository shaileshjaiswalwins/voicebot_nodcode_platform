import React from 'react';
import { render, screen } from '@testing-library/react';
import { CampaignsView } from './CampaignsView';

describe('CampaignsView cleanup', () => {
  it('does not show the removed static "Dispatch contract" documentation block', () => {
    render(
      <CampaignsView
        campaigns={[]}
        bots={[]}
        languages={[]}
        outcomes={[]}
        loading={false}
        workspaceMode="list"
        selectedCampaignKey=""
        onSelectCampaign={vi.fn()}
        onBackToList={vi.fn()}
        onSaveStrategy={vi.fn()}
        onAssignBot={vi.fn()}
        onSetStatus={vi.fn()}
      />
    );
    expect(screen.queryByText('Dispatch contract')).not.toBeInTheDocument();
    expect(screen.queryByText(/Lead event\/API/)).not.toBeInTheDocument();
    expect(screen.getByText(/Click "Edit Strategy"/)).toBeInTheDocument();
  });
});

describe('CampaignsView bot-assignment loading feedback', () => {
  const campaign = { _id: 'c1', campaign_key: 'ck1', name: 'Summer Sale', bot_id: '', status: 'active' } as any;

  it('shows a spinner and disables the select while an assignment is in flight', () => {
    render(
      <CampaignsView
        campaigns={[campaign]}
        bots={[]}
        languages={[]}
        outcomes={[]}
        loading={false}
        workspaceMode="list"
        selectedCampaignKey=""
        onSelectCampaign={vi.fn()}
        onBackToList={vi.fn()}
        onSaveStrategy={vi.fn()}
        onAssignBot={vi.fn()}
        assignBotState={{ ck1: 'running' }}
        onSetStatus={vi.fn()}
      />
    );
    expect(screen.getByRole('status', { name: 'Assigning' })).toBeInTheDocument();
    expect(screen.getByRole('combobox')).toBeDisabled();
  });

  it('shows no spinner and an enabled select when idle', () => {
    render(
      <CampaignsView
        campaigns={[campaign]}
        bots={[]}
        languages={[]}
        outcomes={[]}
        loading={false}
        workspaceMode="list"
        selectedCampaignKey=""
        onSelectCampaign={vi.fn()}
        onBackToList={vi.fn()}
        onSaveStrategy={vi.fn()}
        onAssignBot={vi.fn()}
        assignBotState={{ ck1: 'idle' }}
        onSetStatus={vi.fn()}
      />
    );
    expect(screen.queryByRole('status', { name: 'Assigning' })).not.toBeInTheDocument();
    expect(screen.getByRole('combobox')).not.toBeDisabled();
  });
});
