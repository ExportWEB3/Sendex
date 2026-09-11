"""
AI-Powered Reply Service

Generates context-aware email replies using AI.
Supports Google Gemini for intelligent, personalized responses.
"""

import os
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import json

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration - Google Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
AI_MODEL = os.getenv("AI_MODEL", "gemini-2.0-flash")  # Default to current fast model
AI_ENABLED = os.getenv("AI_REPLIES_ENABLED", "false").lower() == "true"


class ConversationHistory:
    """Manages email conversation history for context-aware replies."""
    
    def __init__(self, db):
        self.db = db
    
    def get_thread_history(
        self, 
        campaign_id: int, 
        recipient_email: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get conversation history for a specific recipient in a campaign.
        Returns list of emails in chronological order.
        """
        from models.campaign import CampaignRecipient
        from models.warmup import CampaignAutoReply
        
        history = []
        
        # Get the campaign recipient
        cr = self.db.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id == campaign_id,
            CampaignRecipient.recipient.has(email=recipient_email)
        ).first()
        
        if not cr:
            return history
        
        # Get original sent email info
        if cr.sent_at:
            history.append({
                "role": "sent",
                "timestamp": cr.sent_at.isoformat(),
                "subject": cr.campaign.subject if cr.campaign else "Original Email",
                "content": "Original campaign email"  # We'd need to store/retrieve actual content
            })
        
        # Get all auto-replies sent to this recipient
        replies = self.db.query(CampaignAutoReply).filter(
            CampaignAutoReply.campaign_recipient_id == cr.id
        ).order_by(CampaignAutoReply.created_at).all()
        
        for reply in replies:
            # Received reply from recipient
            if reply.original_reply_snippet:
                history.append({
                    "role": "received",
                    "timestamp": reply.created_at.isoformat() if reply.created_at else None,
                    "subject": reply.original_subject or reply.subject,
                    "content": reply.original_reply_snippet
                })
            
            # Our reply back
            if reply.status == "sent" and reply.body_text:
                history.append({
                    "role": "sent",
                    "timestamp": reply.sent_at.isoformat() if reply.sent_at else None,
                    "subject": reply.subject,
                    "content": reply.body_text[:500]  # Truncate for context
                })
        
        return history[-limit:]  # Return last N messages


class AIReplyGenerator:
    """
    Generates intelligent, context-aware email replies using Google Gemini AI.
    """
    
    def __init__(self):
        self.api_key = GEMINI_API_KEY
        self.model = AI_MODEL
        self.enabled = AI_ENABLED and bool(self.api_key)
        
        if not self.enabled:
            logger.info("AI replies disabled (no GEMINI_API_KEY or AI_REPLIES_ENABLED=false)")
    
    def is_available(self) -> bool:
        """Check if AI service is available."""
        return self.enabled
    
    def generate_reply(
        self,
        incoming_email: Dict[str, Any],
        conversation_history: List[Dict[str, Any]],
        sender_info: Dict[str, Any],
        campaign_context: Optional[Dict[str, Any]] = None,
        tone: str = "professional",
        goal: str = "continue_conversation"
    ) -> Dict[str, Any]:
        """
        Generate a context-aware reply to an incoming email.
        
        Args:
            incoming_email: The email we're replying to
                - from_email: sender's email
                - subject: email subject
                - body: email body text
                - received_at: when received
            conversation_history: List of previous emails in the thread
            sender_info: Info about our sender
                - name: sender name
                - email: sender email
                - company: optional company name
            campaign_context: Optional context about the campaign
                - purpose: what the campaign is about
                - product: product/service being promoted
                - call_to_action: desired action
            tone: Reply tone (professional, friendly, casual)
            goal: Reply goal (continue_conversation, schedule_call, close_deal)
        
        Returns:
            Dict with subject, body_text, body_html
        """
        
        if not self.enabled:
            return self._fallback_reply(incoming_email, sender_info)
        
        try:
            from google import genai
            
            client = genai.Client(api_key=self.api_key)
            
            # Build the prompt
            system_prompt = self._build_system_prompt(sender_info, campaign_context, tone, goal)
            user_prompt = self._build_user_prompt(incoming_email, conversation_history)
            
            # Combine system and user prompt
            full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
            
            response = client.models.generate_content(
                model=self.model,
                contents=full_prompt,
                config={
                    "temperature": 0.5,
                    "max_output_tokens": 800,
                }
            )
            
            reply_text = response.text.strip()
            
            # Parse the response
            return self._parse_ai_response(reply_text, incoming_email["subject"], sender_info)
            
        except ImportError:
            logger.warning("Google GenAI package not installed. Run: pip install google-genai")
            return self._fallback_reply(incoming_email, sender_info)
        except Exception as e:
            logger.error(f"Gemini AI reply generation failed: {e}")
            return self._fallback_reply(incoming_email, sender_info)
    
    def _build_system_prompt(
        self,
        sender_info: Dict[str, Any],
        campaign_context: Optional[Dict[str, Any]],
        tone: str,
        goal: str
    ) -> str:
        """Build the system prompt for the AI."""
        
        sender_name = sender_info.get("name", "")
        sender_email = sender_info.get("email", "")
        company = sender_info.get("company", "")
        
        # Get custom goal
        custom_goal = campaign_context.get('custom_goal', '') if campaign_context else ''
        
        # Build context from campaign
        context_info = campaign_context.get('product', '') if campaign_context else ''
        original_subject = campaign_context.get('original_email_subject', '') if campaign_context else ''
        original_body = campaign_context.get('original_email_body', '') if campaign_context else ''
        campaign_name = campaign_context.get('purpose', '') if campaign_context else ''
        
        # Clean HTML from original body for prompt
        if original_body and '<' in original_body:
            import re
            original_body = re.sub(r'<[^>]+>', ' ', original_body)
            original_body = re.sub(r'\s+', ' ', original_body).strip()[:800]
        
        # Tone descriptions
        tone_map = {
            'professional': 'Write in a polished, business-appropriate manner. Use proper grammar and formal phrasing. Be respectful and articulate.',
            'friendly': 'Write in a warm, approachable way while staying professional. Use a conversational style with some personality. Use contractions naturally.',
            'casual': 'Write like you\'re texting a work friend. Keep it short, direct, and relaxed. Use contractions, informal language, and keep paragraphs very short.',
        }
        tone_desc = tone_map.get(tone, tone_map['professional'])
        
        # Goal descriptions with specific behavioral instructions
        if goal == 'custom' and custom_goal:
            goal_desc = f"""YOUR #1 OBJECTIVE (this overrides everything else):
{custom_goal}

Every single reply you write MUST actively work toward this objective. Be creative and strategic. Adapt your approach based on what the person says, but never lose sight of this goal. If they try to change the subject, acknowledge it briefly then steer back."""
        elif goal == 'schedule_call':
            goal_desc = """YOUR #1 OBJECTIVE: Get them on a call/meeting. EVERY reply MUST include a specific call/meeting ask.
- You MUST actively suggest a time or ask about their availability in EVERY reply — no exceptions
- Frame the call as valuable to THEM (e.g., "I can show you exactly how this would work for you")
- If they said no, expressed disinterest, or pushed back: acknowledge briefly, then reframe the value proposition from a different angle and still ask for just 15 minutes
- If they seem hesitant, address their specific concern and re-suggest with lower commitment ("Even just a quick 10-min chat")
- NEVER accept a "no" passively — always pivot and re-ask with a new angle
- Use natural phrasing like "Would [day] at [time] work?" or "Got 15 min this week? I think you'd find it worth it"
- If they already agreed, confirm the details and express enthusiasm
- NEVER just say "let me know" or "feel free to reach out" — that's passive. Always propose a specific next step"""
        elif goal == 'provide_info':
            goal_desc = """YOUR OBJECTIVE: Answer their questions and provide maximum value.
- Directly address every question they asked
- Provide specific, useful details — not vague generalities
- Offer additional relevant info they didn't ask about but would find helpful
- Position yourself/company as knowledgeable and helpful
- End by asking if they need anything else"""
        elif goal == 'close_deal':
            goal_desc = """YOUR OBJECTIVE: Move toward closing a deal/commitment.
- Address any objections or concerns directly
- Emphasize specific value and ROI
- Create subtle urgency without being pushy
- Suggest clear next steps (sign up, purchase, trial)
- If they're interested, make it easy for them to say yes"""
        else:  # continue_conversation
            goal_desc = """YOUR OBJECTIVE: Keep the conversation going naturally.
- Ask a relevant follow-up question based on what they said
- Show genuine interest in their response
- Share something relevant that adds value
- Keep the door open for future interaction
- Don't be pushy — just be helpful and engaging"""

        prompt = f"""You are {sender_name or 'a real person'} ({sender_email}){f', working at {company}' if company else ''}. You are writing email replies that MUST sound like they come from a real human — not an AI, not a template, not a bot.

=== YOUR IDENTITY ===
Name: {sender_name or 'Unknown'}
Email: {sender_email}
{f'Company/Business: {company}' if company else ''}
{f'What we do/offer: {context_info}' if context_info else ''}

=== TONE ===
{tone_desc}

=== GOAL ===
{goal_desc}

=== ORIGINAL CAMPAIGN INFO ===
{f'Campaign: {campaign_name}' if campaign_name else ''}
{f'Original email subject: {original_subject}' if original_subject else ''}
{f'Original email we sent them: {original_body}' if original_body else '(Original email content not available)'}

=== CRITICAL RULES ===
1. ONLY output the email body text. NO subject line. NO "Subject:" prefix. NO email headers.
2. Sound like a REAL HUMAN. Use natural phrasing, vary sentence length, use contractions.
3. DIRECTLY reference what they said in their reply. Quote or paraphrase their specific words.
4. Keep it concise: 2-4 short paragraphs maximum. People don't read long emails.
5. Sign off naturally with just your first name (e.g., "Best,\\n{sender_name.split()[0] if sender_name else 'Team'}").
6. NEVER use these AI-giveaway phrases: "I hope this email finds you well", "Thank you for reaching out", "I wanted to follow up", "I appreciate you getting back to me", "Please don't hesitate to", "I'd be happy to", "Let me know if you have any questions", "Feel free to reach out", "Don't hesitate to contact", "I'll review and follow up", "Got it, thanks for letting me know".
7. Match their energy — if they wrote 2 sentences, don't write 5 paragraphs.
8. If they asked a question, ANSWER IT FIRST before anything else.
9. If they expressed interest, enthusiasm, or concern — acknowledge it specifically.
10. NEVER make up information. If you don't know something, say you'll check and get back to them.
11. Read between the lines. If they sound interested, lean in. If they sound hesitant, address the hesitation. If they sound busy, be brief.
12. Your reply should feel like a natural next message in a real conversation — not a form letter.
13. CRITICAL: Your GOAL section above is your TOP PRIORITY. Every reply MUST actively work toward that goal. Do NOT write a generic "thanks" or "let me know" reply. Push toward the goal EVERY time.
14. If they said "no" or rejected you, do NOT just accept it — acknowledge their response, then reframe your value proposition from a new angle and re-ask. Be persistent but respectful.
15. NEVER write a passive, wishy-washy reply. Always end with a clear, specific next step or question that advances your goal."""

        return prompt
    
    def _build_user_prompt(
        self,
        incoming_email: Dict[str, Any],
        conversation_history: List[Dict[str, Any]]
    ) -> str:
        """Build the user prompt with email content and history."""
        
        prompt_parts = []
        
        # Add conversation history with clear formatting
        if conversation_history:
            prompt_parts.append("=== FULL CONVERSATION THREAD (oldest first) ===")
            for i, msg in enumerate(conversation_history):
                direction = "→ YOU SENT" if msg["role"] == "sent" else "← THEY REPLIED"
                timestamp = msg.get("timestamp", "")
                prompt_parts.append(f"\n--- Message {i+1}: {direction} ---")
                if msg.get("subject"):
                    prompt_parts.append(f"Subject: {msg['subject']}")
                if msg.get("content"):
                    content = msg['content'][:500]
                    prompt_parts.append(f"{content}")
            prompt_parts.append("")
        
        # Add the incoming email we need to reply to
        prompt_parts.append("=== NEW EMAIL YOU MUST REPLY TO NOW ===")
        prompt_parts.append(f"From: {incoming_email.get('from_email', 'Unknown')}")
        prompt_parts.append(f"Subject: {incoming_email.get('subject', 'No subject')}")
        body = incoming_email.get('body', '') or ''
        if body:
            prompt_parts.append(f"\nTheir message:\n\"\"\"\n{body[:1500]}\n\"\"\"")
        else:
            prompt_parts.append("\n(Their reply had no text body — they may have just replied with a short acknowledgment)")
        
        prompt_parts.append("\n=== WRITE YOUR REPLY NOW ===")
        prompt_parts.append("Remember: Only the email body. No subject line. Sound human. Achieve your goal.")
        
        return "\n".join(prompt_parts)
    
    def _parse_ai_response(
        self,
        ai_text: str,
        original_subject: str,
        sender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Parse AI response into email format."""
        
        # Generate subject
        if not original_subject.lower().startswith("re:"):
            subject = f"Re: {original_subject}"
        else:
            subject = original_subject
        
        body_text = ai_text.strip()
        
        # Strip any "Subject:" line the AI might have included
        import re
        body_text = re.sub(r'^Subject:.*\n?', '', body_text, flags=re.IGNORECASE).strip()
        # Strip any "Re: ..." line at the very start
        body_text = re.sub(r'^Re:.*\n?', '', body_text, flags=re.IGNORECASE).strip()
        
        # Generate HTML version
        body_html = f"""<html>
<body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
{body_text.replace(chr(10), '<br>')}
</body>
</html>"""
        
        return {
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "generated_by": "ai",
            "model": self.model
        }
    
    def _fallback_reply(
        self,
        incoming_email: Dict[str, Any],
        sender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a simple fallback reply when AI is unavailable."""
        
        from services.warmup_email_generator import WarmupEmailGenerator
        
        original_subject = incoming_email.get("subject", "Your email")
        sender_name = sender_info.get("name", "")
        recipient_name = incoming_email.get("from_email", "").split("@")[0].replace(".", " ").title()
        
        return WarmupEmailGenerator.generate_reply(
            original_subject=original_subject,
            recipient_name=recipient_name,
            sender_name=sender_name,
            reply_type="acknowledgment"
        )


# Global instance
ai_reply_generator = AIReplyGenerator()


def generate_ai_reply(
    incoming_email: Dict[str, Any],
    conversation_history: List[Dict[str, Any]] = None,
    sender_info: Dict[str, Any] = None,
    campaign_context: Dict[str, Any] = None,
    tone: str = "professional",
    goal: str = "continue_conversation"
) -> Dict[str, Any]:
    """
    Convenience function to generate AI replies.
    
    Usage:
        reply = generate_ai_reply(
            incoming_email={
                "from_email": "prospect@company.com",
                "subject": "Re: Our conversation",
                "body": "Thanks for reaching out! I'm interested in learning more..."
            },
            sender_info={
                "name": "John Smith",
                "email": "john@mycompany.com"
            },
            tone="friendly",
            goal="schedule_call"
        )
    """
    return ai_reply_generator.generate_reply(
        incoming_email=incoming_email,
        conversation_history=conversation_history or [],
        sender_info=sender_info or {},
        campaign_context=campaign_context,
        tone=tone,
        goal=goal
    )
