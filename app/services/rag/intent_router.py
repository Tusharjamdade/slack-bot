import re
import json
import logging
from typing import Dict, Any, Optional
from langchain_groq import ChatGroq

from app.config import settings

logger = logging.getLogger(__name__)

ROUTER_PROMPT = """You are an intelligent Intent Router for an AI Assistant in Slack.
Your task is to analyze the user's incoming message and determine whether answering it requires retrieving previous chat history/past knowledge from a database (RAG).

CLASSIFICATION CRITERIA:
1. "conversation_overview": The user is asking for a list of past questions, past conversations, recent messages, or a general recap/overview of what was discussed so far.
   -> needs_history: true
   -> is_chronological: true
   -> search_query: null
   Examples:
   - "What are my past questions?"
   - "What questions did I ask you in past?"
   - "What are my past conversations with you?"
   - "Show my recent chat history."
   - "What did we talk about earlier?"
   - "Summarize our conversation so far."

2. "history_recall": The user is asking about a specific topic, decision, fact, or entity discussed in the past.
   -> needs_history: true
   -> is_chronological: false
   -> search_query: concise keyword query for vector search
   Examples:
   - "What did we decide about the database architecture yesterday?"
   - "What was the meeting time we discussed earlier?"
   - "Do you remember the customer's phone number?"
   - "What did we say about ECS Fargate?"

3. "hybrid": The user wants to execute a tool (e.g. send email, schedule meeting, create note), BUT the details depend on past discussion.
   -> needs_history: true
   -> is_chronological: false
   -> search_query: concise keyword query
   Examples:
   - "Send an email to Alice about the bug we discussed."
   - "Schedule the meeting we talked about earlier."
   - "Add the topics we discussed to my Keep notes."

4. "direct_tool": The user provides all necessary details to perform a workspace action (Google Calendar, Gmail, Keep) right now.
   -> needs_history: false
   -> is_chronological: false
   Examples:
   - "Schedule a meeting with Bob tomorrow at 3pm."
   - "Check my unread emails."
   - "Create a keep note titled 'Groceries' with apples and milk."
   - "Search my calendar for tomorrow's standup."

5. "direct_qa": General knowledge, coding, math, explanations, greetings, or questions that don't depend on past conversation memory.
   -> needs_history: false
   -> is_chronological: false
   Examples:
   - "How do I reverse a linked list in Python?"
   - "Explain the difference between ECS Fargate and EC2."
   - "Hello!" / "Good morning."

Respond ONLY with a JSON object in this exact structure:
{
  "needs_history": true/false,
  "is_chronological": true/false,
  "task_type": "conversation_overview" | "history_recall" | "hybrid" | "direct_tool" | "direct_qa",
  "search_query": "concise keyword-rich search query (or null if is_chronological or not needed)",
  "reasoning": "brief explanation (1 sentence)"
}
"""


