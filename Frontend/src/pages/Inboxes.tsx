import { useEffect, useState } from 'react';
import { Header, Modal, StatusBadge, InboxesSkeleton, SelectionBar } from '../components';
import { Plus, Play, Pause, Trash2, Mail, MailX, AlertTriangle, RefreshCw, Shield, MailCheck, Search, Reply, Cloud, Timer, Send, ListChecks } from 'lucide-react';
import { getDisplayWarmupDay } from '../warmupDay';
import { useInboxesPage } from '../features/inboxes/useInboxesPage';
import type { Inbox } from '../../typefiles';

/** Sending Mode Badge */
function ModeBadge({ mode }: { mode?: string }) {
  const cfg: Record<string, { bg: string; text: string; label: string; dot: string }> = {
    active:     { bg: 'bg-emerald-100', text: 'text-emerald-700', label: 'Active',     dot: 'bg-emerald-500' },
    hyper:      { bg: 'bg-purple-100',  text: 'text-purple-700',  label: 'Hyper',      dot: 'bg-purple-500' },
    distracted: { bg: 'bg-amber-100',   text: 'text-amber-700',   label: 'Distracted', dot: 'bg-amber-500' },
    offline:    { bg: 'bg-gray-100',    text: 'text-gray-600',    label: 'Offline',    dot: 'bg-gray-400' },
  };
  const m = cfg[mode || 'active'] || cfg.active;
  return (
    <div className="flex items-center gap-1.5">
      <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full font-medium ${m.bg} ${m.text}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
        {m.label}
      </span>
    </div>
  );
}

