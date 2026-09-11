"""
Day 8: Email Template Engine
- Variable substitution ({{first_name}}, {{company}}, etc.)
- Unsubscribe link injection
- Tracking pixel injection
"""

import re
import html
import os
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlencode

from services.tracking_signature import sign_tracking_request


GENERIC_EMAIL_LOCAL_PARTS = {
    'admin', 'billing', 'careers', 'contact', 'enquiries', 'hello', 'help',
    'hr', 'info', 'inquiries', 'mail', 'marketing', 'media', 'news',
    'noreply', 'no-reply', 'office', 'press', 'reception', 'sales',
    'service', 'support', 'team', 'webmaster',
}


def infer_recipient_name(email: str) -> Tuple[str, str]:
    """Infer only realistic-looking name parts from an email local part.

    Delimited alphabetic names such as ``arti.johri`` are safe to split. A
    single alphabetic token can be used as a first name unless it is a generic
    mailbox. Initials, digit-heavy/random handles, and concatenated names are
    intentionally left blank rather than guessed.
    """
    local = str(email or '').split('@', 1)[0].split('+', 1)[0].strip().lower()
    if not local or local in GENERIC_EMAIL_LOCAL_PARTS or any(char.isdigit() for char in local):
        return '', ''

    tokens = [token for token in re.split(r'[._\-]+', local) if token]
    if not tokens or any(not token.isalpha() or len(token) > 30 for token in tokens):
        return '', ''

    if len(tokens) == 1:
        token = tokens[0]
        if len(token) < 2 or token in GENERIC_EMAIL_LOCAL_PARTS:
            return '', ''
        # Longer undelimited handles are more likely usernames or concatenated
        # names (for example ``zswilliams``) than a reliably visible first name.
        if len(token) > 8 or not re.search(r'[aeiouy]', token):
            return '', ''
        return token.title(), ''

    # Do not invent a first name from an initial such as m.galingana.
    first_name = tokens[0].title() if len(tokens[0]) > 1 else ''
    last_name = ' '.join(token.title() for token in tokens[1:] if len(token) > 1)
    return first_name, last_name


