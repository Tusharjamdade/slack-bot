import re
import logging
from typing import Optional, List
from langchain_groq import ChatGroq
from langchain.agents import create_agent

from app.config import settings
from app.tools import all_tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are MyAgent, an intelligent AI assistant in Slack with access to Google Workspace productivity tools.

Capabilities:
1. Google Calendar: Check upcoming meetings (list_calendar_events), schedule events (create_calendar_event), search appointments (search_calendar_events).
2. Gmail: Check unread messages (get_unread_emails), search emails (search_emails), read email details (read_email_content), send emails (send_email).
3. Google Keep: List notes/checklists (list_keep_notes), create notes (create_keep_note), append to notes (append_to_keep_note).
4. Tech & General Assistance: Programming, Software Engineering, DevOps, Data Science, Productivity.

STRICT RESPONSE STYLE & FORMATTING RULES:
1. Be extremely concise, crisp, and straight to the point. Aim for 1-3 sentences or short bullet points.
2. NO fluff, greetings, conversational filler, or pleasantries (NEVER say "Sure!", "Here you go:", "Hope this helps!", "Let me know if you need anything else").
3. SLACK FORMATTING ONLY:
   - NEVER use double asterisks (**word**). Slack uses single asterisks (*word*) for bold text.
   - NEVER use horizontal rule lines (---, ___, or --).
   - NEVER use markdown heading hashtags (#, ##, ###). Use single asterisks *Heading* if a title is required.
   - For lists, use simple bullets (- or •) without unnecessary sub-nesting.
   - For code, use standard backticks (`code` or ```code```).
4. When performing an action (e.g. creating an event, sending an email), execute the tool directly and state the outcome in one line.
"""


def format_for_slack(text: str) -> str:
    """Sanitize and convert standard markdown into clean Slack mrkdwn."""
    if not text:
        return ""

    # Convert markdown headers (### Header) to Slack bold (*Header*)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", text, flags=re.MULTILINE)

    # Convert double asterisks **bold** to Slack single asterisk *bold*
    text = re.sub(r"\*\*(.*?)\*\*", r"*\1*", text)

    # Remove horizontal rules (---, ___, ***)
    text = re.sub(r"^[ \t]*[-*_]{3,}[ \t]*$", "", text, flags=re.MULTILINE)

    # Clean up double hyphens / em-dashes into single clean dash
    text = re.sub(r"(?<!-)\s*--+\s*(?!-)", " - ", text)

    # Collapse 3 or more newlines into double newlines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


class AgentService:
    """Service to orchestrate LangChain Groq model with Google Workspace tools."""

    def __init__(self):
        self._agent = None
        self._llm = None

    def _get_llm(self) -> ChatGroq:
        if not self._llm:
            model_name = settings.GROQ_MODEL or "llama-3.3-70b-versatile"
            self._llm = ChatGroq(
                model=model_name,
                api_key=settings.GROQ_API_KEY,
                temperature=0.2,
            )
        return self._llm

    def get_agent(self):
        if not self._agent:
            llm = self._get_llm()
            try:
                self._agent = create_agent(
                    model=llm,
                    tools=all_tools,
                    system_prompt=SYSTEM_PROMPT,
                )
            except Exception as e:
                logger.error("Failed to initialize LangChain agent with tools: %s", e)
                raise e
        return self._agent

    def process_message(self, message: str) -> str:
        """Process incoming user message through agent and return concise Slack-formatted reply."""
        try:
            agent = self.get_agent()
            result = agent.invoke({
                "messages": [{"role": "user", "content": message}]
            })
            messages = result.get("messages", [])
            raw_response = ""
            if messages:
                last_msg = messages[-1]
                content = last_msg.content
                if isinstance(content, list):
                    text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
                    raw_response = "\n".join(text_parts).strip() or str(content)
                else:
                    raw_response = str(content)
            else:
                raw_response = "Request processed with no response."

            return format_for_slack(raw_response)
        except Exception as e:
            logger.exception("Error during agent message processing: %s", e)
            return f"Error processing request: {e}"


agent_service = AgentService()