/** Live countdown to midnight UTC (when warmup day advances) */
function WarmupCountdown({ state }: { state: string }) {
  const [timeLeft, setTimeLeft] = useState('');
  useEffect(() => {
    if (state !== 'warming_up') return;
    const tick = () => {
      const now = new Date();
      const midnight = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
      const diff = midnight.getTime() - now.getTime();
      const h = Math.floor(diff / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      setTimeLeft(`${h}h ${m}m ${s}s`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [state]);
  if (state !== 'warming_up') return null;
  return (
    <span className="inline-flex items-center gap-1 text-[11px] text-indigo-500 font-mono">
      <Timer size={10} className="animate-pulse" /> {timeLeft}
    </span>
  );
}

export function Inboxes() {
  const {
    data: {
      inboxes,
      smtpAccounts,
      fleetQuota,
      selectedSmtp,
      selectedSmtpIsApi: isSelectedApi,
    },
    status: { loading, initialLoad, lastUpdated },
    mx: { statuses: mxStatuses },
    fix: {
      inboxId: fixModalInbox,
      state: fixState,
      message: fixMessage,
      replyToEmail,
      replyToPassword,
      open: handleFixClick,
      close: closeFixModal,
      setEmail: setReplyToEmail,
      setPassword: setReplyToPassword,
      submit: handleSetupReplyTo,
    },
    diagnosis: {
      inboxId: diagnoseInbox,
      loading: diagnoseLoading,
      result: diagnoseResult,
      showAppPasswordSteps: showAppPassSteps,
      run: handleDiagnose,
      close: closeDiagnosis,
      toggleAppPasswordSteps,
    },
    replyEditor: {
      inboxId: editingReplyTo,
      email: inlineReplyTo,
      saving: savingReplyTo,
      open: openReplyEditor,
      close: closeReplyEditor,
      setEmail: setInlineReplyTo,
      save: handleSaveReplyTo,
    },
    creation: {
      open: showModal,
      creating,
      showImapOverride,
      selectedSmtpId,
      openModal: openCreateModal,
      closeModal: closeCreateModal,
      setSmtpId: setSelectedSmtpId,
      toggleImapOverride,
    },
    testSend: {
      inbox: testInbox,
      recipient: testRecipient,
      sending: sendingTest,
      open: openTestSend,
      close: closeTestSend,
      setRecipient: setTestRecipient,
      submit: handleTestSend,
    },
    selection: {
      enabled: selectMode,
      selectedIds,
      isDeleting: bulkDeleting,
      toggleMode: toggleSelectMode,
      toggleOne: toggleSelected,
      toggleAll: handleSelectAll,
    },
    actions: {
      refresh: loadData,
      pauseWarmup: handlePauseWarmup,
      resumeWarmup: handleResumeWarmup,
      remove: handleDelete,
      removeSelected: handleBulkDelete,
      create: handleCreate,
    },
  } = useInboxesPage();

  // Helper: check if an inbox is backed by a Resend API account.
  const isApiBacked = (inbox: Inbox) => {
    const smtp = smtpAccounts.find(a => a.id === inbox.smtp_account_id);
    return smtp?.provider_type === 'ses_api' || smtp?.provider_type === 'brevo';
  };
  return (
    <div className="flex-1 flex flex-col min-h-screen">
      <Header title="Inboxes" onRefresh={loadData} lastUpdated={lastUpdated} />
      
      {initialLoad ? <InboxesSkeleton /> : (
      <div className={`p-4 sm:p-6 flex-1 transition-opacity duration-200 ${loading ? 'opacity-60' : ''}`}>
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
          <p className="text-gray-600">Manage your email inboxes and warm-up progress</p>
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <button
              onClick={toggleSelectMode}
              className={`px-3 py-1.5 text-sm rounded-lg transition-colors flex items-center gap-2 justify-center ${
                selectMode ? 'bg-gray-200 text-gray-800' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              <ListChecks size={16} /> {selectMode ? 'Cancel Select' : 'Select'}
            </button>
            <button
              onClick={openCreateModal}
              className="bg-indigo-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2 flex-1 sm:flex-none justify-center"
            >
              <Plus size={16} /> Add Inbox
            </button>
          </div>
        </div>

        {selectMode && (
          <SelectionBar
            count={selectedIds.size}
            total={inboxes.length}
            onSelectAll={handleSelectAll}
            onDelete={handleBulkDelete}
            onCancel={toggleSelectMode}
            deleting={bulkDeleting}
            itemLabel="inbox"
          />
        )}

        {/* Mobile Cards */}
        <div className="lg:hidden space-y-4">
          {inboxes.map(inbox => {
            const displayWarmupDay = getDisplayWarmupDay(inbox);
            return (
            <div key={inbox.id} className={`bg-white rounded-xl shadow p-4 ${isApiBacked(inbox) ? 'ring-1 ring-orange-200' : ''}`}>
              <div className="flex justify-between items-start mb-3">
                <div className="flex items-start gap-2">
                  {selectMode && (
                    <input
                      type="checkbox"
                      checked={selectedIds.has(inbox.id)}
                      onChange={() => toggleSelected(inbox.id)}
                      className="mt-1 w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                    />
                  )}
                  <div>
                  <p className="font-medium text-gray-900">{inbox.email}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <p className="text-sm text-gray-500">Group {inbox.group}</p>
                    {isApiBacked(inbox) ? (
                      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700">
                        <Cloud size={12} /> Resend
                      </span>
                    ) : inbox.has_imap ? (
                      <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full ${inbox.reply_enabled ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                        <Mail size={12} /> {inbox.reply_enabled ? 'Replies On' : 'Replies Off'}
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">
                        <MailX size={12} /> No IMAP
                      </span>
                    )}
                    {mxStatuses[inbox.id]?.mismatch && (
                      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-700">
                        <AlertTriangle size={12} /> MX Mismatch
                      </span>
                    )}
                  </div>
                  </div>
                </div>
                <StatusBadge status={inbox.state} />
              </div>
              {/* Sending Mode */}
              <div className="mb-2">
                <ModeBadge mode={inbox.sending_mode} />
              </div>
              <div className="grid grid-cols-3 gap-4 text-sm mb-2">
                <div>
                  <p className="text-gray-500">Day</p>
                  <p className="font-medium">{displayWarmupDay}</p>
                </div>
                <div>
                  <p className="text-gray-500">Fleet cap</p>
                  <p className="font-medium">{fleetQuota ? `${(fleetQuota.budget / 1000).toFixed(0)}k/day` : '—'}</p>
                </div>
                <div>
                  <p className="text-gray-500">Fleet used</p>
                  <p className="font-medium">{fleetQuota ? fleetQuota.used_today : '—'}</p>
                </div>
              </div>
              {fleetQuota && (
                <div className="mb-3">
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>Fleet budget today</span>
                    <span>{fleetQuota.remaining.toLocaleString()} left</span>
                  </div>
                  <div className="w-full bg-gray-200 rounded-full h-1.5">
                    <div
                      className="bg-indigo-600 h-1.5 rounded-full transition-all"
                      style={{ width: `${Math.min(100, (fleetQuota.used_today / Math.max(1, fleetQuota.budget)) * 100)}%` }}
                    />
                  </div>
                </div>
              )}
              <div className="mb-3">
                <WarmupCountdown state={inbox.state} />
              </div>
              {/* MX Mismatch Warning */}
              {mxStatuses[inbox.id]?.mismatch && !inbox.reply_to_email && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-2.5 mb-3">
                  <div className="flex items-start gap-2">
                    <AlertTriangle size={14} className="text-red-500 mt-0.5 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-red-700">Replies won't be detected — mail goes to a different server</p>
                    </div>
                  </div>
                  <button
                    onClick={() => handleFixClick(inbox.id)}
                    className="mt-2 w-full py-1.5 text-xs bg-red-600 text-white rounded-lg hover:bg-red-700 flex items-center justify-center gap-1.5"
                  >
                    <Shield size={12} /> Fix Reply Detection
                  </button>
                </div>
              )}
              {inbox.reply_to_email && editingReplyTo !== inbox.id && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-2 mb-3 flex items-center justify-between">
                  <p className="text-xs text-blue-700 flex items-center gap-1"><MailCheck size={12} /> Replies → {inbox.reply_to_email}</p>
                  <button onClick={() => openReplyEditor(inbox.id, inbox.reply_to_email || '')} className="text-xs text-blue-500 hover:text-blue-700 underline">Edit</button>
                </div>
              )}
              {!inbox.reply_to_email && editingReplyTo !== inbox.id && (
                <button
                  onClick={() => openReplyEditor(inbox.id)}
                  className="w-full mb-3 py-1.5 text-xs bg-blue-50 text-blue-600 rounded-lg hover:bg-blue-100 flex items-center justify-center gap-1.5 border border-blue-200"
                >
                  <Reply size={12} /> Set Reply-To Email
                </button>
              )}
              {editingReplyTo === inbox.id && (
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-2.5 mb-3 space-y-2">
                  <p className="text-xs font-medium text-blue-800">Reply-To Email</p>
                  <p className="text-xs text-blue-600">Replies from recipients will go to this email instead of {inbox.email}</p>
                  <input
                    type="email"
                    value={inlineReplyTo}
                    onChange={e => setInlineReplyTo(e.target.value)}
                    placeholder="e.g. yourgmail@gmail.com"
                    className="w-full px-2.5 py-1.5 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleSaveReplyTo(inbox.id)}
                      disabled={savingReplyTo}
                      className="flex-1 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
                    >
                      {savingReplyTo ? 'Saving...' : 'Save'}
                    </button>
                    <button
                      onClick={closeReplyEditor}
                      className="px-3 py-1.5 text-xs text-gray-600 hover:text-gray-800 bg-white rounded-lg border"
                    >
                      Cancel
                    </button>
                    {inbox.reply_to_email && (
                      <button
                        onClick={() => void handleSaveReplyTo(inbox.id, '')}
                        className="px-3 py-1.5 text-xs text-red-600 hover:text-red-800 bg-white rounded-lg border border-red-200"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  <p className="text-xs text-blue-500">For auto-replies, also set up IMAP via "Fix Reply Detection" above. Just the Reply-To header doesn't need IMAP.</p>
                </div>
              )}
              {/* Diagnose button - always available for inboxes with IMAP */}
              {inbox.imap_host && (
                <button
                  onClick={() => handleDiagnose(inbox.id)}
                  className="w-full mb-3 py-1.5 text-xs bg-gray-100 text-gray-600 rounded-lg hover:bg-gray-200 flex items-center justify-center gap-1.5"
                >
                  <Search size={12} /> Diagnose Reply Detection
                </button>
              )}
              <button
                onClick={() => openTestSend(inbox)}
                disabled={!inbox.is_active || inbox.state === 'disabled'}
                className="w-full mb-3 py-1.5 text-xs bg-indigo-50 text-indigo-600 rounded-lg hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 flex items-center justify-center gap-1.5 border border-indigo-200"
              >
                <Send size={12} /> Send Test Email
              </button>
              <div className="flex gap-2">
                {inbox.state === 'warming_up' ? (
                  <button onClick={() => handlePauseWarmup(inbox.id)} className="flex-1 py-1.5 text-sm bg-amber-50 text-amber-700 rounded-lg flex items-center justify-center gap-1.5 hover:bg-amber-100 transition-colors">
                    <Pause size={14} /> Pause
                  </button>
                ) : inbox.state === 'paused' ? (
                  <button onClick={() => handleResumeWarmup(inbox.id)} className="flex-1 py-1.5 text-sm bg-emerald-50 text-emerald-700 rounded-lg flex items-center justify-center gap-1.5 hover:bg-emerald-100 transition-colors">
                    <Play size={14} /> Resume
                  </button>
                ) : inbox.state === 'warmed_up' ? (
                  <span className="flex-1 py-1.5 text-sm text-center text-green-600 font-medium">Warmed Up</span>
                ) : null}
                <button onClick={() => handleDelete(inbox.id)} className="px-3 py-1.5 bg-red-50 text-red-600 rounded-lg hover:bg-red-100 transition-colors">
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
            );
          })}
        </div>

        {/* Desktop Table */}
        <div className="hidden lg:block bg-white rounded-xl shadow overflow-hidden">
          <table className="w-full">
            <thead className="bg-gray-50">
              <tr>
                {selectMode && <th className="px-4 py-3 w-8"></th>}
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Email</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Mode</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Replies / Reply-To</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Warm-up</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Fleet left</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {inboxes.length === 0 ? (
                <tr>
                  <td colSpan={selectMode ? 8 : 7} className="px-4 py-8 text-center text-gray-500">
                    No inboxes yet. Add one to get started!
                  </td>
                </tr>
              ) : (
                inboxes.map(inbox => {
                  const displayWarmupDay = getDisplayWarmupDay(inbox);
                  return (
                  <tr key={inbox.id} className={isApiBacked(inbox) ? 'bg-[#0b1117]' : ''}>
                    {selectMode && (
                      <td className="px-4 py-4">
                        <input
                          type="checkbox"
                          checked={selectedIds.has(inbox.id)}
                          onChange={() => toggleSelected(inbox.id)}
                          className="w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                        />
                      </td>
                    )}
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-gray-900">{inbox.email}</span>
                        {isApiBacked(inbox) && (
                          <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full bg-orange-100 text-orange-700">
                            <Cloud size={10} /> Resend
                          </span>
                        )}
                      </div>
                      <div className="text-sm text-gray-500">Group {inbox.group}</div>
                    </td>
                    <td className="px-4 py-4"><StatusBadge status={inbox.state} /></td>
                    <td className="px-4 py-4">
                      <ModeBadge mode={inbox.sending_mode} />
                    </td>
                    <td className="px-4 py-4">
                      <div className="space-y-1.5">
                        {isApiBacked(inbox) ? (
                          <span className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-orange-100 text-orange-700">
                            <Cloud size={12} /> Resend
                          </span>
                        ) : inbox.has_imap ? (
                          <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full ${inbox.reply_enabled ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'}`}>
                            <Mail size={12} /> {inbox.reply_enabled ? 'Enabled' : 'Disabled'}
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-gray-100 text-gray-500">
                            <MailX size={12} /> No IMAP
                          </span>
                        )}
                        {!isApiBacked(inbox) && mxStatuses[inbox.id]?.mismatch && !inbox.reply_to_email && (
                          <div>
                            <button
                              onClick={() => handleFixClick(inbox.id)}
                              className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-red-600 text-white hover:bg-red-700"
                            >
                              <AlertTriangle size={10} /> Fix Replies
                            </button>
                          </div>
                        )}
                        {/* Reply-To display/edit */}
                        {inbox.reply_to_email && editingReplyTo !== inbox.id && (
                          <div className="flex items-center gap-1.5">
                            <span className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-blue-100 text-blue-700">
                              <MailCheck size={10} /> → {inbox.reply_to_email}
                            </span>
                            <button onClick={() => openReplyEditor(inbox.id, inbox.reply_to_email || '')} className="text-xs text-blue-500 hover:text-blue-700 underline">Edit</button>
                          </div>
                        )}
                        {!inbox.reply_to_email && editingReplyTo !== inbox.id && (
                          <button
                            onClick={() => openReplyEditor(inbox.id)}
                            className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full bg-blue-50 text-blue-600 hover:bg-blue-100 border border-blue-200"
                          >
                            <Reply size={10} /> Set Reply-To
                          </button>
                        )}
                        {editingReplyTo === inbox.id && (
                          <div className="bg-blue-50 border border-blue-200 rounded-lg p-2 space-y-1.5 max-w-xs">
                            <input
                              type="email"
                              value={inlineReplyTo}
                              onChange={e => setInlineReplyTo(e.target.value)}
                              placeholder="e.g. yourgmail@gmail.com"
                              className="w-full px-2 py-1 border rounded text-xs focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                            />
                            <div className="flex gap-1.5">
                              <button
                                onClick={() => handleSaveReplyTo(inbox.id)}
                                disabled={savingReplyTo}
                                className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                              >
                                {savingReplyTo ? 'Saving...' : 'Save'}
                              </button>
                              <button
                                onClick={closeReplyEditor}
                                className="px-2 py-1 text-xs text-gray-600 hover:text-gray-800 bg-white rounded border"
                              >
                                Cancel
                              </button>
                              {inbox.reply_to_email && (
                                <button
                                  onClick={() => void handleSaveReplyTo(inbox.id, '')}
                                  className="px-2 py-1 text-xs text-red-600 hover:text-red-800 bg-white rounded border border-red-200"
                                >
                                  Remove
                                </button>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-4 text-gray-600">
                      <div className="text-sm">Day {displayWarmupDay}</div>
                      <div className="text-xs text-gray-400">
                        {fleetQuota ? `Fleet cap: ${(fleetQuota.budget / 1000).toFixed(0)}k/day · used ${fleetQuota.used_today.toLocaleString()}` : 'Fleet cap: —'}
                      </div>
                      <WarmupCountdown state={inbox.state} />
                    </td>
                    <td className="px-4 py-4 text-gray-600">{fleetQuota ? fleetQuota.remaining.toLocaleString() : '—'}</td>
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-1.5">
                        {inbox.state === 'warming_up' ? (
                          <button onClick={() => handlePauseWarmup(inbox.id)} className="p-1.5 text-amber-600 hover:bg-amber-50 rounded-lg transition-colors" title="Pause Warmup">
                            <Pause size={16} />
                          </button>
                        ) : inbox.state === 'paused' ? (
                          <button onClick={() => handleResumeWarmup(inbox.id)} className="p-1.5 text-emerald-600 hover:bg-emerald-50 rounded-lg transition-colors" title="Resume Warmup">
                            <Play size={16} />
                          </button>
                        ) : null}
                        {inbox.imap_host && (
                          <button onClick={() => handleDiagnose(inbox.id)} className="p-1.5 text-gray-500 hover:bg-gray-100 rounded-lg transition-colors" title="Diagnose Reply Detection">
                            <Search size={16} />
                          </button>
                        )}
                        <button
                          onClick={() => openTestSend(inbox)}
                          disabled={!inbox.is_active || inbox.state === 'disabled'}
                          className="p-1.5 text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-40"
                          title="Send Test Email"
                        >
                          <Send size={16} />
                        </button>
                        <button onClick={() => handleDelete(inbox.id)} className="p-1.5 text-red-500 hover:bg-red-50 rounded-lg transition-colors" title="Delete">
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
      )}

      {/* Add Inbox Modal */}
      <Modal isOpen={showModal} onClose={closeCreateModal} title="Add Inbox">
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">SMTP Account</label>
            <select
              name="smtp_account_id"
              required
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500"
              onChange={e => setSelectedSmtpId(parseInt(e.target.value) || null)}
              value={selectedSmtpId || ''}
            >
              <option value="">Select SMTP Account</option>
              {smtpAccounts.map(account => (
                <option key={account.id} value={account.id}>
                  {(account.provider_type === 'brevo' || account.provider_type === 'ses_api') ? '☁️ ' : ''}{account.name} ({account.from_email})
                  {(account.provider_type === 'brevo' || account.provider_type === 'ses_api') ? ' — Resend' : account.has_imap ? ' ✓ IMAP' : ''}
                </option>
              ))}
            </select>
          </div>

          {/* API provider auto-fill banner */}
          {isSelectedApi && (
            <div className="bg-orange-50 border border-orange-200 rounded-lg p-3">
              <div className="flex items-start gap-2">
                <Cloud size={16} className="text-orange-600 mt-0.5 shrink-0" />
                <div className="text-xs text-orange-800">
                  <p className="font-medium mb-1">Resend Inbox</p>
                  <p>Email auto-filled from your account. Sending via API — fast, no SMTP ports needed.</p>
                </div>
              </div>
            </div>
          )}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email Address</label>
            {isSelectedApi ? (
              <>
                <input
                  type="email"
                  name="email"
                  required
                  className="w-full px-3 py-2 border rounded-lg focus:ring-2 bg-orange-50 border-orange-200 text-orange-800 focus:ring-orange-500"
                  value={selectedSmtp?.from_email || ''}
                  readOnly
                />
                <p className="text-xs mt-1 text-orange-500">Auto-filled from your account — this is the sending address</p>
              </>
            ) : (
              <input type="email" name="email" required className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500" placeholder="user@example.com" />
            )}
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Group</label>
            <select name="group" className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500">
              <option value="A">Group A</option>
              <option value="B">Group B</option>
              <option value="C">Group C</option>
            </select>
          </div>
          
          {/* Auto-detection info - only for non-API */}
          {!isSelectedApi && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
              <div className="flex items-start gap-2">
                <Shield size={16} className="text-blue-600 mt-0.5 shrink-0" />
                <div className="text-xs text-blue-800">
                  <p className="font-medium mb-1">IMAP & Reply Detection</p>
                  <p>IMAP settings will be auto-detected from your SMTP account. Just enter the email and select the SMTP account — everything else is automatic.</p>
                </div>
              </div>
            </div>
          )}
          {isSelectedApi && (
            <div className="bg-green-50 border border-green-200 rounded-lg p-3">
              <div className="flex items-start gap-2">
                <Shield size={16} className="text-green-600 mt-0.5 shrink-0" />
                <div className="text-xs text-green-800">
                  <p className="font-medium mb-1">📬 Reply Detection — Auto-configured</p>
                  <p>Replies to this inbox will be automatically routed to the shared domain mailbox and detected by the system. No manual IMAP setup needed.</p>
                </div>
              </div>
            </div>
          )}

          {/* Optional: Reply-To override - only for non-API */}
          {!isSelectedApi && (
            <div>
              <label className="block text-xs text-gray-500 mb-1">Reply-To Email <span className="text-gray-400">(optional — override where replies go)</span></label>
              <input type="email" name="reply_to_email" placeholder="replies@example.com" className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm" />
            </div>
          )}

          {/* Toggle to show manual IMAP override - only for non-API */}
          {!isSelectedApi && (
            !showImapOverride ? (
              <button type="button" onClick={toggleImapOverride} className="text-xs text-gray-400 hover:text-gray-600 underline">
                Override IMAP settings manually
              </button>
            ) : (
              <div className="border-t pt-4 mt-2">
                <p className="text-sm font-medium text-gray-700 mb-2">Manual IMAP Override</p>
                <div className="space-y-3">
                  <div className="grid grid-cols-3 gap-3">
                    <div className="col-span-2">
                      <label className="block text-xs text-gray-500 mb-1">IMAP Host</label>
                      <input type="text" name="imap_host" placeholder="imap.example.com" className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm" />
                    </div>
                    <div>
                      <label className="block text-xs text-gray-500 mb-1">IMAP Port</label>
                      <input type="number" name="imap_port" defaultValue="993" className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm" />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">IMAP Username</label>
                    <input type="text" name="imap_username" placeholder="user@example.com" className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm" />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">IMAP Password</label>
                    <input type="password" name="imap_password" placeholder="App password" className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm" />
                  </div>
                  <button type="button" onClick={toggleImapOverride} className="text-xs text-gray-400 hover:text-gray-600 underline">
                    Hide manual settings (use auto-detect)
                  </button>
                </div>
              </div>
            )
          )}

          <div className="flex justify-end gap-3 pt-4">
            <button type="button" onClick={closeCreateModal} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={creating} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 flex items-center gap-2">
              {creating ? (
                <><RefreshCw size={14} className="animate-spin" /> Detecting & Creating...</>
              ) : (
                'Create Inbox'
              )}
            </button>
          </div>
        </form>
      </Modal>

      <Modal isOpen={testInbox !== null} onClose={closeTestSend} title="Send Test Email">
        <form onSubmit={handleTestSend} className="space-y-4">
          <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3 text-sm text-indigo-800">
            <p className="font-medium">From {testInbox?.email}</p>
            <p className="mt-1 text-xs text-indigo-600">The delivery-check message is already drafted. Enter only the address that should receive it.</p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="test-recipient-email">Recipient Email</label>
            <input
              id="test-recipient-email"
              type="email"
              required
              autoFocus
              value={testRecipient}
              onChange={e => setTestRecipient(e.target.value)}
              placeholder="you@example.com"
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
            />
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={closeTestSend} disabled={sendingTest} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 disabled:opacity-50">
              Cancel
            </button>
            <button type="submit" disabled={sendingTest || !testRecipient.trim()} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50 flex items-center gap-2">
              {sendingTest ? <><RefreshCw size={14} className="animate-spin" /> Sending...</> : <><Send size={14} /> Send Test</>}
            </button>
          </div>
        </form>
      </Modal>

      {/* Smart Fix Reply Detection Modal */}
      <Modal isOpen={fixModalInbox !== null} onClose={closeFixModal} title="Fix Reply Detection">
        <div className="space-y-4">
          {/* Step 1: Trying auto-fix */}
          {fixState === 'trying' && (
            <div className="flex items-center gap-3 py-4">
              <RefreshCw size={20} className="animate-spin text-indigo-500" />
              <p className="text-sm text-gray-600">Trying to fix automatically...</p>
            </div>
          )}

          {/* Step 2: Auto-fix succeeded */}
          {fixState === 'done' && (
            <div className="space-y-3">
              <div className="bg-green-50 border border-green-200 rounded-lg p-3">
                <p className="text-sm text-green-800 flex items-center gap-2">
                  <MailCheck size={16} /> {fixMessage}
                </p>
              </div>
              <div className="flex justify-end">
                <button onClick={closeFixModal} className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700">
                  Done
                </button>
              </div>
            </div>
          )}

          {/* Step 3: Auto-fix failed → show Reply-To setup */}
          {(fixState === 'failed' || fixState === 'reply-to' || fixState === 'saving') && (
            <div className="space-y-4">
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                <p className="text-sm text-amber-800">
                  Your SMTP sends from <strong>{inboxes.find(i => i.id === fixModalInbox)?.email}</strong>, but replies go to a different mail server that we can't log into.
                </p>
              </div>

              <div className="bg-gray-50 rounded-lg p-4">
                <p className="text-sm font-medium text-gray-800 mb-1">Solution: Set a Reply-To address</p>
                <p className="text-xs text-gray-500 mb-3">
                  Enter a Gmail (or other email) you have access to. Recipients still see your original email as the sender, but when they hit Reply, it goes to this address instead — where we can detect it.
                </p>
                <div className="space-y-3">
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">Reply-To Email</label>
                    <input
                      type="email"
                      value={replyToEmail}
                      onChange={e => setReplyToEmail(e.target.value)}
                      placeholder="your.email@gmail.com"
                      className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-600 mb-1">IMAP Password / App Password</label>
                    <input
                      type="password"
                      value={replyToPassword}
                      onChange={e => setReplyToPassword(e.target.value)}
                      placeholder="Gmail: use App Password from Google Security"
                      className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 text-sm"
                    />
                    <button type="button" onClick={toggleAppPasswordSteps} className="text-xs text-indigo-600 hover:text-indigo-800 mt-1.5 underline underline-offset-2 font-medium">
                      {showAppPassSteps ? 'Hide steps' : 'How to find your App Password →'}
                    </button>
                    {showAppPassSteps && (
                      <div className="mt-2 bg-indigo-50 border border-indigo-200 rounded-lg p-3 text-xs text-indigo-900 space-y-2">
                        <p className="font-semibold">Steps to get a Gmail App Password:</p>
                        <ol className="list-decimal list-inside space-y-1 ml-1">
                          <li>Open your browser and go to <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer" className="underline font-medium text-indigo-700">myaccount.google.com/apppasswords</a></li>
                          <li>Sign in to your Google account if prompted</li>
                          <li>Enter a name like <strong>"FLEETCTRL-X"</strong> and click <strong>Create</strong></li>
                          <li>Google gives you a <strong>16-character password</strong> — paste that here</li>
                        </ol>
                        <div className="border-t border-indigo-200 pt-2 mt-2">
                          <p className="font-semibold">Don't see App Passwords?</p>
                          <ol className="list-decimal list-inside space-y-1 ml-1 mt-1">
                            <li>Go to <a href="https://myaccount.google.com/security" target="_blank" rel="noopener noreferrer" className="underline font-medium text-indigo-700">myaccount.google.com/security</a></li>
                            <li>Scroll to "How you sign in to Google"</li>
                            <li>Enable <strong>2-Step Verification</strong> first</li>
                            <li>After that, the App Passwords option will appear</li>
                          </ol>
                        </div>
                        <p className="text-indigo-600 mt-1">Not Gmail? (Zoho, Outlook, etc.) — just use your regular email password.</p>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {fixState === 'reply-to' && fixMessage && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-2">
                  <p className="text-xs text-red-700">{fixMessage}</p>
                </div>
              )}

              <div className="flex justify-end gap-3">
                <button onClick={closeFixModal} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800">
                  Cancel
                </button>
                <button
                  onClick={handleSetupReplyTo}
                  disabled={!replyToEmail || !replyToPassword || fixState === 'saving'}
                  className="px-4 py-2 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 flex items-center gap-2"
                >
                  {fixState === 'saving' ? (
                    <><RefreshCw size={14} className="animate-spin" /> Setting up...</>
                  ) : (
                    <><MailCheck size={14} /> Set Up Reply-To</>
                  )}
                </button>
              </div>
            </div>
          )}
        </div>
      </Modal>

      {/* Diagnose Reply Detection Modal */}
      <Modal isOpen={diagnoseInbox !== null} onClose={closeDiagnosis} title="Reply Detection Diagnostic">
        <div className="space-y-4">
          {diagnoseLoading && (
            <div className="flex items-center gap-3 p-4">
              <RefreshCw size={18} className="animate-spin text-indigo-500" />
              <span className="text-sm text-gray-600">Scanning IMAP inbox and analyzing...</span>
            </div>
          )}
          {diagnoseResult && !diagnoseLoading && (
            <div className="space-y-3">
              {/* Summary */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <div className="bg-gray-50 rounded-lg p-2.5 text-center">
                  <div className="text-lg font-bold text-gray-800">{diagnoseResult.emails_in_inbox ?? 0}</div>
                  <div className="text-xs text-gray-500">Total Emails</div>
                </div>
                <div className="bg-green-50 rounded-lg p-2.5 text-center">
                  <div className="text-lg font-bold text-green-700">{diagnoseResult.reply_emails ?? 0}</div>
                  <div className="text-xs text-green-600">Real Replies</div>
                </div>
                <div className="bg-red-50 rounded-lg p-2.5 text-center">
                  <div className="text-lg font-bold text-red-700">{diagnoseResult.bounce_emails ?? 0}</div>
                  <div className="text-xs text-red-600">Bounces/NDR</div>
                </div>
                <div className="bg-amber-50 rounded-lg p-2.5 text-center">
                  <div className="text-lg font-bold text-amber-700">{diagnoseResult.read_receipt_emails ?? 0}</div>
                  <div className="text-xs text-amber-600">Read Receipts</div>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="bg-indigo-50 rounded-lg p-2.5 text-center">
                  <div className="text-lg font-bold text-indigo-600">{diagnoseResult.matched_to_campaigns ?? 0}</div>
                  <div className="text-xs text-indigo-500">Matched Recipients</div>
                </div>
                <div className="bg-gray-50 rounded-lg p-2.5 text-center">
                  <div className="text-lg font-bold text-gray-800">{diagnoseResult.campaigns_using_inbox?.length ?? 0}</div>
                  <div className="text-xs text-gray-500">Campaigns</div>
                </div>
              </div>

              {/* Issues */}
              {diagnoseResult.issues?.length > 0 && (
                <div className="space-y-2">
                  <h4 className="text-sm font-semibold text-gray-700">Issues Found</h4>
                  {diagnoseResult.issues.map((issue: string, i: number) => (
                    <div key={i} className={`p-2.5 rounded-lg text-xs ${issue.includes('MISMATCH') || issue.includes('won\'t') || issue.includes('going elsewhere') ? 'bg-red-50 border border-red-200 text-red-700' : issue.includes('correctly configured') ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-amber-50 border border-amber-200 text-amber-700'}`}>
                      {issue}
                    </div>
                  ))}
                </div>
              )}

              {/* MX Info */}
              {diagnoseResult.mx_info && !diagnoseResult.mx_info.error && (
                <div className="space-y-1">
                  <h4 className="text-sm font-semibold text-gray-700">Mail Routing</h4>
                  <div className="bg-gray-50 rounded-lg p-2.5 text-xs space-y-1">
                    <p><span className="font-medium">MX Records:</span> {diagnoseResult.mx_info.mx_records?.join(', ')}</p>
                    <p><span className="font-medium">IMAP Server:</span> {diagnoseResult.mx_info.imap_host}</p>
                    <p><span className="font-medium">Match:</span> {diagnoseResult.mx_info.match ? <span className="text-green-600 font-bold">Yes</span> : <span className="text-red-600 font-bold">No — replies go to MX, not IMAP!</span>}</p>
                  </div>
                </div>
              )}

              {/* IMAP Connection */}
              {diagnoseResult.imap_connection && (
                <div className="text-xs">
                  <span className="font-medium">IMAP:</span>{' '}
                  {diagnoseResult.imap_connection.status === 'connected' ? (
                    <span className="text-green-600">Connected to {diagnoseResult.imap_connection.host}</span>
                  ) : (
                    <span className="text-red-600">Failed — {diagnoseResult.imap_connection.error}</span>
                  )}
                </div>
              )}

              {/* Campaigns */}
              {diagnoseResult.campaigns_using_inbox?.length > 0 && (
                <div className="space-y-1">
                  <h4 className="text-sm font-semibold text-gray-700">Campaigns Using This Inbox</h4>
                  {diagnoseResult.campaigns_using_inbox.map((c) => (
                    <div key={c.id} className="bg-gray-50 rounded-lg p-2 text-xs">
                      <p className="font-medium">{c.name} ({c.status})</p>
                      <p className="text-gray-500">Recipients: {c.sent_recipients?.join(', ') || 'none'}</p>
                    </div>
                  ))}
                </div>
              )}

              {/* Recent Emails Sample */}
              {diagnoseResult.email_samples?.length > 0 && (
                <div className="space-y-1">
                  <h4 className="text-sm font-semibold text-gray-700">Recent Emails in IMAP ({diagnoseResult.emails_in_inbox} total)</h4>
                  <div className="max-h-48 overflow-y-auto space-y-1">
                    {diagnoseResult.email_samples.map((e, i) => {
                      const type = e.type || (e.is_reply ? 'reply' : 'other');
                      const badgeClass = type === 'bounce' ? 'bg-red-200 text-red-700'
                        : type === 'read_receipt' ? 'bg-amber-200 text-amber-700'
                        : type === 'reply' ? 'bg-green-200 text-green-700'
                        : 'bg-gray-200 text-gray-600';
                      const badgeLabel = type === 'bounce' ? 'Bounce'
                        : type === 'read_receipt' ? 'Receipt'
                        : type === 'reply' ? 'Reply'
                        : 'Email';
                      const bgClass = type === 'bounce' ? 'bg-red-50'
                        : type === 'read_receipt' ? 'bg-amber-50'
                        : type === 'reply' ? 'bg-green-50'
                        : 'bg-gray-50';
                      return (
                        <div key={i} className={`text-xs p-1.5 rounded ${bgClass}`}>
                          <span className={`inline-block w-14 text-center rounded px-1 text-[10px] font-medium ${badgeClass}`}>
                            {badgeLabel}
                          </span>{' '}
                          <span className="font-medium">{e.from}</span> — {e.subject}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
