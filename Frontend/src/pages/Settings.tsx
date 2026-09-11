import { useState } from 'react';
import { Header, SettingsSkeleton } from '../components';
import { Key, Plus, Copy, Users, Shield, Clock, RefreshCw, Trash2, Mail, Eye, EyeOff } from 'lucide-react';
import { useSettingsPage } from '../features/settings/useSettingsPage';
import { getDisplayWarmupDay } from '../warmupDay';

export function Settings() {
  const {
    permissions: { isAdmin, currentUserId },
    data: {
      activationCodes,
      users,
      selectedUserDetails,
      fallbackConfigured,
      fallbackInfo,
    },
    status: {
      initialLoad,
      loadingCodes,
      loadingUsers,
      loadingUserDetails,
      loadingFallback,
      creatingCode,
      savingFallback,
    },
    admin: {
      refreshCodes: loadActivationCodes,
      refreshUsers: loadUsers,
      createCode,
      deleteCode: handleDeleteCode,
      copyCode: handleCopyCode,
      openUserDetails: loadUserDetails,
      closeUserDetails,
      toggleRole: handleToggleRole,
    },
    fallback: {
      refresh: refreshFallback,
      save: saveFallback,
      remove: removeFallback,
    },
    actions: { refresh },
  } = useSettingsPage();

  const [newCodeDays, setNewCodeDays] = useState(30);
  const [newCodeNote, setNewCodeNote] = useState('');
  const [fallbackEmailOverride, setFallbackEmailOverride] = useState<string | null>(null);
  const [fallbackPassword, setFallbackPassword] = useState('');
  const [showFallbackPassword, setShowFallbackPassword] = useState(false);
  const fallbackEmail = fallbackEmailOverride ?? fallbackInfo?.email ?? '';

  const loadFallbackEmail = async () => {
    setFallbackEmailOverride(null);
    return refreshFallback();
  };

  const handleRefresh = async () => {
    setFallbackEmailOverride(null);
    await refresh();
  };

  const handleSaveFallback = async () => {
    if (await saveFallback(fallbackEmail, fallbackPassword)) {
      setFallbackEmailOverride(null);
      setFallbackPassword('');
    }
  };

  const handleDeleteFallback = async () => {
    if (await removeFallback()) {
      setFallbackEmailOverride(null);
      setFallbackPassword('');
    }
  };

  const handleCreateCode = async () => {
    if (await createCode(newCodeDays, newCodeNote)) setNewCodeNote('');
  };

  return (
    <div className="flex-1 flex flex-col min-h-screen">
      <Header title="Settings" onRefresh={handleRefresh} lastUpdated={null} />
      
      {initialLoad ? <SettingsSkeleton /> : (
      <div className="p-4 sm:p-6 flex-1">
        {/* Admin Sections */}
        {isAdmin && (
          <>
            {/* Activation Codes */}
            <div className="bg-white/60 backdrop-blur-xl rounded-2xl border border-white/40 shadow-[0_8px_32px_rgba(0,0,0,0.06)] p-6 mb-6">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-500/10 backdrop-blur-sm rounded-xl ring-1 ring-indigo-500/20">
                    <Key className="w-5 h-5 text-indigo-600" />
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-gray-900">Activation Codes</h2>
                    <p className="text-sm text-gray-500">Generate codes for new user registration</p>
                  </div>
                </div>
                <button
                  onClick={loadActivationCodes}
                  disabled={loadingCodes}
                  className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
                >
                  <RefreshCw className={`w-4 h-4 ${loadingCodes ? 'animate-spin' : ''}`} />
                </button>
              </div>

              {/* Create New Code */}
              <div className="mb-6 p-4 bg-white/50 backdrop-blur-sm rounded-xl border border-gray-200/60">
                <h3 className="font-medium text-gray-900 mb-3">Create New Code</h3>
                <div className="flex flex-wrap gap-3 items-end">
                  <div>
                    <label className="block text-sm text-gray-600 mb-1">Duration (days)</label>
                    <input
                      type="number"
                      min="1"
                      max="365"
                      value={newCodeDays}
                      onChange={(e) => setNewCodeDays(parseInt(e.target.value) || 30)}
                      className="w-24 px-3 py-2 bg-white/70 border border-gray-200/80 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 transition-all"
                    />
                  </div>
                  <div className="flex-1 min-w-[200px]">
                    <label className="block text-sm text-gray-600 mb-1">Note (optional)</label>
                    <input
                      type="text"
                      value={newCodeNote}
                      onChange={(e) => setNewCodeNote(e.target.value)}
                      placeholder="e.g., For John Doe"
                      className="w-full px-3 py-2 bg-white/70 border border-gray-200/80 rounded-xl focus:ring-2 focus:ring-indigo-500/40 focus:border-indigo-400 transition-all"
                    />
                  </div>
                  <button
                    onClick={handleCreateCode}
                    disabled={creatingCode}
                    className="px-3 py-1.5 text-sm bg-indigo-600/90 backdrop-blur-sm text-white rounded-xl hover:bg-indigo-600 shadow-lg shadow-indigo-500/20 disabled:opacity-50 flex items-center gap-1.5 transition-all"
                  >
                    <Plus size={16} />
                    {creatingCode ? 'Creating...' : 'Generate Code'}
                  </button>
                </div>
              </div>

              {/* Codes List */}
              <div className="space-y-2">
                {activationCodes.length === 0 ? (
                  <p className="text-center text-gray-500 py-4">No activation codes yet</p>
                ) : (
                  activationCodes.map((code) => (
                    <div
                      key={code.id}
                      className={`p-3 rounded-xl transition-all ${code.used_at ? 'bg-gray-100/50 border border-gray-200/60' : 'bg-white/60 backdrop-blur-sm border border-white/50 shadow-sm hover:shadow-md hover:bg-white/80'}`}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <code className={`font-mono text-sm ${code.used_at ? 'text-gray-400' : 'text-indigo-600 font-medium'}`}>
                            {code.code}
                          </code>
                          {!code.used_at && (
                            <button
                              onClick={() => handleCopyCode(code.code)}
                              className="p-1 text-gray-400 hover:text-gray-600"
                              title="Copy code"
                            >
                              <Copy size={14} />
                            </button>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={`px-2 py-0.5 rounded text-xs ${code.used_at ? 'bg-gray-200 text-gray-600' : 'bg-green-100 text-green-700'}`}>
                            {code.used_at ? 'Used' : 'Available'}
                          </span>
                          <span className="text-xs text-gray-500 flex items-center gap-1">
                            <Clock size={12} />
                            {code.duration_days} days
                          </span>
                          {!code.used_at && (
                            <button
                              onClick={() => handleDeleteCode(code.id)}
                              className="p-1 text-red-400 hover:text-red-600"
                              title="Delete code"
                            >
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      </div>
                      {code.note && (
                        <p className="text-xs text-gray-500 mt-1">{code.note}</p>
                      )}
                      {code.used_at && code.used_by_email && (
                        <p className="text-xs text-gray-500 mt-1">Used by: {code.used_by_email}</p>
                      )}
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* User Management */}
            <div className="bg-white/60 backdrop-blur-xl rounded-2xl border border-white/40 shadow-[0_8px_32px_rgba(0,0,0,0.06)] p-6 mb-6">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-500/10 backdrop-blur-sm rounded-xl ring-1 ring-indigo-500/20">
                    <Users className="w-5 h-5 text-indigo-600" />
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-gray-900">User Management</h2>
                    <p className="text-sm text-gray-500">Manage user accounts and roles</p>
                  </div>
                </div>
                <button
                  onClick={loadUsers}
                  disabled={loadingUsers}
                  className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
                >
                  <RefreshCw className={`w-4 h-4 ${loadingUsers ? 'animate-spin' : ''}`} />
                </button>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="text-left py-2 px-3 font-medium text-gray-600">User</th>
                      <th className="text-left py-2 px-3 font-medium text-gray-600">Role</th>
                      <th className="text-left py-2 px-3 font-medium text-gray-600">Status</th>
                      <th className="text-left py-2 px-3 font-medium text-gray-600">Expires</th>
                      <th className="text-left py-2 px-3 font-medium text-gray-600">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr 
                        key={u.id} 
                        className="border-b border-gray-200/60 hover:bg-indigo-50/40 cursor-pointer transition-colors"
                        onClick={() => loadUserDetails(u.id)}
                      >
                        <td className="py-2 px-3">
                          <div>
                            <p className="font-medium text-gray-900">{u.name || 'No name'}</p>
                            <p className="text-xs text-gray-500">{u.email}</p>
                          </div>
                        </td>
                        <td className="py-2 px-3">
                          <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-xs ${u.role === 'admin' ? 'bg-indigo-500/10 text-indigo-700 ring-1 ring-indigo-500/20' : 'bg-gray-500/10 text-gray-700 ring-1 ring-gray-500/10'}`}>
                            {u.role === 'admin' && <Shield size={12} />}
                            {u.role}
                          </span>
                        </td>
                        <td className="py-2 px-3">
                          <span className={`px-2 py-0.5 rounded text-xs ${u.is_active ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                            {u.is_active ? 'Active' : 'Disabled'}
                          </span>
                        </td>
                        <td className="py-2 px-3 text-xs text-gray-500">
                          {u.account_expires_at 
                            ? new Date(u.account_expires_at).toLocaleDateString()
                            : 'Never'}
                        </td>
                        <td className="py-2 px-3">
                          {u.id === currentUserId ? (
                            <span className="text-xs text-gray-400">You</span>
                          ) : (
                            <button
                              onClick={(e) => { e.stopPropagation(); handleToggleRole(u.id, u.role); }}
                              className="text-xs text-indigo-600 hover:text-indigo-800"
                            >
                              {u.role === 'admin' ? 'Make User' : 'Make Admin'}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {/* Fallback Reply Email - Available to all users */}
        <div className="bg-white/60 backdrop-blur-xl rounded-2xl border border-white/40 shadow-[0_8px_32px_rgba(0,0,0,0.06)] p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-amber-500/10 backdrop-blur-sm rounded-xl ring-1 ring-amber-500/20">
                <Mail className="w-5 h-5 text-amber-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-gray-900">Global Reply-To Email (MX Mismatch Fallback)</h2>
                <p className="text-sm text-gray-500">When any SMTP has MX mismatch, auto-replies are sent through this email instead</p>
              </div>
            </div>
            <button
              onClick={loadFallbackEmail}
              disabled={loadingFallback}
              className="p-1.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
            >
              <RefreshCw className={`w-4 h-4 ${loadingFallback ? 'animate-spin' : ''}`} />
            </button>
          </div>

          {fallbackConfigured && fallbackInfo && (
            <div className="mb-4 p-3 bg-green-50/80 border border-green-200/60 rounded-xl flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-2 h-2 rounded-full bg-green-500" />
                <span className="text-sm font-medium text-green-800">{fallbackInfo.email}</span>
                <span className="text-xs text-green-600">via {fallbackInfo.smtp_host}</span>
              </div>
              <button
                onClick={handleDeleteFallback}
                className="p-1 text-red-400 hover:text-red-600 transition-colors"
                title="Remove fallback email"
              >
                <Trash2 size={14} />
              </button>
            </div>
          )}

          <div className="p-4 bg-white/50 backdrop-blur-sm rounded-xl border border-gray-200/60">
            <h3 className="font-medium text-gray-900 mb-3">
              {fallbackConfigured ? 'Update Fallback Email' : 'Configure Fallback Email'}
            </h3>
            <p className="text-xs text-gray-500 mb-3">
              When auto-replies can't be sent through the original inbox (MX mismatch, SMTP issues), 
              the system will automatically reroute through this email. Use a Gmail/Outlook address with an app password.
            </p>
            <div className="flex flex-col sm:flex-row gap-3">
              <div className="flex-1">
                <label className="block text-sm text-gray-600 mb-1">Email Address</label>
                <input
                  type="email"
                  value={fallbackEmail}
                  onChange={(e) => setFallbackEmailOverride(e.target.value)}
                  placeholder="fallback@gmail.com"
                  className="w-full px-3 py-2 bg-white/70 border border-gray-200/80 rounded-xl focus:ring-2 focus:ring-amber-500/40 focus:border-amber-400 transition-all text-sm"
                />
              </div>
              <div className="flex-1">
                <label className="block text-sm text-gray-600 mb-1">App Password</label>
                <div className="relative">
                  <input
                    type={showFallbackPassword ? 'text' : 'password'}
                    value={fallbackPassword}
                    onChange={(e) => setFallbackPassword(e.target.value)}
                    placeholder="xxxx xxxx xxxx xxxx"
                    className="w-full px-3 py-2 pr-9 bg-white/70 border border-gray-200/80 rounded-xl focus:ring-2 focus:ring-amber-500/40 focus:border-amber-400 transition-all text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => setShowFallbackPassword(!showFallbackPassword)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                  >
                    {showFallbackPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                  </button>
                </div>
              </div>
              <div className="flex items-end">
                <button
                  onClick={handleSaveFallback}
                  disabled={savingFallback || !fallbackEmail || !fallbackPassword}
                  className="px-4 py-2 text-sm bg-amber-600/90 backdrop-blur-sm text-white rounded-xl hover:bg-amber-600 shadow-lg shadow-amber-500/20 disabled:opacity-50 flex items-center gap-1.5 transition-all whitespace-nowrap"
                >
                  {savingFallback ? <RefreshCw size={14} className="animate-spin" /> : <Mail size={14} />}
                  {savingFallback ? 'Testing...' : 'Save & Test'}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      )}

      {/* User Details Modal */}
      {selectedUserDetails && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white/80 backdrop-blur-2xl rounded-2xl shadow-[0_24px_80px_rgba(0,0,0,0.12)] border border-white/60 max-w-4xl w-full max-h-[90vh] overflow-y-auto">
            <div className="sticky top-0 bg-white/70 backdrop-blur-xl border-b border-gray-200/60 px-6 py-4 flex items-center justify-between">
              <div>
                <h2 className="text-xl font-bold text-gray-900">
                  {selectedUserDetails.user.name || selectedUserDetails.user.email}
                </h2>
                <p className="text-sm text-gray-500">{selectedUserDetails.user.email}</p>
              </div>
              <button
                onClick={closeUserDetails}
                className="text-gray-400 hover:text-gray-600 text-2xl"
              >
                ×
              </button>
            </div>
            
            <div className="p-6 space-y-6">
              {/* User Info */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-white/50 backdrop-blur-sm rounded-xl p-3 border border-white/60 shadow-sm">
                  <p className="text-xs text-gray-500">Role</p>
                  <p className="font-medium">{selectedUserDetails.user.role}</p>
                </div>
                <div className="bg-white/50 backdrop-blur-sm rounded-xl p-3 border border-white/60 shadow-sm">
                  <p className="text-xs text-gray-500">Status</p>
                  <p className={`font-medium ${selectedUserDetails.user.is_active ? 'text-green-600' : 'text-red-600'}`}>
                    {selectedUserDetails.user.is_active ? 'Active' : 'Disabled'}
                  </p>
                </div>
                <div className="bg-white/50 backdrop-blur-sm rounded-xl p-3 border border-white/60 shadow-sm">
                  <p className="text-xs text-gray-500">Created</p>
                  <p className="font-medium text-sm">
                    {selectedUserDetails.user.created_at 
                      ? new Date(selectedUserDetails.user.created_at).toLocaleDateString() 
                      : 'N/A'}
                  </p>
                </div>
                <div className="bg-white/50 backdrop-blur-sm rounded-xl p-3 border border-white/60 shadow-sm">
                  <p className="text-xs text-gray-500">Expires</p>
                  <p className="font-medium text-sm">
                    {selectedUserDetails.user.account_expires_at 
                      ? new Date(selectedUserDetails.user.account_expires_at).toLocaleDateString() 
                      : 'Never'}
                  </p>
                </div>
              </div>

              {/* Stats */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <div className="bg-indigo-500/8 backdrop-blur-sm rounded-xl p-4 text-center border border-indigo-500/15 shadow-sm">
                  <p className="text-2xl font-bold text-indigo-600">{selectedUserDetails.stats.campaigns}</p>
                  <p className="text-xs text-indigo-600/70">Campaigns</p>
                </div>
                <div className="bg-emerald-500/8 backdrop-blur-sm rounded-xl p-4 text-center border border-emerald-500/15 shadow-sm">
                  <p className="text-2xl font-bold text-emerald-600">{selectedUserDetails.stats.smtp_accounts}</p>
                  <p className="text-xs text-emerald-600/70">SMTP Accounts</p>
                </div>
                <div className="bg-violet-500/8 backdrop-blur-sm rounded-xl p-4 text-center border border-violet-500/15 shadow-sm">
                  <p className="text-2xl font-bold text-violet-600">{selectedUserDetails.stats.inboxes}</p>
                  <p className="text-xs text-violet-600/70">Inboxes</p>
                </div>
                <div className="bg-amber-500/8 backdrop-blur-sm rounded-xl p-4 text-center border border-amber-500/15 shadow-sm">
                  <p className="text-2xl font-bold text-amber-600">{selectedUserDetails.stats.lists}</p>
                  <p className="text-xs text-amber-600/70">Lists</p>
                </div>
                <div className="bg-gray-500/8 backdrop-blur-sm rounded-xl p-4 text-center border border-gray-500/15 shadow-sm">
                  <p className="text-2xl font-bold text-gray-600">{selectedUserDetails.stats.total_recipients}</p>
                  <p className="text-xs text-gray-600/70">Recipients</p>
                </div>
              </div>

              {/* Campaigns */}
              {selectedUserDetails.campaigns.length > 0 && (
                <div>
                  <h3 className="font-semibold text-gray-800 mb-2">Campaigns</h3>
                  <div className="bg-white/40 backdrop-blur-sm rounded-xl overflow-hidden border border-white/60">
                    <table className="w-full text-sm">
                      <thead className="bg-white/50">
                        <tr>
                          <th className="text-left py-2 px-3">Name</th>
                          <th className="text-left py-2 px-3">Status</th>
                          <th className="text-left py-2 px-3">Sent / Total</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedUserDetails.campaigns.map(c => (
                          <tr key={c.id} className="border-t border-gray-200">
                            <td className="py-2 px-3">{c.name}</td>
                            <td className="py-2 px-3">
                              <span className={`px-2 py-0.5 rounded text-xs ${
                                c.status === 'completed' ? 'bg-green-100 text-green-700' :
                                c.status === 'running' ? 'bg-blue-100 text-blue-700' :
                                'bg-gray-100 text-gray-700'
                              }`}>{c.status}</span>
                            </td>
                            <td className="py-2 px-3">{c.total_sent} / {c.total_recipients}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* SMTP Accounts */}
              {selectedUserDetails.smtp_accounts.length > 0 && (
                <div>
                  <h3 className="font-semibold text-gray-800 mb-2">SMTP Accounts</h3>
                  <div className="bg-white/40 backdrop-blur-sm rounded-xl overflow-hidden border border-white/60">
                    <table className="w-full text-sm">
                      <thead className="bg-white/50">
                        <tr>
                          <th className="text-left py-2 px-3">Name</th>
                          <th className="text-left py-2 px-3">Host</th>
                          <th className="text-left py-2 px-3">From Email</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedUserDetails.smtp_accounts.map(s => (
                          <tr key={s.id} className="border-t border-gray-200">
                            <td className="py-2 px-3">{s.name}</td>
                            <td className="py-2 px-3">{s.host}</td>
                            <td className="py-2 px-3">{s.from_email}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Inboxes */}
              {selectedUserDetails.inboxes.length > 0 && (
                <div>
                  <h3 className="font-semibold text-gray-800 mb-2">Inboxes</h3>
                  <div className="bg-white/40 backdrop-blur-sm rounded-xl overflow-hidden border border-white/60">
                    <table className="w-full text-sm">
                      <thead className="bg-white/50">
                        <tr>
                          <th className="text-left py-2 px-3">Email</th>
                          <th className="text-left py-2 px-3">State</th>
                          <th className="text-left py-2 px-3">Warmup Day</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedUserDetails.inboxes.map(i => (
                          <tr key={i.id} className="border-t border-gray-200">
                            <td className="py-2 px-3">{i.email}</td>
                            <td className="py-2 px-3">
                              <span className={`px-2 py-0.5 rounded text-xs ${
                                i.state === 'warmed_up' ? 'bg-green-100 text-green-700' :
                                i.state === 'warming_up' ? 'bg-yellow-100 text-yellow-700' :
                                'bg-gray-100 text-gray-700'
                              }`}>{i.state}</span>
                            </td>
                            <td className="py-2 px-3">Day {getDisplayWarmupDay(i)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Lists */}
              {selectedUserDetails.lists.length > 0 && (
                <div>
                  <h3 className="font-semibold text-gray-800 mb-2">Recipient Lists</h3>
                  <div className="bg-white/40 backdrop-blur-sm rounded-xl overflow-hidden border border-white/60">
                    <table className="w-full text-sm">
                      <thead className="bg-white/50">
                        <tr>
                          <th className="text-left py-2 px-3">Name</th>
                          <th className="text-left py-2 px-3">Recipients</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedUserDetails.lists.map(l => (
                          <tr key={l.id} className="border-t border-gray-200">
                            <td className="py-2 px-3">{l.name}</td>
                            <td className="py-2 px-3">{l.recipient_count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* No data message */}
              {selectedUserDetails.stats.campaigns === 0 &&
               selectedUserDetails.stats.smtp_accounts === 0 &&
               selectedUserDetails.stats.inboxes === 0 &&
               selectedUserDetails.stats.lists === 0 && (
                <div className="text-center py-8 text-gray-500">
                  <p>This user has no data yet.</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Loading overlay for user details */}
      {loadingUserDetails && (
        <div className="fixed inset-0 bg-black/20 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-white/80 backdrop-blur-xl rounded-2xl p-6 flex items-center gap-3 shadow-[0_8px_32px_rgba(0,0,0,0.08)] border border-white/60">
            <RefreshCw className="w-5 h-5 animate-spin text-indigo-600" />
            <span>Loading user details...</span>
          </div>
        </div>
      )}
    </div>
  );
}
