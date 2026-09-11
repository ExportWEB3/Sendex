import random
from typing import List, Tuple
from datetime import datetime


class WarmupEmailGenerator:
    """Generates natural-looking warm-up emails"""
    
    # Casual subject lines (30+ variations)
    SUBJECT_TEMPLATES = [
        "Quick question",
        "Following up",
        "Hey, got a minute?",
        "Touching base",
        "Quick update",
        "Just checking in",
        "A quick thought",
        "Something came up",
        "Re: our conversation",
        "One more thing",
        "Wanted to share this",
        "Quick favor",
        "Thoughts on this?",
        "Need your input",
        "Brief question",
        "When you get a chance",
        "Quick note",
        "Circling back",
        "Following up on our chat",
        "A quick ask",
        "Something to consider",
        "Wanted to mention",
        "Quick heads up",
        "Just a thought",
        "Before I forget",
        "Re: next steps",
        "Quick sync",
        "Checking availability",
        "Brief update",
        "One quick thing",
        "Wanted to loop you in",
        "Quick question for you",
        "Need a quick favor",
        "Something interesting",
        "When you have time",
    ]
    
    # Greetings
    GREETINGS = [
        "Hi {name}",
        "Hey {name}",
        "Hello {name}",
        "Hi there",
        "Hey there",
        "Good morning",
        "Good afternoon",
        "Hope you're doing well",
        "Hope this finds you well",
        "Trust you're doing great",
    ]
    
    # Opening lines
    OPENINGS = [
        "I wanted to reach out about something.",
        "Just had a quick thought I wanted to share.",
        "I've been meaning to get in touch.",
        "Hope you're having a good week so far.",
        "I was thinking about our last conversation.",
        "Something came up that I thought you'd find interesting.",
        "I wanted to follow up on a few things.",
        "Just wanted to touch base with you.",
        "I had a question I was hoping you could help with.",
        "I came across something relevant to what we discussed.",
    ]
    
    # Body sentences (will combine 2-4 randomly)
    BODY_SENTENCES = [
        "I've been looking into some options and wanted to get your thoughts.",
        "There are a few things I think we should consider moving forward.",
        "I think this could be a good opportunity for us to explore.",
        "Let me know what you think when you get a chance.",
        "I'd love to hear your perspective on this.",
        "I believe there might be some synergies here worth exploring.",
        "This has been on my mind lately and I wanted to share.",
        "I think it would be worthwhile to discuss this further.",
        "There's been some interesting developments I wanted to mention.",
        "I wanted to make sure we're on the same page about this.",
        "I've given this some thought and have a few ideas.",
        "It would be great to get your input on this.",
        "I think we should probably touch base about this soon.",
        "There are a couple of things I'd like to run by you.",
        "I wanted to loop you in on this before moving forward.",
        "Let me know if you have any questions or concerns.",
        "I'm curious to hear what you think about this approach.",
        "I think this aligns well with what we discussed previously.",
        "I wanted to make sure this is on your radar.",
        "Feel free to share any thoughts or feedback you might have.",
    ]
    
    # Closing lines
    CLOSINGS = [
        "Let me know what you think.",
        "Looking forward to hearing from you.",
        "Talk soon!",
        "Let me know if you have any questions.",
        "Would love to hear your thoughts.",
        "Get back to me when you can.",
        "Thanks in advance!",
        "Appreciate your help with this.",
        "Hope to hear from you soon.",
        "Take care!",
        "Chat soon.",
        "Thanks!",
        "Best regards.",
        "Cheers!",
        "All the best.",
    ]
    
    # Sign-offs
    SIGNOFFS = [
        "Best,",
        "Thanks,",
        "Cheers,",
        "Best regards,",
        "Talk soon,",
        "All the best,",
        "Regards,",
        "Warmly,",
        "Take care,",
        "Thanks!",
    ]
    
    @classmethod
    def generate_subject(cls) -> str:
        """Generate a random subject line"""
        return random.choice(cls.SUBJECT_TEMPLATES)
    
    @classmethod
    def generate_greeting(cls, recipient_name: str = None) -> str:
        """Generate a random greeting"""
        greeting = random.choice(cls.GREETINGS)
        if recipient_name and "{name}" in greeting:
            # Use first name only
            first_name = recipient_name.split()[0] if recipient_name else "there"
            return greeting.format(name=first_name)
        elif "{name}" in greeting:
            return greeting.replace(" {name}", "")
        return greeting
    
    @classmethod
    def generate_body(cls, word_count_range: Tuple[int, int] = (30, 80)) -> str:
        """Generate random body text with 30-80 words"""
        
        # Start with opening
        sentences = [random.choice(cls.OPENINGS)]
        
        # Add 2-4 body sentences
        num_sentences = random.randint(2, 4)
        body_sentences = random.sample(cls.BODY_SENTENCES, num_sentences)
        sentences.extend(body_sentences)
        
        body = " ".join(sentences)
        
        # Rough word count check
        words = body.split()
        if len(words) < word_count_range[0]:
            # Add another sentence if too short
            sentences.append(random.choice(cls.BODY_SENTENCES))
            body = " ".join(sentences)
        
        return body
    
    @classmethod
    def generate_closing(cls) -> str:
        """Generate a random closing line"""
        return random.choice(cls.CLOSINGS)
    
    @classmethod
    def generate_signoff(cls, sender_name: str = None) -> str:
        """Generate sign-off with optional name"""
        signoff = random.choice(cls.SIGNOFFS)
        if sender_name:
            return f"{signoff}\n{sender_name}"
        return signoff
    
    @classmethod
    def generate_email(
        cls,
        recipient_name: str = None,
        sender_name: str = None
    ) -> dict:
        """Generate a complete warm-up email"""
        
        subject = cls.generate_subject()
        greeting = cls.generate_greeting(recipient_name)
        body = cls.generate_body()
        closing = cls.generate_closing()
        signoff = cls.generate_signoff(sender_name)
        
        # Combine into full text
        full_text = f"{greeting},\n\n{body}\n\n{closing}\n\n{signoff}"
        
        # Generate HTML version
        full_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
            <p>{greeting},</p>
            <p>{body}</p>
            <p>{closing}</p>
            <p>{signoff.replace(chr(10), '<br>')}</p>
        </body>
        </html>
        """
        
        return {
            "subject": subject,
            "body_text": full_text,
            "body_html": full_html,
            "greeting": greeting,
            "body": body,
            "closing": closing,
            "signoff": signoff
        }
    
    @classmethod
    def generate_reply(
        cls,
        original_subject: str,
        recipient_name: str = None,
        sender_name: str = None,
        reply_type: str = "acknowledgment"
    ) -> dict:
        """Generate a reply email (for warm-up reply simulation)"""
        
        # Reply subject
        if not original_subject.lower().startswith("re:"):
            subject = f"Re: {original_subject}"
        else:
            subject = original_subject
        
        greeting = cls.generate_greeting(recipient_name)
        
        # Different reply types
        reply_bodies = {
            "acknowledgment": [
                "Thanks for reaching out! I'll take a look at this and get back to you.",
                "Got it, thanks for letting me know. I'll review and follow up.",
                "Thanks for the update! I appreciate you keeping me in the loop.",
                "Received, thank you! I'll give this some thought.",
                "Thanks for sharing this. Let me look into it and I'll get back to you.",
            ],
            "question": [
                "Thanks for this! Quick question - could you clarify what you mean by that?",
                "Interesting, thanks for sharing. What timeline are you thinking for this?",
                "Got it. Just to make sure I understand - are you suggesting we move forward with this?",
                "Thanks! One thing I'm curious about - how does this fit with our current approach?",
                "Appreciate the update. Do you have any specific recommendations?",
            ],
            "agreement": [
                "Great points! I completely agree with your thinking here.",
                "This makes a lot of sense. I think we should definitely move forward.",
                "I was thinking the same thing! Let's do it.",
                "Sounds good to me. I'm on board with this approach.",
                "Perfect, I think that's the right call. Count me in.",
            ],
            "detailed": [
                "Thanks for bringing this up. I've given it some thought and I think there are a few things we should consider. First, we need to make sure the timing is right. Second, we should probably get a few other people involved. Let me know what you think about this approach.",
                "I appreciate you reaching out about this. I've been thinking about it too, and I believe we're on the right track. There are definitely some opportunities here that we should explore further. Would be great to discuss in more detail when you have time.",
            ],
            "clarification": [
                "Thanks for explaining. That makes more sense now. I'll proceed accordingly.",
                "Ah, I see what you mean now. Thanks for clarifying!",
                "Got it, that helps a lot. I was wondering about that. Thanks for the context.",
                "Thanks for the clarification. That answers my question. I'll move forward with this.",
            ],
        }
        
        body = random.choice(reply_bodies.get(reply_type, reply_bodies["acknowledgment"]))
        closing = cls.generate_closing()
        signoff = cls.generate_signoff(sender_name)
        
        full_text = f"{greeting},\n\n{body}\n\n{closing}\n\n{signoff}"
        
        full_html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6;">
            <p>{greeting},</p>
            <p>{body}</p>
            <p>{closing}</p>
            <p>{signoff.replace(chr(10), '<br>')}</p>
        </body>
        </html>
        """
        
        return {
            "subject": subject,
            "body_text": full_text,
            "body_html": full_html,
            "reply_type": reply_type
        }


# Convenience functions
def generate_warmup_email(recipient_name: str = None, sender_name: str = None) -> dict:
    return WarmupEmailGenerator.generate_email(recipient_name, sender_name)

def generate_warmup_reply(original_subject: str, recipient_name: str = None, sender_name: str = None) -> dict:
    reply_type = random.choice(["acknowledgment", "question", "agreement", "detailed", "clarification"])
    return WarmupEmailGenerator.generate_reply(original_subject, recipient_name, sender_name, reply_type)
