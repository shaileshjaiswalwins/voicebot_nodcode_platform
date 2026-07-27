import React from 'react';
import { render, screen } from '@testing-library/react';
import { FlowBuilderView } from './FlowBuilderView';
import type { Bot, Flow } from '../api';

// @xyflow/react relies on ResizeObserver, which jsdom doesn't implement.
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(globalThis as any).ResizeObserver = ResizeObserverMock;

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

function makeFlow(overrides: Partial<Flow> = {}): Flow {
  return { nodes: [], edges: [], ...overrides };
}

describe('FlowBuilderView empty canvas hint', () => {
  it('shows a hint to add a node when the flow has no nodes yet', () => {
    render(
      <FlowBuilderView
        selectedBot={makeBot()}
        flow={makeFlow()}
        onChange={vi.fn()}
        onSave={vi.fn()}
      />
    );
    expect(screen.getByText('This flow is empty')).toBeInTheDocument();
  });

  it('hides the empty-canvas hint once the flow has at least one node', () => {
    render(
      <FlowBuilderView
        selectedBot={makeBot()}
        flow={makeFlow({ nodes: [{ id: 'n1', type: 'message', position: { x: 0, y: 0 }, data: { text: 'hi' } }] })}
        onChange={vi.fn()}
        onSave={vi.fn()}
      />
    );
    expect(screen.queryByText('This flow is empty')).not.toBeInTheDocument();
  });

  it('prompts to select an agent when none is selected', () => {
    render(<FlowBuilderView selectedBot={undefined} flow={makeFlow()} onChange={vi.fn()} onSave={vi.fn()} />);
    expect(screen.getByText(/Select an agent first/)).toBeInTheDocument();
  });
});