class TemplateEngine:
    """
    Simple template engine for email personalization.
    
    Supported variables:
    - {{first_name}} - Recipient's first name
    - {{last_name}} - Recipient's last name
    - {{full_name}} - First + Last name
    - {{email}} - Recipient's email
    - {{company}} - Recipient's company
    - {{title}} - Recipient's job title
    - {{unsubscribe_link}} - Unsubscribe URL
    - {{sender_name}} - Sender's name
    - {{sender_email}} - Sender's email
    
    Fallback syntax: {{variable|fallback}}
    Example: {{first_name|there}} → "there" if first_name is empty
    """
    
    # Pattern to match {{variable}} or {{variable|fallback}}
    # Supports whitespace and either snake_case or hyphenated variable names.
    VARIABLE_PATTERN = re.compile(r'\{\{\s*([a-zA-Z0-9_-]+)\s*(?:\|\s*([^}]*?)\s*)?\}\}')
    
    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = os.getenv("BASE_URL", "http://localhost:8000")
        self.base_url = base_url.rstrip('/')
    
    def render(
        self,
        template: str,
        recipient: Dict[str, Any],
        sender: Dict[str, Any] = None,
        campaign_id: int = None,
        include_tracking: bool = False,
        include_unsubscribe: bool = True,
        extra_context: Dict[str, Any] = None
    ) -> str:
        """
        Render a template with recipient data.
        
        Args:
            template: The template string (subject or body)
            recipient: Recipient data dict (email, first_name, last_name, company, title, unsubscribe_token)
            sender: Sender data dict (name, email)
            campaign_id: Campaign ID for tracking
            include_tracking: Whether to add tracking pixel
            include_unsubscribe: Whether to add unsubscribe link
            extra_context: Additional variables (e.g. from campaign template_data)
        
        Returns:
            Rendered template string
        """
        
        # Build context
        context = self._build_context(recipient, sender)
        
        # Merge extra_context (template_data) — these override recipient fields for custom vars
        if extra_context:
            for k, v in extra_context.items():
                if str(k).startswith('_assistant_'):
                    continue
                normalized = self._normalize_var_name(str(k))
                normalized_compact = normalized.replace('_', '')
                value = str(v) if v is not None else ''
                context[normalized] = value
                context[normalized_compact] = value
        
        # Generate unsubscribe link
        unsubscribe_token = recipient.get('unsubscribe_token', '')
        context['unsubscribe_link'] = f"{self.base_url}/unsubscribe/{unsubscribe_token}"
        
        # Replace variables
        result = self._replace_variables(template, context)
        
        return result
    
    def render_html(
        self,
        template: str,
        recipient: Dict[str, Any],
        sender: Dict[str, Any] = None,
        campaign_id: int = None,
        include_tracking: bool = True,
        include_unsubscribe: bool = True,
        extra_context: Dict[str, Any] = None
    ) -> str:
        """
        Render HTML template with tracking pixel and unsubscribe footer.
        GLOBALLY DISABLED: tracking pixels and unsubscribe footers are OFF
        to avoid spam filters on cold domains.
        """
        
        # First do variable replacement
        result = self.render(
            template, recipient, sender, campaign_id,
            include_tracking=False, include_unsubscribe=False,
            extra_context=extra_context
        )
        
        # GLOBALLY DISABLED — tracking pixels trigger spam filters on cold domains
        # if include_tracking and campaign_id:
        #     tracking_pixel = self._generate_tracking_pixel(campaign_id, recipient.get('id'))
        #     result = self._inject_tracking_pixel(result, tracking_pixel)
        
        # Auto-inject unsubscribe footer if template doesn't already have one
        if include_unsubscribe:
            unsubscribe_token = recipient.get('unsubscribe_token', '')
            if unsubscribe_token and '{{unsubscribe_link}}' not in template and '/unsubscribe/' not in result:
                result = self._inject_unsubscribe_footer(result, unsubscribe_token)
        
        return result
    
    def _build_context(self, recipient: Dict[str, Any], sender: Dict[str, Any] = None) -> Dict[str, str]:
        """Build variable context from recipient and sender data."""

        email = str(recipient.get('email', '') or '')
        email_local = email.split('@')[0] if '@' in email else email

        first_name = str(recipient.get('first_name', '') or '').strip()
        last_name = str(recipient.get('last_name', '') or '').strip()

        # Fill only realistic-looking names from the email. Unclear/random
        # handles stay blank and are rendered as the neutral word "there".
        inferred_first, inferred_last = infer_recipient_name(email)
        if not first_name:
            first_name = inferred_first
        if not last_name:
            last_name = inferred_last

        full_name = f"{first_name} {last_name}".strip()

        context = {
            'first_name': first_name,
            'firstname': first_name,
            'last_name': last_name,
            'lastname': last_name,
            'full_name': full_name,
            'fullname': full_name,
            'client_name': full_name,
            'clientname': full_name,
            'recipient_name': full_name,
            'recipientname': full_name,
            'email': email,
            'company': recipient.get('company', '') or '',
            'title': recipient.get('title', '') or '',
        }

        if sender:
            sender_name = sender.get('name') or sender.get('from_name') or ''
            sender_email = sender.get('email') or sender.get('from_email') or ''
            context['sender_name'] = str(sender_name)
            context['sendername'] = str(sender_name)
            context['sender_email'] = str(sender_email)
            context['senderemail'] = str(sender_email)

        return context

    def _normalize_var_name(self, var_name: str) -> str:
        """Normalize variable names so aliases resolve consistently."""
        return (var_name or '').strip().lower().replace('-', '_')
    
    def _replace_variables(self, template: str, context: Dict[str, str]) -> str:
        """Replace all {{variable}} occurrences with values from context."""

        def replacer(match):
            raw_var_name = match.group(1)
            var_name = self._normalize_var_name(raw_var_name)
            fallback = match.group(2)  # May be None

            value = context.get(var_name, '')

            # Try compact alias, e.g. first_name -> firstname.
            if (value is None or value == '') and '_' in var_name:
                value = context.get(var_name.replace('_', ''), '')

            # Ensure string output.
            if value is None:
                value = ''
            elif not isinstance(value, str):
                value = str(value)

            # Use fallback if value is empty
            if not value and fallback is not None:
                value = fallback

            # Name placeholders are automatic, not unresolved campaign
            # requirements. When no realistic name exists, produce a natural
            # greeting ("Hi there") instead of inventing a person's name.
            if not value and fallback is None and var_name in {
                'first_name', 'firstname', 'last_name', 'lastname', 'full_name', 'fullname',
                'client_name', 'clientname', 'recipient_name', 'recipientname',
            }:
                value = 'there'

            return value

        return self.VARIABLE_PATTERN.sub(replacer, template)

    def resolve_sender_context(
        self,
        recipient: Dict[str, Any],
        sender_email: Optional[str],
        sender_name_template: Optional[str] = None,
        extra_context: Dict[str, Any] = None,
        fallback_sender_name: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Resolve sender data for template rendering and From header generation.

        sender_name_template may contain template variables such as {{first_name}}.
        """
        safe_sender_email = sender_email or ''
        default_name = fallback_sender_name
        if not default_name:
            default_name = safe_sender_email.split('@')[0].replace('.', ' ').title() if safe_sender_email else ''

        rendered_name = ''
        if sender_name_template:
            # Use a stable base sender context to avoid recursive sender_name templates.
            base_sender = {
                'name': default_name,
                'email': safe_sender_email,
            }
            rendered_name = self.render(
                template=sender_name_template,
                recipient=recipient,
                sender=base_sender,
                include_tracking=False,
                include_unsubscribe=False,
                extra_context=extra_context,
            ).strip()

        # Never fall back to the raw template string (could contain unresolved {{vars}}).
        final_name = rendered_name or default_name

        return {
            'name': final_name,
            'email': safe_sender_email,
            'from_name': final_name,
        }
    
    def _generate_tracking_pixel(self, campaign_id: int, recipient_id: int = None) -> str:
        """Generate a 1x1 tracking pixel image tag."""
        
        params = {'c': campaign_id}
        if recipient_id:
            params['r'] = recipient_id
        params['s'] = sign_tracking_request(campaign_id, recipient_id)
        
        tracking_url = f"{self.base_url}/api/track/open?{urlencode(params)}"
        
        return f'<img src="{tracking_url}" width="1" height="1" style="display:none;" alt="" />'
    
    def _inject_tracking_pixel(self, html_content: str, tracking_pixel: str) -> str:
        """Inject tracking pixel before </body> tag."""
        
        if '</body>' in html_content.lower():
            # Insert before </body>
            return re.sub(
                r'(</body>)',
                f'{tracking_pixel}\\1',
                html_content,
                flags=re.IGNORECASE
            )
        else:
            # Append at end
            return html_content + tracking_pixel
    
    def _inject_unsubscribe_footer(self, html_content: str, token: str) -> str:
        """Inject unsubscribe footer before </body> tag."""
        
        unsubscribe_url = f"{self.base_url}/unsubscribe/{token}"
        
        footer = f'''
        <div style="margin-top: 40px; padding-top: 20px; border-top: 1px solid #e5e7eb; text-align: center; font-size: 12px; color: #6b7280;">
            <p>You received this email because you're subscribed to our mailing list.</p>
            <p><a href="{unsubscribe_url}" style="color: #6b7280;">Unsubscribe</a> from future emails.</p>
        </div>
        '''
        
        if '</body>' in html_content.lower():
            return re.sub(
                r'(</body>)',
                f'{footer}\\1',
                html_content,
                flags=re.IGNORECASE
            )
        else:
            return html_content + footer
    
    def wrap_clicks(self, html_content: str, campaign_id: int, recipient_id: int = None) -> str:
        """
        Wrap all links in HTML for click tracking.
        Replaces <a href="URL"> with <a href="track_url?original=URL">
        """
        
        def link_replacer(match):
            original_url = match.group(1)
            
            # Skip unsubscribe links and mailto
            if 'unsubscribe' in original_url.lower() or original_url.startswith('mailto:'):
                return match.group(0)
            
            # Build tracking URL
            params = {
                'c': campaign_id,
                'url': original_url,
                's': sign_tracking_request(campaign_id, recipient_id, original_url),
            }
            if recipient_id:
                params['r'] = recipient_id
            
            track_url = f"{self.base_url}/api/track/click?{urlencode(params)}"
            
            return f'href="{track_url}"'
        
        # Match href="..." but not javascript: or mailto:
        pattern = r'href="(https?://[^"]+)"'
        return re.sub(pattern, link_replacer, html_content)
    
    def preview(self, template: str, sample_recipient: Dict[str, Any] = None) -> str:
        """
        Preview a template with sample data.
        """
        
        if not sample_recipient:
            sample_recipient = {
                'email': 'john@example.com',
                'first_name': 'John',
                'last_name': 'Smith',
                'company': 'Acme Corp',
                'title': 'CTO',
                'unsubscribe_token': 'sample_token_123'
            }
        
        sample_sender = {
            'name': 'Your Company',
            'email': 'hello@yourcompany.com'
        }
        
        return self.render(template, sample_recipient, sample_sender)
    
    @staticmethod
    def get_available_variables() -> Dict[str, str]:
        """Return list of available template variables with descriptions."""
        
        return {
            '{{first_name}}': "Recipient's first name",
            '{{last_name}}': "Recipient's last name",
            '{{full_name}}': "First + Last name (or email username if empty)",
            '{{email}}': "Recipient's email address",
            '{{company}}': "Recipient's company name",
            '{{title}}': "Recipient's job title",
            '{{unsubscribe_link}}': "Unsubscribe URL",
            '{{sender_name}}': "Sender's display name",
            '{{sender_email}}': "Sender's email address",
            '{{variable|fallback}}': "Use 'fallback' if variable is empty"
        }
