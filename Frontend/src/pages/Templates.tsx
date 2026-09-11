import { useMemo, type ReactNode } from 'react';
import { Plus, Edit2, Trash2, Eye, Save, Mail, Megaphone, UserCheck, Bell, Sparkles, Paperclip, RefreshCw, FileText, Image as ImageIcon, Film, X, Code, AlignLeft, ListChecks } from 'lucide-react';
import { Header, Modal, SelectionBar } from '../components';
import { useTemplatesPage } from '../features/templates/useTemplatesPage';
import { sanitizeEmailHtml } from '../utils/sanitize-html';

// ============================================================================
// 5 Built-in Starter Templates
// ============================================================================
const STARTER_TEMPLATES: Array<{
  name: string;
  description: string;
  category: string;
  subject_line: string;
  icon: ReactNode;
  color: string;
  html_content: string;
}> = [
  {
    name: 'Welcome Email',
    description: 'Warm welcome for new subscribers or customers',
    category: 'welcome',
    subject_line: 'Welcome to {{company_name|our community}}, {{first_name|there}}!',
    icon: <UserCheck size={20} />,
    color: 'bg-emerald-500',
    html_content: `<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f7" style="background-color:#f4f4f7;">
  <tr><td align="center" style="padding:40px 20px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="background-color:#ffffff;border:1px solid #e5e7eb;max-width:600px;width:100%;">
      <tr><td bgcolor="#10b981" style="background-color:#10b981;padding:40px 30px;text-align:center;">
        <h1 style="color:#ffffff;margin:0;font-size:28px;font-family:Arial,Helvetica,sans-serif;">Welcome aboard! &#127881;</h1>
      </td></tr>
      <tr><td style="padding:30px;">
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 16px;">Hi <strong>{{first_name|there}}</strong>,</p>
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 16px;">Thanks for joining <strong>{{company_name|us}}</strong>! We're excited to have you with us.</p>
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 12px;">Here's what you can do next:</p>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 16px 16px;">
          <tr><td style="padding:4px 0;font-size:15px;color:#4b5563;">&#8226; Complete your profile</td></tr>
          <tr><td style="padding:4px 0;font-size:15px;color:#4b5563;">&#8226; Explore our features</td></tr>
          <tr><td style="padding:4px 0;font-size:15px;color:#4b5563;">&#8226; Check out our getting started guide</td></tr>
        </table>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:24px auto;">
          <tr><td align="center" bgcolor="#10b981" style="background-color:#10b981;padding:14px 32px;">
            <a href="{{cta_link|#}}" style="color:#ffffff;text-decoration:none;font-weight:bold;font-size:16px;font-family:Arial,Helvetica,sans-serif;display:inline-block;">Get Started</a>
          </td></tr>
        </table>
        <p style="font-size:14px;color:#9ca3af;text-align:center;margin:0;">Need help? Just reply to this email.</p>
        <p style="font-size:12px;color:#9ca3af;text-align:center;margin:16px 0 0;"><a href="{{unsubscribe_link}}" style="color:#9ca3af;">Unsubscribe</a></p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>`,
  },
  {
    name: 'Promotional Offer',
    description: 'Special deal or limited-time promotion',
    category: 'promotional',
    subject_line: '🔥 {{discount|25}}% OFF — Limited time only!',
    icon: <Megaphone size={20} />,
    color: 'bg-orange-500',
    html_content: `<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f7" style="background-color:#f4f4f7;">
  <tr><td align="center" style="padding:40px 20px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="background-color:#ffffff;border:1px solid #e5e7eb;max-width:600px;width:100%;">
      <tr><td bgcolor="#f97316" style="background-color:#f97316;padding:40px 30px;text-align:center;">
        <p style="color:#ffffff;margin:0 0 8px;font-size:14px;text-transform:uppercase;letter-spacing:2px;font-family:Arial,Helvetica,sans-serif;">Limited Time Offer</p>
        <h1 style="color:#ffffff;margin:0;font-size:48px;font-weight:800;font-family:Arial,Helvetica,sans-serif;">{{discount|25}}% OFF</h1>
        <p style="color:#ffffff;margin:8px 0 0;font-size:16px;font-family:Arial,Helvetica,sans-serif;">on {{product_name|our top products}}</p>
      </td></tr>
      <tr><td style="padding:30px;">
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 16px;">Hi {{first_name|there}},</p>
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 16px;">For a limited time, we're offering an exclusive <strong>{{discount|25}}% discount</strong> on {{product_name|our top products}}. Don't miss out!</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
          <tr><td bgcolor="#fff7ed" style="background-color:#fff7ed;border:1px solid #fed7aa;padding:16px;text-align:center;">
            <p style="margin:0;color:#9a3412;font-size:14px;font-family:Arial,Helvetica,sans-serif;">Use code at checkout:</p>
            <p style="margin:8px 0 0;color:#ea580c;font-size:24px;font-weight:800;letter-spacing:3px;font-family:Arial,Helvetica,sans-serif;">{{promo_code|SAVE25}}</p>
          </td></tr>
        </table>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:24px auto;">
          <tr><td align="center" bgcolor="#f97316" style="background-color:#f97316;padding:14px 32px;">
            <a href="{{cta_link|#}}" style="color:#ffffff;text-decoration:none;font-weight:bold;font-size:16px;font-family:Arial,Helvetica,sans-serif;display:inline-block;">Shop Now</a>
          </td></tr>
        </table>
        <p style="font-size:13px;color:#9ca3af;text-align:center;margin:0;">Offer expires {{expiry_date|soon}}. Terms apply.</p>
        <p style="font-size:12px;color:#9ca3af;text-align:center;margin:16px 0 0;"><a href="{{unsubscribe_link}}" style="color:#9ca3af;">Unsubscribe</a></p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>`,
  },
  {
    name: 'Newsletter',
    description: 'Clean newsletter with content sections',
    category: 'newsletter',
    subject_line: '📬 {{newsletter_title|Our Newsletter}} — {{month|Monthly}} Update',
    icon: <Mail size={20} />,
    color: 'bg-blue-500',
    html_content: `<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f7" style="background-color:#f4f4f7;">
  <tr><td align="center" style="padding:40px 20px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="background-color:#ffffff;border:1px solid #e5e7eb;max-width:600px;width:100%;">
      <tr><td bgcolor="#3b82f6" style="background-color:#3b82f6;padding:30px;text-align:center;">
        <h1 style="color:#ffffff;margin:0;font-size:24px;font-family:Arial,Helvetica,sans-serif;">{{newsletter_title|Our Newsletter}}</h1>
        <p style="color:#dbeafe;margin:6px 0 0;font-size:14px;font-family:Arial,Helvetica,sans-serif;">{{newsletter_subtitle|Monthly Update}}</p>
      </td></tr>
      <tr><td style="padding:30px;">
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 16px;">Hi {{first_name|there}},</p>
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 20px;">{{intro_text|Here is what is new this month:}}</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px;">
          <tr>
            <td width="4" bgcolor="#3b82f6" style="background-color:#3b82f6;"></td>
            <td style="padding:12px 16px;">
              <h3 style="color:#1e40af;margin:0 0 8px;font-size:18px;font-family:Arial,Helvetica,sans-serif;">{{heading_1|Feature Update}}</h3>
              <p style="color:#4b5563;margin:0;font-size:15px;line-height:1.5;">{{section_1|We have been working hard on new features to improve your experience.}}</p>
            </td>
          </tr>
        </table>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px;">
          <tr>
            <td width="4" bgcolor="#3b82f6" style="background-color:#3b82f6;"></td>
            <td style="padding:12px 16px;">
              <h3 style="color:#1e40af;margin:0 0 8px;font-size:18px;font-family:Arial,Helvetica,sans-serif;">{{heading_2|Community News}}</h3>
              <p style="color:#4b5563;margin:0;font-size:15px;line-height:1.5;">{{section_2|Join our growing community and stay connected with the latest updates.}}</p>
            </td>
          </tr>
        </table>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:24px auto;">
          <tr><td align="center" bgcolor="#3b82f6" style="background-color:#3b82f6;padding:14px 32px;">
            <a href="{{cta_url|#}}" style="color:#ffffff;text-decoration:none;font-weight:bold;font-size:16px;font-family:Arial,Helvetica,sans-serif;display:inline-block;">{{cta_text|Read More}}</a>
          </td></tr>
        </table>
      </td></tr>
      <tr><td bgcolor="#f9fafb" style="background-color:#f9fafb;padding:20px 30px;text-align:center;border-top:1px solid #e5e7eb;">
        <p style="color:#9ca3af;font-size:12px;margin:0;font-family:Arial,Helvetica,sans-serif;">{{footer_text|You received this because you subscribed.}}</p>
        <p style="color:#9ca3af;font-size:12px;margin:8px 0 0;font-family:Arial,Helvetica,sans-serif;"><a href="{{unsubscribe_link}}" style="color:#9ca3af;">Unsubscribe</a></p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>`,
  },
  {
    name: 'Service Notification',
    description: 'Alert or notification about account/service changes',
    category: 'transactional',
    subject_line: 'Action Required: {{notification_title|Important Update}}',
    icon: <Bell size={20} />,
    color: 'bg-red-500',
    html_content: `<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f7" style="background-color:#f4f4f7;">
  <tr><td align="center" style="padding:40px 20px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="background-color:#ffffff;border:1px solid #e5e7eb;max-width:600px;width:100%;">
      <tr><td bgcolor="#ef4444" style="background-color:#ef4444;padding:30px;text-align:center;">
        <p style="font-size:32px;margin:0 0 12px;line-height:1;">&#9888;&#65039;</p>
        <h1 style="color:#ffffff;margin:0;font-size:22px;font-family:Arial,Helvetica,sans-serif;">{{notification_title|Important Update}}</h1>
      </td></tr>
      <tr><td style="padding:30px;">
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 16px;">Hi {{first_name|there}},</p>
        <p style="font-size:16px;color:#374151;line-height:1.6;margin:0 0 16px;">{{notification_message|We have an important update regarding your account.}}</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:20px 0;">
          <tr><td bgcolor="#fef2f2" style="background-color:#fef2f2;border:1px solid #fecaca;padding:16px;">
            <p style="margin:0;color:#991b1b;font-size:14px;font-family:Arial,Helvetica,sans-serif;"><strong>Important:</strong> {{action_required|Please review and take action.}}</p>
          </td></tr>
        </table>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:24px auto;">
          <tr><td align="center" bgcolor="#ef4444" style="background-color:#ef4444;padding:14px 32px;">
            <a href="{{cta_link|#}}" style="color:#ffffff;text-decoration:none;font-weight:bold;font-size:16px;font-family:Arial,Helvetica,sans-serif;display:inline-block;">Take Action Now</a>
          </td></tr>
        </table>
        <p style="font-size:13px;color:#9ca3af;text-align:center;margin:0;">If you didn't expect this, please contact support.</p>        <p style="font-size:12px;color:#9ca3af;text-align:center;margin:16px 0 0;"><a href="{{unsubscribe_link}}" style="color:#9ca3af;">Unsubscribe</a></p>      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>`,
  },
  {
    name: 'Minimal Clean',
    description: 'Simple, clean template for any purpose',
    category: 'custom',
    subject_line: '{{subject|A message for you}}',
    icon: <Sparkles size={20} />,
    color: 'bg-violet-500',
    html_content: `<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<meta name="supported-color-schemes" content="light only">
</head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:Arial,Helvetica,sans-serif;-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f4f4f7" style="background-color:#f4f4f7;">
  <tr><td align="center" style="padding:40px 20px;">
    <table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" style="background-color:#ffffff;border:1px solid #e5e7eb;max-width:560px;width:100%;">
      <tr><td style="padding:40px;">
        <h1 style="color:#111827;margin:0 0 20px;font-size:22px;font-weight:700;font-family:Arial,Helvetica,sans-serif;">{{heading|Hello}}</h1>
        <p style="font-size:16px;color:#374151;line-height:1.7;margin:0 0 16px;">Hi {{first_name|there}},</p>
        <p style="font-size:16px;color:#374151;line-height:1.7;margin:0 0 24px;">{{body_text|We wanted to share something with you.}}</p>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0">
          <tr><td bgcolor="#7c3aed" style="background-color:#7c3aed;padding:12px 28px;">
            <a href="{{cta_link|#}}" style="color:#ffffff;text-decoration:none;font-weight:bold;font-size:15px;font-family:Arial,Helvetica,sans-serif;display:inline-block;">{{cta_text|Learn More}}</a>
          </td></tr>
        </table>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:32px;">
          <tr><td style="border-top:1px solid #e5e7eb;padding-top:20px;">
            <p style="color:#9ca3af;font-size:13px;margin:0;font-family:Arial,Helvetica,sans-serif;">{{company_name|Our Team}}</p>
            <p style="color:#9ca3af;font-size:12px;margin:8px 0 0;font-family:Arial,Helvetica,sans-serif;"><a href="{{unsubscribe_link}}" style="color:#9ca3af;">Unsubscribe</a></p>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>`,
  },
];

