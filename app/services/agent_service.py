import re
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any
from langchain_groq import ChatGroq
from langchain.agents import create_agent

from app.config import settings
from app.tools import all_tools, get_tools_for_workspace
from app.services.rag.rag_service import rag_service

logger = logging.getLogger(__name__)

from datetime import datetime

SYSTEM_PROMPT = """You are MyAgent, an AI assistant in Slack with Google Calendar, Gmail, focus timers, and conversation memory.

Rules:
1. Be extremely concise (1-3 sentences or short bullets). Never use greetings, filler, or pleasantries.
2. Slack mrkdwn only: *bold*, `code`, - bullets. Never use **double asterisks** or markdown headings (#).
3. Actions: Execute the required tool directly and confirm in 1 short line.
4. Time: Use Current System Time for relative dates/times."""

_HEADER_RE = re.compile(r"^#{1,6}\s*(.+)$", flags=re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_HR_RE = re.compile(r"^[ \t]*[-*_]{3,}[ \t]*$", flags=re.MULTILINE)
_DASH_RE = re.compile(r"(?<!-)\s*--+\s*(?!-)")
_NEWLINES_RE = re.compile(r"\n{3,}")


def format_for_slack(text: str) -> str:
    """Sanitize and convert standard markdown into clean Slack mrkdwn (optimized)."""
    if not text:
        return ""

    # Convert markdown headers (### Header) to Slack bold (*Header*)
    text = _HEADER_RE.sub(r"*\1*", text)

    # Convert double asterisks **bold** to Slack single asterisk *bold*
    text = _BOLD_RE.sub(r"*\1*", text)

    # Remove horizontal rules (---, ___, ***)
    text = _HR_RE.sub("", text)

    # Clean up double hyphens / em-dashes into single clean dash
    text = _DASH_RE.sub(" - ", text)

    # Collapse 3 or more newlines into double newlines
    text = _NEWLINES_RE.sub("\n\n", text)

    return text.strip()


class AgentService:
    """Service to orchestrate LangChain Groq model with Google Workspace tools and pgvector RAG."""

    def __init__(self):
        self._agents_cache: Dict[tuple, Any] = {}
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

    def get_agent(self, enabled_tools: Optional[List[str]] = None):
        tools = get_tools_for_workspace(enabled_tools)
        tool_key = tuple(sorted(t.name for t in tools))
        if tool_key not in self._agents_cache:
            llm = self._get_llm()
            try:
                self._agents_cache[tool_key] = create_agent(
                    model=llm,
                    tools=tools,
                    system_prompt=SYSTEM_PROMPT,
                )
            except Exception as e:
                logger.error("Failed to initialize LangChain agent with tools: %s", e)
                raise e
        return self._agents_cache[tool_key]

    async def process_message(
        self,
        message: str,
        channel_id: str = "general",
        user_id: str = "user",
        thread_ts: Optional[str] = None,
        event_id: Optional[str] = None,
        team_id: Optional[str] = None,
        enabled_tools: Optional[List[str]] = None,
    ) -> str:
        """
        Process incoming user message through RAG pipeline, agent, and tools.
        Stores conversation history in PostgreSQL with pgvector embeddings.
        """
        # Namespace session_id with team_id for complete multi-tenant workspace isolation
        team_prefix = f"{team_id}:" if team_id else ""
        session_id = f"{team_prefix}{channel_id}:{thread_ts}" if thread_ts else f"{team_prefix}{channel_id}:main"

        # 1. Store incoming user message & compute vector embedding in background/sync
        try:
            await rag_service.store_user_turn(
                session_id=session_id,
                channel_id=channel_id,
                user_id=user_id,
                content=message,
                metadata={"event_id": event_id, "thread_ts": thread_ts},
                team_id=team_id,
            )
        except Exception as e:
            logger.warning("Could not persist user message to database: %s", e)

        # 2. Adaptive Retrieval: Check if query requires previous knowledge with team_id isolation
        rag_context = ""
        task_type = "direct_qa"
        if settings.ENABLE_RAG:
            try:
                rag_eval = await rag_service.evaluate_and_retrieve(
                    message=message,
                    channel_id=channel_id,
                    session_id=session_id,
                    team_id=team_id,
                )
                task_type = rag_eval.get("task_type", "direct_qa")
                if rag_eval.get("needs_history"):
                    rag_context = rag_eval.get("context", "")
            except Exception as e:
                logger.warning("Adaptive RAG retrieval error: %s", e)

        # 3. Retrieve short-term recent conversation turns for context continuity (max 3 turns, truncated)
        conversation_history: List[Dict[str, str]] = []
        try:
            recent_msgs = await rag_service.get_recent_history(
                session_id=session_id,
                channel_id=channel_id,
                limit=min(settings.SHORT_TERM_MEMORY_LIMIT, 3),
            )
            for msg in recent_msgs:
                if msg.get("role") == "user" and msg.get("content", "").strip() == message.strip():
                    continue
                role = "assistant" if msg["role"] == "assistant" else "user"
                content = (msg.get("content") or "").strip()
                # Hard limit past turns to 200 chars to conserve tokens
                if len(content) > 200:
                    content = content[:200] + "..."
                conversation_history.append({"role": role, "content": content})
        except Exception as e:
            logger.warning("Could not fetch recent conversation history: %s", e)

        # 4. Construct input prompt for agent with real-time temporal grounding
        now_str = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
        time_header = f"[Current System Time: {now_str}]"

        if rag_context:
            augmented_content = f"{time_header}\n{rag_context}\n{message}"
        else:
            augmented_content = f"{time_header}\n{message}"

        agent_messages = list(conversation_history)
        agent_messages.append({"role": "user", "content": augmented_content})

        # 5. Invoke LangChain Agent with Automatic Recovery for Rate Limits
        raw_response = ""
        tool_calls_record = None
        agent = self.get_agent(enabled_tools=enabled_tools)

        async def _execute_agent(msgs: list) -> tuple:
            if hasattr(agent, "ainvoke"):
                res = await agent.ainvoke({"messages": msgs})
            else:
                res = await asyncio.to_thread(agent.invoke, {"messages": msgs})

            resp_text = ""
            tc_record = None
            out_messages = res.get("messages", [])
            if out_messages:
                last_m = out_messages[-1]
                cnt = last_m.content
                if isinstance(cnt, list):
                    text_pts = [p.get("text", "") for p in cnt if isinstance(p, dict)]
                    resp_text = "\n".join(text_pts).strip() or str(cnt)
                else:
                    resp_text = str(cnt)

                tc_list = []
                for m in out_messages:
                    if hasattr(m, "tool_calls") and m.tool_calls:
                        tc_list.extend(m.tool_calls)
                if tc_list:
                    tc_record = tc_list
            return resp_text or "Request processed with no response.", tc_record

        try:
            raw_response, tool_calls_record = await _execute_agent(agent_messages)
        except Exception as e:
            err_str = str(e)
            if "rate_limit_exceeded" in err_str or "413" in err_str or "429" in err_str:
                logger.warning("Rate limit hit with history (%s). Retrying with pruned minimal context...", err_str)
                try:
                    # Retry with bare minimum tokens: only immediate prompt, zero history
                    minimal_messages = [{"role": "user", "content": f"{time_header}\n{message}"}]
                    raw_response, tool_calls_record = await _execute_agent(minimal_messages)
                except Exception as retry_err:
                    logger.exception("Retry failed after rate limit: %s", retry_err)
                    raw_response = f"Rate limit reached on model. Please retry in a few seconds."
            else:
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
                team_id=team_id,
            )
        except Exception as e:
            logger.warning("Could not persist assistant message to database: %s", e)

        return format_for_slack(raw_response)

    def process_message_sync(self, message: str, **kwargs) -> str:
        """Synchronous wrapper for process_message."""
        return asyncio.run(self.process_message(message, **kwargs))


agent_service = AgentService()
