import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { RowActions } from './RowActions';

type Item = { id: string; name: string };

const item: Item = { id: '1', name: 'Alpha' };

// RowActions now renders through the shared "⋮" RowActionsMenu (matching BotsView) rather than
// always-visible titled buttons — every test opens that menu first, then targets the menu item.
function openMenu() {
  fireEvent.click(screen.getByRole('button', { name: /row actions/i }));
}

describe('RowActions', () => {
  it('renders Edit and Delete menu items', () => {
    render(
      <RowActions
        item={item}
        onEdit={() => {}}
        onDelete={() => {}}
        deleteTitle="Delete item?"
        deleteDescription={(i) => `"${i.name}" will be removed.`}
      />
    );
    openMenu();
    expect(screen.getByRole('menuitem', { name: /^edit$/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /delete item/i })).toBeInTheDocument();
  });

  it('clicking Delete opens confirm dialog', () => {
    render(
      <RowActions
        item={item}
        onDelete={() => {}}
        deleteTitle="Delete item?"
        deleteDescription={(i) => `"${i.name}" will be removed.`}
      />
    );
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: /delete item/i }));
    expect(screen.getByText('"Alpha" will be removed.')).toBeInTheDocument();
  });

  it('confirming calls onDelete', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(
      <RowActions
        item={item}
        onDelete={onDelete}
        deleteTitle="Delete item?"
        deleteDescription={(i) => `"${i.name}" will be removed.`}
      />
    );
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: /delete item/i }));
    fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(item));
  });

  it('cancel does not call onDelete', () => {
    const onDelete = vi.fn();
    render(
      <RowActions
        item={item}
        onDelete={onDelete}
        deleteTitle="Delete item?"
        deleteDescription={(i) => `"${i.name}" will be removed.`}
      />
    );
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: /delete item/i }));
    fireEvent.click(screen.getByText('Cancel'));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.queryByText('"Alpha" will be removed.')).not.toBeInTheDocument();
  });

  it('busy state disables both dialog buttons during an in-flight delete', async () => {
    let resolveDelete: () => void = () => {};
    const onDelete = vi.fn(() => new Promise<void>((resolve) => { resolveDelete = resolve; }));
    render(
      <RowActions
        item={item}
        onDelete={onDelete}
        deleteTitle="Delete item?"
        deleteDescription={(i) => `"${i.name}" will be removed.`}
      />
    );
    openMenu();
    fireEvent.click(screen.getByRole('menuitem', { name: /delete item/i }));
    fireEvent.click(screen.getByText('Delete'));
    await waitFor(() => {
      expect(screen.getByText('Cancel')).toBeDisabled();
    });
    resolveDelete();
  });
});