class IntentRouter:
    """Adaptive classifier to decide whether RAG vector retrieval or chronological history is needed."""

    def __init__(self):
        self._llm = None

    def _get_llm(self) -> ChatGroq:
        if self._llm is None:
            # Use Groq with temperature 0.0 for fast, deterministic routing
            self._llm = ChatGroq(
                model=settings.GROQ_MODEL or "llama-3.3-70b-versatile",
                api_key=settings.GROQ_API_KEY,
                temperature=0.0,
            )
        return self._llm

    def _heuristic_fallback(self, message: str) -> Dict[str, Any]:
        """Fast fallback rule-based classifier if LLM call is unavailable."""
        msg_lower = message.lower()

        # 1. Patterns strongly indicative of conversation overview / past questions
        overview_patterns = [
            r"\b(past|previous|recent|earlier)\s+(questions?|conversations?|messages?|chats?|queries)\b",
            r"\bwhat\s+(questions?|did i ask|have i asked|were my questions)\b",
            r"\b(what are|show|list|get|tell me)\s+(my\s+)?(past|previous|recent|all)\s+(questions?|conversations?|messages?|chats?)\b",
            r"\b(recap|summarize)\s+(our\s+)?(conversation|chat|discussion|messages)\b",
            r"\bwhat\s+(have we|did we)\s+(talked?|discussed?|chatted)\s+about\b",
        ]
        for pat in overview_patterns:
            if re.search(pat, msg_lower):
                return {
                    "needs_history": True,
                    "is_chronological": True,
                    "task_type": "conversation_overview",
                    "search_query": None,
                    "reasoning": f"Matched chronological overview pattern: '{pat}'",
                }

        # 2. Patterns strongly indicative of specific memory recall
        recall_patterns = [
            r"\b(remember|recall|earlier|previous|yesterday|last week|last month)\b",
            r"\b(we discussed|we talked|we decided|you mentioned|i mentioned|did (we|i|they) say)\b",
            r"\bwhat was (that|the) (topic|link|file|decision|discussion|plan|meeting)\b",
        ]
        for pat in recall_patterns:
            if re.search(pat, msg_lower):
                # Clean filler words for search query
                clean_query = re.sub(r"\b(do you remember|what did we say about|can you recall)\b", "", msg_lower).strip()
                return {
                    "needs_history": True,
                    "is_chronological": False,
                    "task_type": "history_recall",
                    "search_query": clean_query or message,
                    "reasoning": f"Matched historical recall pattern: '{pat}'",
                }

        # 3. Check for direct workspace actions
        tool_patterns = [
            r"\b(schedule|calendar|meeting|appointment|event)\b",
            r"\b(email|gmail|inbox|unread)\b",
            r"\b(keep|note|notes|checklist)\b",
        ]
        for pat in tool_patterns:
            if re.search(pat, msg_lower):
                return {
                    "needs_history": False,
                    "is_chronological": False,
                    "task_type": "direct_tool",
                    "search_query": None,
                    "reasoning": "Direct workspace tool request without past context dependency",
                }

        return {
            "needs_history": False,
            "is_chronological": False,
            "task_type": "direct_qa",
            "search_query": None,
            "reasoning": "General direct question or chit-chat",
        }

    def route_message(self, message: str) -> Dict[str, Any]:
        """
        Analyze the incoming message and return routing decision.
        """
        if not settings.ENABLE_RAG:
            return {
                "needs_history": False,
                "is_chronological": False,
                "task_type": "direct_qa",
                "search_query": None,
                "reasoning": "RAG is disabled in configuration",
            }

        # Short greetings or trivial messages bypass LLM routing
        stripped = message.strip().lower()
        if len(stripped.split()) <= 2 and stripped in ("hi", "hello", "hey", "test", "ok", "thanks", "bye"):
            return {
                "needs_history": False,
                "is_chronological": False,
                "task_type": "direct_qa",
                "search_query": None,
                "reasoning": "Trivial greeting",
            }

        try:
            llm = self._get_llm()
            response = llm.invoke([
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": message},
            ])
            raw_text = response.content
            if isinstance(raw_text, list):
                raw_text = " ".join([p.get("text", "") for p in raw_text if isinstance(p, dict)])

            # Extract JSON block
            json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))
                needs_history = bool(data.get("needs_history", False))
                task_type = data.get("task_type", "direct_qa")
                is_chronological = bool(data.get("is_chronological", False)) or task_type == "conversation_overview"
                search_query = data.get("search_query")
                if needs_history and not is_chronological and not search_query:
                    search_query = message
                return {
                    "needs_history": needs_history,
                    "is_chronological": is_chronological,
                    "task_type": task_type,
                    "search_query": search_query,
                    "reasoning": data.get("reasoning", "LLM router classification"),
                }
            else:
                logger.warning("Intent router output did not contain valid JSON. Using fallback.")
                return self._heuristic_fallback(message)
        except Exception as e:
            logger.warning("Error during intent routing LLM call: %s. Using heuristic fallback.", e)
            return self._heuristic_fallback(message)


intent_router = IntentRouter()
