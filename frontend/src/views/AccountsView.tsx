import React, { useCallback, useEffect, useState } from 'react';
import { Trash2, Users } from 'lucide-react';
import type { AccountEntry } from '../api';
import { api } from '../api';
import { StatusPill } from '../components/StatusPill';
import { EmptyState } from '../components/EmptyState';
import { SkeletonTableBody } from '../components/SkeletonTableBody';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { IconButton } from '../components/ui/icon-button';
import { friendlyApiError } from '../utils/errors';

export function AccountsView({ currentUserEmail }: { currentUserEmail?: string }) {
  const [accounts, setAccounts] = useState<AccountEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busyEmail, setBusyEmail] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AccountEntry | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setAccounts(await api.listUsers());
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function changeRole(account: AccountEntry, role: string) {
    setBusyEmail(account.email);
    setError('');
    try {
      const updated = await api.updateUserRole(account.email, role);
      setAccounts((prev) => prev.map((a) => (a.email === updated.email ? { ...a, role: updated.role } : a)));
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setBusyEmail(null);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setBusyEmail(pendingDelete.email);
    setError('');
    try {
      await api.deleteUser(pendingDelete.email);
      setAccounts((prev) => prev.filter((a) => a.email !== pendingDelete.email));
      setPendingDelete(null);
    } catch (err) {
      setError(friendlyApiError(err));
    } finally {
      setBusyEmail(null);
    }
  }

  return (
    <section className="content-grid">
      <div className="table-panel">
        <div className="panel-header">
          <div>
            <h2>Accounts</h2>
            <p>Everyone who can sign in — local signups and Justdial SSO. Admins see and manage every bot; regular users only see their own.</p>
          </div>
        </div>

        {error && <p className="notice error" role="alert">{error}</p>}

        <div className="table-scroll"><table>
          <thead>
            <tr><th>Email</th><th>Source</th><th>Role</th><th /></tr>
          </thead>
          <tbody>
            {loading && !accounts.length ? (
              <SkeletonTableBody cols={4} rows={5} />
            ) : accounts.length === 0 ? (
              <tr><td colSpan={4}>
                <EmptyState icon={<Users size={32} />} heading="No accounts yet" description="Accounts appear here once someone signs up or logs in via SSO." />
              </td></tr>
            ) : accounts.map((account) => {
              const isSelf = account.email === currentUserEmail;
              const busy = busyEmail === account.email;
              return (
                <tr key={account.email}>
                  <td>{account.email}{isSelf && <small style={{ color: 'var(--muted)' }}> (you)</small>}</td>
                  <td><StatusPill value={account.is_sso ? 'sso' : 'local'} /></td>
                  <td>
                    <select
                      value={account.role}
                      disabled={busy || isSelf}
                      onChange={(e) => changeRole(account, e.target.value)}
                      style={{ fontSize: 'var(--font-size-md)' }}
                      title={isSelf ? "You can't change your own role" : undefined}
                    >
                      <option value="user">user</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td>
                    <IconButton
                      aria-label={`Delete ${account.email}`}
                      disabled={busy || isSelf}
                      title={isSelf ? "You can't delete the account you're logged in as" : 'Delete account'}
                      onClick={() => setPendingDelete(account)}
                    >
                      <Trash2 size={14} />
                    </IconButton>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table></div>
      </div>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this account?"
          description={`"${pendingDelete.email}" will no longer be able to sign in. Bots they created are not deleted.`}
          confirmLabel="Delete"
          busyLabel="Deleting…"
          busy={busyEmail === pendingDelete.email}
          tone="danger"
          onCancel={() => setPendingDelete(null)}
          onConfirm={confirmDelete}
        />
      )}
    </section>
  );
}
