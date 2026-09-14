import { Header, Modal, ListsSkeleton, ListCard, PageSearch, SelectionBar } from '../components';
import { Plus, ChevronLeft, ChevronRight, Search, X, ListChecks } from 'lucide-react';
import { useListsPage } from '../features/lists/useListsPage';

export function Lists() {
  const page = useListsPage();
  const { lists, visibleLists } = page.data;
  const { loading, initialLoad, lastUpdated } = page.status;
  const {
    createOpen: showModal,
    addRecipientsListId: addRecipientsModal,
  } = page.dialogs;
  const {
    view: viewEmailsModal,
    visible: filteredRecipients,
    total: recipientsTotal,
    page: recipientsPage,
    loading: recipientsLoading,
    search: recipientSearch,
    pageSize: PAGE_SIZE,
  } = page.recipients;
  const {
    enabled: selectMode,
    selectedIds,
    isDeleting: bulkDeleting,
  } = page.selection;
  const loadData = page.actions.refresh;

  const openViewEmails = page.recipients.open;
  const handlePageChange = page.recipients.changePage;

  const handleCreate = page.actions.create;

  const handleDelete = page.actions.remove;
  const toggleSelectMode = page.selection.toggleMode;
  const toggleSelected = page.selection.toggleOne;
  const handleSelectAll = page.selection.toggleAll;
  const handleBulkDelete = page.actions.removeSelected;
  const handleAddRecipients = page.actions.addRecipients;

  return (
    <div className="flex-1 flex flex-col min-h-screen">
      <Header title="Recipient Lists" onRefresh={loadData} lastUpdated={lastUpdated} />
      
      {initialLoad ? <ListsSkeleton /> : (
      <div className={`p-4 sm:p-6 flex-1 transition-opacity duration-200 ${loading ? 'opacity-60' : ''}`}>
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
          <p className="text-gray-600">Manage recipient lists and contacts</p>
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
              onClick={page.dialogs.openCreate}
              className="bg-indigo-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2 flex-1 sm:flex-none justify-center"
            >
              <Plus size={16} /> New List
            </button>
          </div>
        </div>

        <PageSearch
          value={page.search.value}
          onChange={page.search.setValue}
          placeholder="Search lists by name or description..."
          label="Search recipient lists"
          resultCount={page.search.resultCount}
          totalCount={page.search.totalCount}
        />

        {selectMode && (
          <SelectionBar
            count={selectedIds.size}
            total={visibleLists.length}
            onSelectAll={handleSelectAll}
            onDelete={handleBulkDelete}
            onCancel={toggleSelectMode}
            deleting={bulkDeleting}
            itemLabel="list"
          />
        )}

        {/* Cards for all screens */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {lists.length === 0 ? (
            <div className="col-span-full bg-white rounded-xl shadow p-8 text-center text-gray-500">
              No lists yet. Create one to get started!
            </div>
          ) : visibleLists.length === 0 ? (
            <div className="col-span-full border border-gray-200 bg-white p-8 text-center text-gray-500">
              No lists match your search.
            </div>
          ) : (
            visibleLists.map(list => (
              <ListCard
                key={list.id}
                list={list}
                onViewEmails={openViewEmails}
                onAddRecipients={page.dialogs.openAddRecipients}
                onDelete={handleDelete}
                selectMode={selectMode}
                selected={selectedIds.has(list.id)}
                onToggleSelect={toggleSelected}
              />
            ))
          )}
        </div>
      </div>
      )}

      {/* View Emails Modal */}
      <Modal isOpen={!!viewEmailsModal} onClose={page.recipients.close} title={`Emails in "${viewEmailsModal?.listName || ''}"`}>
        <div className="space-y-3">
          {/* Search */}
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Search emails..."
              value={recipientSearch}
              onChange={(event) => page.recipients.setSearch(event.target.value)}
              className="w-full pl-9 pr-8 py-2 text-sm border rounded-lg focus:ring-2 focus:ring-indigo-500"
            />
            {recipientSearch && (
              <button onClick={() => page.recipients.setSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                <X size={14} />
              </button>
            )}
          </div>

          <div className="text-xs text-gray-500">{recipientsTotal} total recipients{recipientSearch ? ` (${filteredRecipients.length} shown)` : ''}</div>

          {/* Table */}
          <div className="max-h-[400px] overflow-y-auto border rounded-lg">
            {recipientsLoading ? (
              <div className="p-8 text-center text-gray-400">Loading...</div>
            ) : filteredRecipients.length === 0 ? (
              <div className="p-8 text-center text-gray-400">{recipientSearch ? 'No matching emails' : 'No recipients in this list'}</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 sticky top-0">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-gray-600">#</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-600">Email</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-600">Name</th>
                    <th className="text-left px-3 py-2 font-medium text-gray-600">Status</th>
                    <th className="text-right px-3 py-2 font-medium text-gray-600">Sent</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {filteredRecipients.map((r, idx) => (
                    <tr key={r.id} className="hover:bg-gray-50">
                      <td className="px-3 py-2 text-gray-400">{recipientsPage * PAGE_SIZE + idx + 1}</td>
                      <td className="px-3 py-2 font-mono text-xs">{r.email}</td>
                      <td className="px-3 py-2 text-gray-600">{[r.first_name, r.last_name].filter(Boolean).join(' ') || '—'}</td>
                      <td className="px-3 py-2">
                        <span className={`inline-block px-2 py-0.5 text-xs rounded-full ${r.status === 'active' ? 'bg-green-100 text-green-700' : r.status === 'unsubscribed' ? 'bg-yellow-100 text-yellow-700' : r.status === 'bounced' ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'}`}>
                          {r.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-right text-gray-600">{r.total_sent}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination */}
          {recipientsTotal > PAGE_SIZE && !recipientSearch && (
            <div className="flex items-center justify-between pt-2">
              <button
                onClick={() => handlePageChange(recipientsPage - 1)}
                disabled={recipientsPage === 0}
                className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg disabled:opacity-40 hover:bg-gray-100 transition-colors"
              >
                <ChevronLeft size={14} /> Previous
              </button>
              <span className="text-xs text-gray-500">
                Page {recipientsPage + 1} of {Math.ceil(recipientsTotal / PAGE_SIZE)}
              </span>
              <button
                onClick={() => handlePageChange(recipientsPage + 1)}
                disabled={(recipientsPage + 1) * PAGE_SIZE >= recipientsTotal}
                className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg disabled:opacity-40 hover:bg-gray-100 transition-colors"
              >
                Next <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      </Modal>

      {/* Create List Modal */}
      <Modal isOpen={showModal} onClose={page.dialogs.closeCreate} title="Create List">
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">List Name</label>
            <input type="text" name="name" required className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description (optional)</label>
            <textarea name="description" rows={2} className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500" />
          </div>
          <div className="flex justify-end gap-3 pt-4">
            <button type="button" onClick={page.dialogs.closeCreate} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 transition-colors">Cancel</button>
            <button type="submit" className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors">Create</button>
          </div>
        </form>
      </Modal>

      {/* Add Recipients Modal */}
      <Modal isOpen={!!addRecipientsModal} onClose={page.dialogs.closeAddRecipients} title="Add Recipients">
        <form onSubmit={(e) => addRecipientsModal && handleAddRecipients(e, addRecipientsModal)} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Recipients (one per line)</label>
            <textarea
              name="recipients"
              rows={8}
              required
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 font-mono text-sm"
              placeholder="email@example.com&#10;another@example.com, Jane&#10;third@example.com, John, Doe"
            />
            <p className="text-xs text-gray-500 mt-1">Format: email (optional: first_name, last_name)</p>
          </div>
          <div className="flex justify-end gap-3 pt-4">
            <button type="button" onClick={page.dialogs.closeAddRecipients} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 transition-colors">Cancel</button>
            <button type="submit" className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors">Add</button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
