import re
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any
from langchain_groq import ChatGroq
from langchain.agents import create_agent

from app.config import settings
from app.tools import all_tools
from app.services.rag.rag_service import rag_service

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are MyAgent, an intelligent AI assistant in Slack with access to Google Workspace productivity tools and persistent conversation memory backed by PostgreSQL with pgvector.

Capabilities:
1. Google Calendar: Check upcoming meetings (list_calendar_events), schedule events (create_calendar_event), search appointments (search_calendar_events).
2. Gmail: Check unread messages (get_unread_emails), search emails (search_emails), read email details (read_email_content), send emails (send_email).
3. Google Keep: Search notes (search_keep_notes), list notes/checklists (list_keep_notes), create notes (create_keep_note), append to notes (append_to_keep_note).
4. Chat History & Memory:
   - View chronological past questions, messages, and conversation recap using get_recent_chat_history tool.
   - Search previous chats, past decisions, and topics via semantic search using search_chat_history tool.
5. Tech & General Assistance: Programming, Software Engineering, DevOps, Data Science, Productivity.

STRICT RESPONSE STYLE & FORMATTING RULES:
1. Be extremely concise, crisp, and straight to the point. Aim for 1-3 sentences or short bullet points.
2. NO fluff, greetings, conversational filler, or pleasantries (NEVER say "Sure!", "Here you go:", "Hope this helps!", "Let me know if you need anything else").
3. SLACK FORMATTING ONLY:
   - NEVER use double asterisks (**word**). Slack uses single asterisks (*word*) for bold text.
   - NEVER use horizontal rule lines (---, ___, or --).
   - NEVER use markdown heading hashtags (#, ##, ###). Use single asterisks *Heading* if a title is required.
   - For lists, use simple bullets (- or •) without unnecessary sub-nesting.
   - For code, use standard backticks (`code` or ```code```).
4. When performing an action (e.g. creating an event, sending an email, adding a note), execute the tool directly and state the outcome in one line.
5. When answering questions that reference past discussions, past questions, or previous knowledge, synthesize the provided conversation history / retrieved knowledge or use get_recent_chat_history / search_chat_history to answer accurately.
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
    """Service to orchestrate LangChain Groq model with Google Workspace tools and pgvector RAG."""

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

    async def process_message(
        self,
        message: str,
        channel_id: str = "general",
        user_id: str = "user",
        thread_ts: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> str:
        """
        Process incoming user message through RAG pipeline, agent, and tools.
        Stores conversation history in PostgreSQL with pgvector embeddings.
        """
        session_id = f"{channel_id}:{thread_ts}" if thread_ts else f"{channel_id}:main"

        # 1. Store incoming user message & compute vector embedding in background/sync
        try:
            await rag_service.store_user_turn(
                session_id=session_id,
                channel_id=channel_id,
                user_id=user_id,
                content=message,
                metadata={"event_id": event_id, "thread_ts": thread_ts},
            )
        except Exception as e:
            logger.warning("Could not persist user message to database: %s", e)

        # 2. Adaptive Retrieval: Check if query requires previous knowledge
        rag_context = ""
        task_type = "direct_qa"
        if settings.ENABLE_RAG:
            try:
                rag_eval = await rag_service.evaluate_and_retrieve(
                    message=message,
                    channel_id=channel_id,
                    session_id=session_id,
                )
                task_type = rag_eval.get("task_type", "direct_qa")
                if rag_eval.get("needs_history"):
                    rag_context = rag_eval.get("context", "")
            except Exception as e:
                logger.warning("Adaptive RAG retrieval error: %s", e)

        # 3. Retrieve short-term recent conversation turns for context continuity
        conversation_history: List[Dict[str, str]] = []
        try:
            recent_msgs = await rag_service.get_recent_history(
                session_id=session_id,
                channel_id=channel_id,
                limit=settings.SHORT_TERM_MEMORY_LIMIT,
            )
            # Exclude current in-flight user prompt
            for msg in recent_msgs:
                if msg.get("role") == "user" and msg.get("content", "").strip() == message.strip():
                    continue
                role = "assistant" if msg["role"] == "assistant" else "user"
                conversation_history.append({"role": role, "content": msg["content"]})
        except Exception as e:
            logger.warning("Could not fetch recent conversation history: %s", e)

        # 4. Construct input prompt for agent
        if rag_context:
            augmented_content = (
                f"{rag_context}\n\n"
                f"User Request (incorporate the retrieved knowledge above if relevant to answer accurately):\n"
                f"{message}"
            )
        else:
            augmented_content = message

        agent_messages = list(conversation_history)
        agent_messages.append({"role": "user", "content": augmented_content})

        # 5. Invoke LangChain Agent
        raw_response = ""
        tool_calls_record = None
        try:
            agent = self.get_agent()
            if hasattr(agent, "ainvoke"):
                result = await agent.ainvoke({"messages": agent_messages})
            else:
                result = await asyncio.to_thread(agent.invoke, {"messages": agent_messages})

            messages = result.get("messages", [])
            if messages:
                last_msg = messages[-1]
                content = last_msg.content
                if isinstance(content, list):
                    text_parts = [part.get("text", "") for part in content if isinstance(part, dict)]
                    raw_response = "\n".join(text_parts).strip() or str(content)
                else:
                    raw_response = str(content)

                # Capture any tool calls from intermediate messages
                tool_calls = []
                for m in messages:
                    if hasattr(m, "tool_calls") and m.tool_calls:
                        tool_calls.extend(m.tool_calls)
                if tool_calls:
                    tool_calls_record = tool_calls
            else:
                raw_response = "Request processed with no response."

        except Exception as e:
            logger.exception("Error during agent message processing: %s", e)
            raw_response = f"Error processing request: {e}"

        # 6. Store assistant reply & compute vector embedding in PostgreSQL
        try:
            await rag_service.store_assistant_turn(
                session_id=session_id,
                channel_id=channel_id,
                user_id="assistant",
                content=raw_response,
                tool_calls=tool_calls_record,
                metadata={"task_type": task_type, "rag_used": bool(rag_context)},
            )
        except Exception as e:
            logger.warning("Could not persist assistant message to database: %s", e)

        return format_for_slack(raw_response)

    def process_message_sync(self, message: str, **kwargs) -> str:
        """Synchronous wrapper for process_message."""
        return asyncio.run(self.process_message(message, **kwargs))


agent_service = AgentService()
