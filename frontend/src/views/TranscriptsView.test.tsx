import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TranscriptsView } from './TranscriptsView';
import type { Bot, Transcript } from '../api';

function makeBot(overrides: Partial<Bot> = {}): Bot {
  return {
    _id: 'bot-1',
    name: 'Sales Bot',
    description: '',
    assistant_id: 'asst-1',
    status: 'active',
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

function makeTranscript(overrides: Partial<Transcript> = {}): Transcript {
  return {
    _id: 't1',
    bot_id: 'bot-1',
    created_at: new Date().toISOString(),
    transcript: [{ role: 'assistant', text: 'Hello, how can I help?', created_at: new Date().toISOString() }],
    ...overrides,
  };
}

const baseProps = {
  localRecordingByRoom: {},
  callEvents: [],
  campaigns: [],
  loading: false,
  searchText: '',
  onSearchText: vi.fn(),
  filters: { status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '', end_date: '' },
  onFiltersChange: vi.fn(),
  onSelect: vi.fn(),
};

describe('TranscriptsView chat turn labels', () => {
  it('shows the real bot name for assistant turns instead of a hardcoded name', () => {
    const bot = makeBot({ name: 'Sales Bot' });
    const transcript = makeTranscript();
    render(
      <TranscriptsView
        {...baseProps}
        transcripts={[transcript]}
        selectedTranscript={transcript}
        bots={[bot]}
      />
    );
    expect(screen.getByText('Sales Bot')).toBeInTheDocument();
    expect(screen.queryByText('Tanya / Assistant')).not.toBeInTheDocument();
  });

  it('falls back to "Assistant" when the bot cannot be resolved', () => {
    const transcript = makeTranscript({ bot_id: 'unknown-bot' });
    render(
      <TranscriptsView
        {...baseProps}
        transcripts={[transcript]}
        selectedTranscript={transcript}
        bots={[]}
      />
    );
    expect(screen.getByText('Assistant')).toBeInTheDocument();
  });
});

describe('TranscriptsView date range validation', () => {
  async function renderWithFiltersOpen(filters: typeof baseProps.filters) {
    const user = userEvent.setup();
    render(
      <TranscriptsView
        {...baseProps}
        transcripts={[]}
        bots={[]}
        filters={filters}
      />
    );
    await user.click(screen.getByRole('button', { name: /filters/i }));
  }

  it('shows no error and an enabled export button when the date range is valid or empty', async () => {
    await renderWithFiltersOpen({ status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '2026-01-01', end_date: '2026-01-31' });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /export/i })).not.toBeDisabled();
  });

  it('shows a validation error and disables export when end date is before start date', async () => {
    await renderWithFiltersOpen({ status: '', outcome: '', campaign_id: '', bot_id: '', start_date: '2026-02-10', end_date: '2026-02-01' });
    expect(screen.getByRole('alert')).toHaveTextContent(/can't be before/i);
    expect(screen.getByRole('button', { name: /export/i })).toBeDisabled();
  });
});
