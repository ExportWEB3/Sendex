import { Header, Modal, StatusBadge, SmtpAccountsSkeleton, PageSearch, SelectionBar } from '../components';
import { Plus, Zap, Trash2, Pencil, Mail, MailX, Search, CheckCircle, Cloud, Server, Eye, EyeOff, ListChecks, Loader2, AlertTriangle } from 'lucide-react';
import { useSmtpAccountsPage } from '../features/smtp/useSmtpAccountsPage';
import type {
  SMTPAccount,
  SMTPProviderType,
} from '../../typefiles';

export function SmtpAccounts() {
  const page = useSmtpAccountsPage();
  const { accounts, visibleAccounts, resendConfig, readyAccounts } = page.data;
  const { loading, initialLoad, lastUpdated } = page.status;
  const {
    createOpen: showModal,
    editingAccount,
    createProvider,
    editProvider,
    createAuthType,
    editAuthType,
    showPassword,
    showOauthSecret,
    showImapPassword,
  } = page.editor;
  const {
    testingReady,
    testingAccountIds,
    progress: readyTestProgress,
    failures: readyTestFailures,
  } = page.testing;
  const {
    enabled: selectMode,
    selectedIds,
    isDeleting: bulkDeleting,
  } = page.selection;
  const loadData = page.actions.refresh;

  const handleTest = page.actions.test;
  const handleDetectImap = page.actions.detectImap;
  const handleTestImap = page.actions.testImap;

  const handleDelete = page.actions.remove;
  const toggleSelectMode = page.selection.toggleMode;
  const toggleSelected = page.selection.toggleOne;
  const handleSelectAll = page.selection.toggleAll;
  const handleBulkDelete = page.actions.removeSelected;

  const getSmtpStatus = (account: SMTPAccount): 'active' | 'inactive' | 'ready' => {
    if (!account.last_used_at && account.is_active) return 'ready';
    return account.is_active ? 'active' : 'inactive';
  };

  const handleTestAllReady = page.actions.testAllReady;

  const getProviderLabel = (type: SMTPProviderType | string | null | undefined) => {
    switch (type) {
      case 'brevo':
      case 'ses_api': return 'Resend';
      default: return 'SMTP';
    }
  };

  const getProviderColor = (type: SMTPProviderType | string | null | undefined) => {
    switch (type) {
      case 'brevo': return 'bg-orange-100 text-orange-700';
      case 'ses_api': return 'bg-orange-100 text-orange-700';
      default: return 'bg-gray-100 text-gray-600';
    }
  };

  const getProviderIcon = (type: SMTPProviderType | string | null | undefined) => {
    switch (type) {
      case 'brevo':
      case 'ses_api':
        return <Cloud size={12} />;
      default:
        return <Server size={12} />;
    }
  };

  const handleCreate = page.actions.create;
  const handleEdit = page.editor.openEdit;
  const handleUpdate = page.actions.update;

  const renderProviderSelect = (value: SMTPProviderType, onChange: (v: SMTPProviderType) => void) => (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">Provider Type</label>
      <div className="grid grid-cols-2 gap-2">
        {([
          { value: 'smtp' as const, label: 'SMTP', icon: <Server size={16} />, desc: 'Standard SMTP server' },
          { value: 'brevo' as const, label: 'Resend', icon: <Cloud size={16} />, desc: 'API sending (any-prefix, fast)' },
        ]).map(opt => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`p-3 rounded-lg border-2 text-left transition-all ${
              (value === opt.value || (opt.value === 'brevo' && value === 'ses_api'))
                ? opt.value === 'brevo'
                  ? 'border-orange-500 bg-orange-50'
                  : 'border-indigo-500 bg-indigo-50'
                : 'border-gray-200 bg-white hover:border-gray-300'
            }`}
          >
            <div className="flex items-center gap-2 font-medium text-sm">
              {opt.icon} {opt.label}
            </div>
            <p className="text-xs text-gray-500 mt-0.5">{opt.desc}</p>
          </button>
        ))}
      </div>
    </div>
  );

  const renderSmtpFields = (defaults?: SMTPAccount | null, isEdit?: boolean) => {
    const authType = isEdit ? editAuthType : createAuthType;
    const setAuthType = isEdit ? page.editor.setEditAuthType : page.editor.setCreateAuthType;
    return (
    <>
      {/* Auth Type Toggle */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Authentication</label>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setAuthType('password')}
            className={`p-2 rounded-lg border-2 text-left transition-all text-sm ${
              authType === 'password' ? 'border-indigo-500 bg-indigo-50' : 'border-gray-200 bg-white hover:border-gray-300'
            }`}
          >
            <div className="font-medium">Password</div>
            <p className="text-xs text-gray-500">Standard SMTP login</p>
          </button>
          <button
            type="button"
            onClick={() => setAuthType('oauth2')}
            className={`p-2 rounded-lg border-2 text-left transition-all text-sm ${
              authType === 'oauth2' ? 'border-blue-500 bg-blue-50' : 'border-gray-200 bg-white hover:border-gray-300'
            }`}
          >
            <div className="font-medium">OAuth2</div>
            <p className="text-xs text-gray-500">Microsoft 365 / Outlook</p>
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Host</label>
          <input type="text" name="host" required defaultValue={authType === 'oauth2' && !defaults?.host ? 'smtp.office365.com' : (defaults?.host || '')} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" placeholder={authType === 'oauth2' ? 'smtp.office365.com' : 'smtp.gmail.com'} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Port</label>
          <input type="number" name="port" required defaultValue={defaults?.port || 587} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
        </div>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Username {authType === 'oauth2' ? '(email address)' : ''}</label>
        <input type="text" name="username" required defaultValue={defaults?.username || ''} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" placeholder={authType === 'oauth2' ? 'user@yourdomain.com' : ''} />
      </div>

      {authType === 'password' ? (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">{isEdit ? 'Password (leave blank to keep current)' : 'Password'}</label>
          <div className="relative">
            <input type={showPassword ? 'text' : 'password'} name="password" required={!isEdit} placeholder={isEdit ? '••••••••' : ''} className="w-full px-3 py-2 pr-10 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
            <button type="button" onClick={page.editor.togglePassword} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 p-1" tabIndex={-1}>
              {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="rounded-lg p-3 text-sm bg-blue-50 border border-blue-200">
            <p className="text-blue-700 font-medium text-xs mb-1">Microsoft 365 OAuth2</p>
            <p className="text-blue-600 text-xs">
              Register an app in Microsoft Entra ID → API permissions → add SMTP.Send → generate a client secret.
              <br />Tenant ID is your Entra directory ID (found in Entra admin center → Overview).
            </p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Tenant ID</label>
            <input type="text" name="oauth2_tenant_id" required defaultValue={defaults?.oauth2_tenant_id || ''} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Client ID</label>
            <input type="text" name="oauth2_client_id" required defaultValue={defaults?.oauth2_client_id || ''} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">{isEdit ? 'Client Secret (leave blank to keep)' : 'Client Secret'}</label>
            <div className="relative">
              <input type={showOauthSecret ? 'text' : 'password'} name="oauth2_client_secret" required={!isEdit} defaultValue={''} placeholder={isEdit ? '••••••••' : ''} className="w-full px-3 py-2 pr-10 border rounded-lg focus:ring-2 focus:ring-blue-500" />
              <button type="button" onClick={page.editor.toggleOauthSecret} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 p-1" tabIndex={-1}>
                {showOauthSecret ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>
        </>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Encryption</label>
        <select name="encryption" defaultValue={defaults?.encryption || 'tls'} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500">
          <option value="tls">TLS (STARTTLS)</option>
          <option value="ssl">SSL</option>
          <option value="none">None</option>
        </select>
      </div>
      <div className="border-t border-dashed border-gray-200 pt-4 mt-2">
        <p className="text-sm font-medium text-gray-500 mb-3 flex items-center gap-2">
          IMAP Settings
          <span className="text-xs font-normal bg-gray-100 text-gray-400 px-2 py-0.5 rounded-full">Optional</span>
        </p>
        <div className="grid grid-cols-2 gap-4 mb-3">
          <div>
            <label className="block text-sm font-medium text-gray-500 mb-1">IMAP Host</label>
            <input type="text" name="imap_host" defaultValue={defaults?.imap_host || ''} className="w-full px-3 py-2 border border-dashed border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 bg-gray-50" placeholder="imap.gmail.com" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-500 mb-1">IMAP Port</label>
            <input type="number" name="imap_port" defaultValue={defaults?.imap_port || 993} className="w-full px-3 py-2 border border-dashed border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 bg-gray-50" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-500 mb-1">IMAP Username</label>
            <input type="text" name="imap_username" defaultValue={defaults?.imap_username || ''} className="w-full px-3 py-2 border border-dashed border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 bg-gray-50" placeholder="imap user" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-500 mb-1">IMAP Password</label>
            <div className="relative">
              <input type={showImapPassword ? 'text' : 'password'} name="imap_password" defaultValue="" className="w-full px-3 py-2 pr-10 border border-dashed border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 bg-gray-50" placeholder={defaults ? 'Leave blank to keep existing password' : 'IMAP password'} />
              <button type="button" onClick={page.editor.toggleImapPassword} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 p-1" tabIndex={-1}>
                {showImapPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
    );
  };

  const renderResendFields = () => (
    <>
      <div className={`rounded-lg p-3 text-sm ${resendConfig?.configured ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'}`}>
        <div className={`flex items-center gap-2 font-medium mb-1 ${resendConfig?.configured ? 'text-green-700' : 'text-red-700'}`}>
          <Cloud size={14} /> Resend {resendConfig?.configured ? '— Connected' : '— Not Configured'}
        </div>
        {resendConfig?.configured ? (
          <p className="text-green-600 text-xs">
            💡 You can use any address on your verified domain — no real mailbox needed.
            {resendConfig?.has_shared_imap && (
              <>
                <br />📬 Replies routed to: <strong>{resendConfig.shared_imap_user}</strong> via {resendConfig.shared_imap_host}
              </>
            )}
          </p>
        ) : (
          <p className="text-red-600 text-xs">
            Resend is not configured. Contact an administrator.
          </p>
        )}
      </div>
    </>
  );

  return (
    <div className="flex min-h-screen min-w-0 flex-1 flex-col overflow-x-hidden">
      <Header title="SMTP Accounts" onRefresh={loadData} lastUpdated={lastUpdated} />
      {initialLoad ? <SmtpAccountsSkeleton /> : (
      <div className={`min-w-0 flex-1 p-4 transition-opacity duration-200 sm:p-6 ${loading ? 'opacity-60' : ''}`}>
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
          <p className="text-gray-600">Manage SMTP and Resend accounts for sending emails</p>
          <div className="flex flex-wrap items-center gap-2 w-full sm:w-auto">
            <button
              type="button"
              onClick={() => void handleTestAllReady()}
              disabled={testingReady || loading || bulkDeleting || readyAccounts.length === 0}
              title={readyAccounts.length > 0 ? `Test ${readyAccounts.length} account${readyAccounts.length === 1 ? '' : 's'} currently showing Ready` : 'No accounts are currently showing Ready'}
              className="flex flex-1 items-center justify-center gap-2 border border-cyan-500 bg-cyan-950/30 px-3 py-1.5 text-sm font-medium text-cyan-300 transition-colors hover:bg-cyan-900/50 disabled:cursor-not-allowed disabled:border-gray-700 disabled:bg-gray-900/40 disabled:text-gray-500 sm:flex-none"
            >
              {testingReady ? <Loader2 size={16} className="animate-spin" /> : <Zap size={16} />}
              {testingReady && readyTestProgress
                ? `Testing ${readyTestProgress.completed}/${readyTestProgress.total}`
                : `Test All Ready (${readyAccounts.length})`}
            </button>
            <button
              onClick={toggleSelectMode}
              disabled={testingReady}
              className={`px-3 py-1.5 text-sm rounded-lg transition-colors flex items-center gap-2 justify-center ${
                selectMode ? 'bg-gray-200 text-gray-800' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              } disabled:cursor-not-allowed disabled:opacity-50`}
            >
              <ListChecks size={16} /> {selectMode ? 'Cancel Select' : 'Select'}
            </button>
            <button
              onClick={() => page.editor.openCreate('smtp')}
              className="bg-indigo-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2 flex-1 sm:flex-none justify-center"
            >
              <Plus size={16} /> Add Account
            </button>
          </div>
        </div>

        {readyTestProgress && (
          <div className={`mb-4 border px-3 py-2.5 ${
            readyTestProgress.failed > 0
              ? 'border-amber-700 bg-amber-950/25'
              : readyTestProgress.completed === readyTestProgress.total
                ? 'border-emerald-700 bg-emerald-950/25'
                : 'border-cyan-800 bg-cyan-950/25'
          }`}>
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="flex min-w-0 items-center gap-2 font-medium text-gray-200">
                {testingReady
                  ? <Loader2 size={15} className="shrink-0 animate-spin text-cyan-400" />
                  : readyTestProgress.failed > 0
                    ? <AlertTriangle size={15} className="shrink-0 text-amber-400" />
                    : <CheckCircle size={15} className="shrink-0 text-emerald-400" />}
                Ready account connection test
              </span>
              <span className="shrink-0 text-xs text-gray-400">
                {readyTestProgress.completed}/{readyTestProgress.total} · {readyTestProgress.succeeded} passed · {readyTestProgress.failed} failed
              </span>
            </div>
            <div className="mt-2 h-1.5 w-full bg-gray-800">
              <div
                className={`h-full transition-all duration-200 ${readyTestProgress.failed > 0 ? 'bg-amber-500' : 'bg-cyan-400'}`}
                style={{ width: `${readyTestProgress.total ? (readyTestProgress.completed / readyTestProgress.total) * 100 : 0}%` }}
              />
            </div>
            {!testingReady && readyTestFailures.length > 0 && (
              <div className="mt-2 space-y-1 border-t border-amber-800/60 pt-2 text-xs text-amber-200">
                {readyTestFailures.map(failure => (
                  <p key={failure.id} className="truncate" title={failure.message}>
                    <span className="font-semibold">{failure.name}:</span> {failure.message}
                  </p>
                ))}
              </div>
            )}
          </div>
        )}

        <PageSearch
          value={page.search.value}
          onChange={page.search.setValue}
          placeholder="Search sending accounts..."
          label="Search sending accounts"
          resultCount={page.search.resultCount}
          totalCount={page.search.totalCount}
        />

        {selectMode && (
          <SelectionBar
            count={selectedIds.size}
            total={visibleAccounts.length}
            onSelectAll={handleSelectAll}
            onDelete={handleBulkDelete}
            onCancel={toggleSelectMode}
            deleting={bulkDeleting}
            itemLabel="account"
          />
        )}

        <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {accounts.length === 0 ? (
            <div className="col-span-full bg-white rounded-xl shadow p-8 text-center text-gray-500">
              No accounts yet. Add an SMTP server or Resend account to get started!
            </div>
          ) : visibleAccounts.length === 0 ? (
            <div className="col-span-full border border-gray-200 bg-white p-8 text-center text-gray-500">
              No sending accounts match your search.
            </div>
          ) : (
            visibleAccounts.map(account => {
              const status = getSmtpStatus(account);
              const isLegacyApi = account.provider_type === 'ses_api';
              const isResendRecord = account.provider_type === 'brevo';
              const isApi = isLegacyApi || isResendRecord;
              return (
              <div key={account.id} className={`min-w-0 overflow-hidden bg-white rounded-xl shadow p-2 ${(isLegacyApi || isResendRecord) ? 'ring-1 ring-orange-200' : ''}`}>
                <div className="mb-4 grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
                  <div className="flex min-w-0 items-start gap-2">
                    {selectMode && (
                      <input
                        type="checkbox"
                        checked={selectedIds.has(account.id)}
                        onChange={() => toggleSelected(account.id)}
                        className="mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <h3
                        className="line-clamp-2 min-w-0 wrap-anywhere text-sm font-semibold leading-snug text-gray-900"
                        title={account.name}
                      >
                        {account.name}
                      </h3>
                      <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                        <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-xs font-medium ${getProviderColor(account.provider_type)}`}>
                        {getProviderIcon(account.provider_type)}
                        {getProviderLabel(account.provider_type)}
                        </span>
                        {isApi ? (
                          <span className="min-w-0 truncate text-xs text-gray-500" title="Resend API">
                            Resend API
                          </span>
                        ) : (
                          <span className="min-w-0 truncate text-xs text-gray-500" title={`${account.host}:${account.port}`}>
                            {account.host}:{account.port}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="shrink-0">
                    <StatusBadge
                      status={status === 'ready' ? 'pending' : status}
                      text={status === 'ready' ? 'Ready' : status === 'active' ? 'Active' : 'Inactive'}
                    />
                  </div>
                </div>
                <div className="mb-4 min-w-0 space-y-2 text-sm">
                  <div className="min-w-0">
                    <span className="text-gray-500">From: </span>
                    <span className="wrap-anywhere text-gray-900">{account.from_email}</span>
                  </div>
                  {account.from_name && (
                    <div className="min-w-0">
                      <span className="text-gray-500">Name: </span>
                      <span className="wrap-anywhere text-gray-900">{account.from_name}</span>
                    </div>
                  )}
                  {isApi ? (
                    <div>
                      <span className="text-gray-500">Provider: </span>
                      <span className="text-gray-900">Resend API</span>
                    </div>
                  ) : (
                    <>
                      <div>
                        <span className="text-gray-500">Encryption: </span>
                        <span className="text-gray-900 uppercase">{account.encryption}</span>
                        {account.auth_type === 'oauth2' && (
                          <span className="ml-2 inline-flex items-center text-xs px-1.5 py-0.5 rounded-full font-medium bg-blue-100 text-blue-700">OAuth2</span>
                        )}
                      </div>
                      <div>
                        <span className="text-gray-500">Limits: </span>
                        <span className="text-gray-900">{account.hourly_limit}/hr, {account.daily_limit}/day</span>
                      </div>
                      <div>
                        <span className="text-gray-500">IMAP: </span>
                        {account.has_imap ? (
                          <span className="inline-flex items-center gap-1 text-green-700">
                            <Mail size={12} /> {account.imap_host}:{account.imap_port}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-gray-400">
                            <MailX size={12} /> Not configured
                          </span>
                        )}
                      </div>
                    </>
                  )}
                </div>
                <div className="flex gap-2 flex-wrap">
                  <button
                    onClick={() => handleEdit(account)}
                    className="flex-1 py-1.5 text-sm bg-gray-50 text-gray-600 rounded-lg flex items-center justify-center gap-1.5 hover:bg-gray-100 transition-colors"
                  >
                    <Pencil size={14} /> Edit
                  </button>
                  <button
                    onClick={() => void handleTest(account.id)}
                    disabled={testingReady || testingAccountIds.has(account.id)}
                    className={`flex-1 py-1.5 text-sm rounded-lg flex items-center justify-center gap-1.5 transition-colors ${
                      (isLegacyApi || isResendRecord) ? 'bg-orange-50 text-orange-600 hover:bg-orange-100' : 'bg-indigo-50 text-indigo-600 hover:bg-indigo-100'
                    } disabled:cursor-not-allowed disabled:opacity-50`}
                  >
                    {testingAccountIds.has(account.id)
                      ? <Loader2 size={14} className="animate-spin" />
                      : <Zap size={14} />}
                    {testingAccountIds.has(account.id) ? 'Testing' : 'Test'}
                  </button>
                  {!isApi && (
                    account.has_imap ? (
                      <button
                        onClick={() => handleTestImap(account.id)}
                        className="py-1.5 px-2 text-sm bg-green-50 text-green-600 rounded-lg flex items-center justify-center gap-1.5 hover:bg-green-100 transition-colors"
                        title="Test IMAP Connection"
                      >
                        <CheckCircle size={14} /> IMAP
                      </button>
                    ) : (
                      <button
                        onClick={() => handleDetectImap(account.id)}
                        className="py-1.5 px-2 text-sm bg-blue-50 text-blue-600 rounded-lg flex items-center justify-center gap-1.5 hover:bg-blue-100 transition-colors"
                        title="Auto-detect IMAP Settings"
                      >
                        <Search size={14} /> IMAP
                      </button>
                    )
                  )}
                  <button
                    onClick={() => handleDelete(account.id)}
                    className="px-3 py-1.5 bg-red-50 text-red-600 rounded-lg hover:bg-red-100 transition-colors"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              );
            })
          )}
        </div>
      </div>
      )}

      {/* Create Account Modal */}
      <Modal isOpen={showModal} onClose={page.editor.closeCreate} title="Add Account">
        <form onSubmit={handleCreate} className="space-y-4">
          <input type="hidden" name="provider_type" value={createProvider} />
          {renderProviderSelect(createProvider, page.editor.setCreateProvider)}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Account Name</label>
            <input type="text" name="name" required className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" placeholder={createProvider === 'brevo' ? 'Resend - Sales Outreach' : 'My Gmail'} />
          </div>
          {createProvider === 'brevo' && resendConfig?.resend_verified_domain ? (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">From Email</label>
              <div className="flex items-center">
                <input
                  type="text"
                  name="resend_email_prefix"
                  required
                  className="flex-1 px-3 py-2 border border-r-0 rounded-l-lg focus:ring-2 focus:ring-orange-500 focus:border-orange-500"
                  placeholder="hello"
                  autoComplete="off"
                />
                <span className="px-3 py-2 bg-orange-50 border border-orange-200 rounded-r-lg text-sm text-orange-700 font-medium whitespace-nowrap">
                  @{resendConfig.resend_verified_domain}
                </span>
              </div>
              <p className="text-xs text-gray-400 mt-1">Any prefix works — no mailbox needed</p>
              <p className="text-xs text-amber-600 mt-0.5">💡 Use a professional prefix (support, hello, team, alex) for higher deliverability</p>
            </div>
          ) : createProvider === 'brevo' ? (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">From Email</label>
              <input type="email" name="from_email" required className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500" placeholder="hello@yourdomain.com" />
              <p className="text-xs text-gray-400 mt-1">Any address on your verified domain — no mailbox needed</p>
            </div>
          ) : (
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">From Email</label>
              <input type="email" name="from_email" required className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" placeholder="you@example.com" />
            </div>
          )}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">From Name <span className="text-red-500">*</span></label>
            <input type="text" name="from_name" required className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" placeholder="John Doe" />
          </div>
          {createProvider === 'brevo' ? renderResendFields() : renderSmtpFields()}
          <div className="flex justify-end gap-3 pt-4">
            <button type="button" onClick={page.editor.closeCreate} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 transition-colors">Cancel</button>
            <button type="submit" className={`px-3 py-1.5 text-sm text-white rounded-lg transition-colors ${
              createProvider === 'brevo' ? 'bg-orange-600 hover:bg-orange-700' : 'bg-indigo-600 hover:bg-indigo-700'
            }`}>Create</button>
          </div>
        </form>
      </Modal>

      {/* Edit Account Modal */}
      <Modal isOpen={!!editingAccount} onClose={page.editor.closeEdit} title="Edit Account">
        {editingAccount && (
          <form onSubmit={handleUpdate} className="space-y-4">
            <input type="hidden" name="provider_type" value={editProvider} />
            {renderProviderSelect(editProvider, page.editor.setEditProvider)}
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Account Name</label>
              <input type="text" name="name" required defaultValue={editingAccount.name} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">From Email</label>
              <input type="email" name="from_email" required defaultValue={editingAccount.from_email} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">From Name <span className="text-red-500">*</span></label>
              <input type="text" name="from_name" required defaultValue={editingAccount.from_name || ''} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
            </div>
            {(editProvider === 'ses_api' || editProvider === 'brevo') ? renderResendFields() : renderSmtpFields(editingAccount, true)}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Hourly Limit</label>
                <input type="number" name="hourly_limit" defaultValue={editingAccount.hourly_limit} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Daily Limit</label>
                <input type="number" name="daily_limit" defaultValue={editingAccount.daily_limit} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Status</label>
              <select name="is_active" defaultValue={editingAccount.is_active ? 'true' : 'false'} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500">
                <option value="true">Active</option>
                <option value="false">Inactive</option>
              </select>
            </div>
            <div className="flex justify-end gap-3 pt-4">
              <button type="button" onClick={page.editor.closeEdit} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 transition-colors">Cancel</button>
              <button type="submit" className={`px-3 py-1.5 text-sm text-white rounded-lg transition-colors ${
                (editProvider === 'ses_api' || editProvider === 'brevo') ? 'bg-orange-600 hover:bg-orange-700' : 'bg-indigo-600 hover:bg-indigo-700'
              }`}>Save Changes</button>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
}