const CATEGORY_COLORS: Record<string, string> = {
  welcome: 'bg-emerald-100 text-emerald-700',
  promotional: 'bg-orange-100 text-orange-700',
  newsletter: 'bg-blue-100 text-blue-700',
  transactional: 'bg-red-100 text-red-700',
  custom: 'bg-violet-100 text-violet-700',
};

export default function Templates() {
  const page = useTemplatesPage();
  const { templates } = page.data;
  const { loading, lastUpdated } = page.status;
  const {
    editorOpen: showEditor,
    editingId,
    form: formData,
    attachments: templateAttachments,
    attachmentUploading,
  } = page.editor;
  const { open: showPreview, html: previewHtml } = page.preview;
  const sanitizedPreviewHtml = useMemo(
    () => sanitizeEmailHtml(previewHtml),
    [previewHtml],
  );
  const {
    enabled: selectMode,
    selectedIds,
    isDeleting: bulkDeleting,
  } = page.selection;
  const loadTemplates = page.actions.refresh;

  const handleHtmlChange = page.editor.updateHtml;
  const handleSubjectChange = page.editor.updateSubject;
  const openEditor = page.editor.open;

  const handleSave = page.actions.save;
  const handleDelete = page.actions.remove;
  const toggleSelectMode = page.selection.toggleMode;
  const toggleSelected = page.selection.toggleOne;
  const handleSelectAll = page.selection.toggleAll;
  const handleBulkDelete = page.actions.removeSelected;

  const handleUseStarter = (starter: typeof STARTER_TEMPLATES[0]) => {
    openEditor({
      name: starter.name,
      description: starter.description,
      category: starter.category,
      subject_line: starter.subject_line,
      html_content: starter.html_content,
    });
  };

  const handleAttachmentUpload = page.editor.uploadAttachments;
  const removeAttachment = page.editor.removeAttachment;

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getFileIcon = (type: string) => {
    if (type.startsWith('image/')) return <ImageIcon className="w-4 h-4 text-blue-500" />;
    if (type.startsWith('video/')) return <Film className="w-4 h-4 text-purple-500" />;
    return <FileText className="w-4 h-4 text-gray-500" />;
  };

  const handlePreview = page.preview.show;

  return (
    <div className="flex-1 flex flex-col min-h-screen">
      <Header title="Email Templates" onRefresh={loadTemplates} lastUpdated={lastUpdated} />

      <div className={`p-4 sm:p-6 flex-1 transition-opacity duration-200 ${loading ? 'opacity-60' : ''}`}>
        {/* Top action bar */}
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-6">
          <p className="text-gray-600">Design and manage email templates for your campaigns</p>
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
              onClick={() => openEditor()}
              className="bg-indigo-600 text-white px-3 py-1.5 text-sm rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2 flex-1 sm:flex-none justify-center"
            >
              <Plus size={16} /> New Template
            </button>
          </div>
        </div>

        {/* Starter Templates */}
        <div className="mb-8">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">Starter Templates</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
            {STARTER_TEMPLATES.map((starter) => (
              <button
                key={starter.name}
                onClick={() => handleUseStarter(starter)}
                className="bg-white rounded-xl shadow p-4 text-left hover:shadow-md hover:-translate-y-0.5 transition-all group"
              >
                <div className={`w-10 h-10 ${starter.color} rounded-lg flex items-center justify-center text-white mb-3 group-hover:scale-110 transition-transform`}>
                  {starter.icon}
                </div>
                <h3 className="font-semibold text-gray-900 text-sm">{starter.name}</h3>
                <p className="text-xs text-gray-500 mt-1 line-clamp-2">{starter.description}</p>
              </button>
            ))}
          </div>
        </div>

        {/* Saved Templates */}
        <div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-3">
            Your Templates {templates.length > 0 && <span className="text-gray-400">({templates.length})</span>}
          </h2>
          {selectMode && templates.length > 0 && (
            <SelectionBar
              count={selectedIds.size}
              total={templates.length}
              onSelectAll={handleSelectAll}
              onDelete={handleBulkDelete}
              onCancel={toggleSelectMode}
              deleting={bulkDeleting}
              itemLabel="template"
            />
          )}
          {templates.length === 0 ? (
            <div className="bg-white rounded-xl shadow p-8 text-center text-gray-500">
              No saved templates yet. Pick a starter above or create one from scratch!
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {templates.map(t => (
                <div key={t.id} className="bg-white rounded-xl shadow p-5">
                  <div className="flex justify-between items-start mb-3">
                    <div className="flex items-start gap-2 flex-1 min-w-0">
                      {selectMode && (
                        <input
                          type="checkbox"
                          checked={selectedIds.has(t.id)}
                          onChange={() => toggleSelected(t.id)}
                          className="mt-1 w-4 h-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 flex-shrink-0"
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <h3 className="font-semibold text-gray-900 truncate">{t.name}</h3>
                        {t.description && <p className="text-xs text-gray-500 mt-1 line-clamp-2">{t.description}</p>}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 mb-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${CATEGORY_COLORS[t.category] || CATEGORY_COLORS.custom}`}>
                      {t.category}
                    </span>
                    {t.template_type === 'plain_text' && (
                      <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-emerald-100 text-emerald-700">
                        Plain Text
                      </span>
                    )}
                    <span className="text-xs text-gray-400">Used {t.usage_count}x</span>
                  </div>
                  <div className="text-xs text-gray-400 mb-3 truncate font-mono">
                    Subject: {t.subject_line}
                  </div>
                  <div className="text-xs text-gray-400 mb-4">
                    Created {new Date(t.created_at).toLocaleDateString()}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handlePreview(
                        t.template_type === 'plain_text'
                          ? `<html><body style="font-family:sans-serif;padding:20px;white-space:pre-wrap;">${t.html_content.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</body></html>`
                          : t.html_content
                      )}
                      className="flex-1 py-1.5 text-sm bg-gray-50 text-gray-700 rounded-lg flex items-center justify-center gap-1.5 hover:bg-gray-100 transition-colors"
                    >
                      <Eye size={14} /> Preview
                    </button>
                    <button
                      onClick={() => openEditor({ ...t })}
                      className="flex-1 py-1.5 text-sm bg-indigo-50 text-indigo-600 rounded-lg flex items-center justify-center gap-1.5 hover:bg-indigo-100 transition-colors"
                    >
                      <Edit2 size={14} /> Edit
                    </button>
                    <button
                      onClick={() => handleDelete(t.id)}
                      className="px-3 py-1.5 bg-red-50 text-red-600 rounded-lg hover:bg-red-100 transition-colors"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Preview Modal - higher z-index so it shows above editor */}
      <Modal isOpen={showPreview} onClose={page.preview.close} title="Template Preview" zIndex={60}>
        <div className="border rounded-lg overflow-auto bg-white" style={{ height: '500px' }}>
          <iframe
            srcDoc={sanitizedPreviewHtml}
            title="Email Preview"
            className="w-full h-full border-0"
            sandbox=""
          />
        </div>
      </Modal>

      {/* Editor Modal */}
      <Modal isOpen={showEditor} onClose={page.editor.close} title={editingId ? 'Edit Template' : 'Create Template'}>
        <form onSubmit={handleSave} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Template Name</label>
              <input
                type="text"
                value={formData.name}
                onChange={(event) => page.editor.update({ name: event.target.value })}
                required
                placeholder="e.g., Welcome Email"
                className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 text-sm"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">Category</label>
              <select
                value={formData.category}
                onChange={(event) => page.editor.update({ category: event.target.value })}
                className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 text-sm"
              >
                <option value="welcome">Welcome</option>
                <option value="promotional">Promotional</option>
                <option value="newsletter">Newsletter</option>
                <option value="transactional">Transactional</option>
                <option value="custom">Custom</option>
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <input
              type="text"
              value={formData.description}
              onChange={(event) => page.editor.update({ description: event.target.value })}
              placeholder="What is this template for?"
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 text-sm"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Subject Line</label>
            <input
              type="text"
              value={formData.subject_line}
              onChange={(e) => handleSubjectChange(e.target.value)}
              required
              placeholder="Use {{variable}} for dynamic content"
              className="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 text-sm font-mono"
            />
          </div>

          {/* Template Type Toggle */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Content Type</label>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => page.editor.update({ template_type: 'html' })}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                  formData.template_type === 'html'
                    ? 'bg-indigo-50 border-indigo-300 text-indigo-700 font-medium'
                    : 'bg-white border-gray-300 text-gray-600 hover:bg-gray-50'
                }`}
              >
                <Code size={14} /> HTML
              </button>
              <button
                type="button"
                onClick={() => page.editor.update({ template_type: 'plain_text' })}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                  formData.template_type === 'plain_text'
                    ? 'bg-emerald-50 border-emerald-300 text-emerald-700 font-medium'
                    : 'bg-white border-gray-300 text-gray-600 hover:bg-gray-50'
                }`}
              >
                <AlignLeft size={14} /> Plain Text
              </button>
            </div>
          </div>

          <div>
            <div className="flex justify-between items-center mb-1">
              <label className="block text-sm font-medium text-gray-700">
                {formData.template_type === 'plain_text' ? 'Plain Text Content' : 'HTML Content'}
              </label>
              {formData.template_type === 'html' && (
                <button
                  type="button"
                  onClick={() => handlePreview(formData.html_content)}
                  className="text-indigo-600 hover:text-indigo-800 text-xs flex items-center gap-1"
                >
                  <Eye size={12} /> Preview
                </button>
              )}
            </div>
            <textarea
              value={formData.html_content}
              onChange={(e) => {
                handleHtmlChange(e.target.value);
                e.target.style.height = 'auto';
                e.target.style.height = Math.min(e.target.scrollHeight, 500) + 'px';
              }}
              required
              rows={formData.template_type === 'plain_text' ? 10 : 6}
              style={{ minHeight: '150px', maxHeight: '500px' }}
              placeholder={formData.template_type === 'plain_text'
                ? 'Write your plain text email here...\nUse {{variable_name}} for dynamic content.\nLine breaks will be preserved.'
                : 'Paste your HTML email here... Use {{variable_name}} for dynamic content'}
              className={`w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-indigo-500 text-sm leading-relaxed resize-none overflow-y-auto ${
                formData.template_type === 'plain_text'
                  ? 'font-sans'
                  : 'font-mono text-xs'
              }`}
            />
            {formData.template_type === 'plain_text' && (
              <p className="text-xs text-gray-400 mt-1">Line breaks will be converted to {'<br>'} tags automatically when sending.</p>
            )}
          </div>

          {formData.available_variables.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              <span className="text-xs text-gray-500">Variables:</span>
              {formData.available_variables.map(v => (
                <span key={v} className="bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded text-xs font-mono">
                  {`{{${v}}}`}
                </span>
              ))}
            </div>
          )}

          {/* Attachments */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              <span className="flex items-center gap-1.5">
                <Paperclip size={14} /> Attachments <span className="text-gray-400 font-normal">(optional — included in every campaign using this template)</span>
              </span>
            </label>
            <div className="border-2 border-dashed border-gray-300 rounded-lg p-3 hover:border-indigo-400 transition-colors">
              <input
                type="file"
                multiple
                onChange={handleAttachmentUpload}
                className="hidden"
                id="template-attachment-input"
                accept="image/*,video/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv,.zip,.rar"
              />
              <label htmlFor="template-attachment-input" className="flex flex-col items-center cursor-pointer py-2">
                {attachmentUploading ? (
                  <div className="flex items-center gap-2 text-sm text-indigo-600">
                    <RefreshCw className="w-4 h-4 animate-spin" /> Uploading...
                  </div>
                ) : (
                  <>
                    <Paperclip className="w-5 h-5 text-gray-400 mb-1" />
                    <span className="text-sm text-gray-600">Click to attach files</span>
                    <span className="text-xs text-gray-400 mt-0.5">These files will be sent with every campaign using this template</span>
                  </>
                )}
              </label>
            </div>

            {templateAttachments.length > 0 && (
              <div className="mt-2 space-y-1.5">
                {templateAttachments.map((att, idx) => (
                  <div key={idx} className="flex items-center gap-2 p-2 bg-gray-50 rounded-lg border border-gray-200">
                    {att.content_type.startsWith('image/') ? (
                      <img src={`/uploads/${att.stored_name}`} alt={att.filename} className="w-10 h-10 object-cover rounded" />
                    ) : (
                      <div className="w-10 h-10 flex items-center justify-center bg-gray-100 rounded">
                        {getFileIcon(att.content_type)}
                      </div>
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-gray-800 truncate">{att.filename}</p>
                      <p className="text-[10px] text-gray-500">{formatFileSize(att.size)}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeAttachment(idx)}
                      className="p-1 text-red-400 hover:text-red-600 hover:bg-red-50 rounded transition-colors"
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={page.editor.close} className="px-3 py-1.5 text-sm text-gray-600 hover:text-gray-800 transition-colors">
              Cancel
            </button>
            <button type="submit" className="px-4 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors flex items-center gap-2">
              <Save size={14} /> {editingId ? 'Update' : 'Create'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
