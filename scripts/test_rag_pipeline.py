"""
Verification and unit test script for the Slack AI Agent RAG pipeline.
Tests text chunking, heuristic routing, Slack mrkdwn formatting, and database schema parsing.
"""

import os
import sys
import unittest

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.agent_service import format_for_slack
from app.services.rag.embedding_service import EmbeddingService
from app.services.rag.intent_router import IntentRouter
from app.config import settings


class TestRagPipeline(unittest.TestCase):

    def test_text_chunking(self):
        """Verify semantic text chunker splits long texts properly."""
        short_text = "This is a brief message."
        chunks = EmbeddingService.chunk_text(short_text, max_chars=100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], short_text)

        long_text = "Paragraph one with details. " * 30
        chunks = EmbeddingService.chunk_text(long_text, max_chars=150, overlap=30)
        self.assertTrue(len(chunks) > 1)
        for c in chunks:
            self.assertTrue(len(c) <= 200)

    def test_slack_formatting(self):
        """Verify markdown sanitization for Slack mrkdwn."""
        markdown_sample = "### Meeting Notes\n**Action Item:** Fix bug -- immediately.\n---\n* Done."
        formatted = format_for_slack(markdown_sample)
        # Verify no markdown headers ###
        self.assertNotIn("###", formatted)
        # Verify bold is single asterisk
        self.assertIn("*Action Item:*", formatted)
        # Verify horizontal rules removed
        self.assertNotIn("---", formatted)
        # Verify clean dash
        self.assertIn(" - ", formatted)

    def test_heuristic_intent_router_fallback(self):
        """Verify router heuristic accurately differentiates recall vs direct tool vs QA."""
        router = IntentRouter()

        # 1. Historical recall queries
        recall_1 = router._heuristic_fallback("Do you remember what we discussed yesterday regarding the API?")
        self.assertTrue(recall_1["needs_history"])
        self.assertEqual(recall_1["task_type"], "history_recall")
        self.assertFalse(recall_1["is_chronological"])

        # 2. Conversation overview / past questions
        overview_1 = router._heuristic_fallback("What are my past questions?")
        self.assertTrue(overview_1["needs_history"])
        self.assertTrue(overview_1["is_chronological"])
        self.assertEqual(overview_1["task_type"], "conversation_overview")

        overview_2 = router._heuristic_fallback("What are my past conversations with you?")
        self.assertTrue(overview_2["needs_history"])
        self.assertTrue(overview_2["is_chronological"])
        self.assertEqual(overview_2["task_type"], "conversation_overview")

        # 3. Direct tool execution queries
        tool_1 = router._heuristic_fallback("Schedule a meeting with Alice tomorrow at 3pm")
        self.assertFalse(tool_1["needs_history"])
        self.assertEqual(tool_1["task_type"], "direct_tool")

        tool_2 = router._heuristic_fallback("Check my unread emails in Gmail")
        self.assertFalse(tool_2["needs_history"])
        self.assertEqual(tool_2["task_type"], "direct_tool")

        # 4. Direct QA
        qa_1 = router._heuristic_fallback("How do I sort a dictionary by key in Python?")
        self.assertFalse(qa_1["needs_history"])
        self.assertEqual(qa_1["task_type"], "direct_qa")

    def test_config_settings(self):
        """Verify settings have expected pgvector and RAG defaults."""
        self.assertTrue(settings.ENABLE_RAG)
        self.assertEqual(settings.EMBEDDING_DIM, 384)
        self.assertGreaterEqual(settings.RAG_TOP_K, 5)
        self.assertGreater(settings.RAG_SIMILARITY_THRESHOLD, 0.0)
        dsn = settings.get_database_dsn()
        self.assertTrue(dsn.startswith("postgresql://"))

    def test_embedding_generation(self):
        """Verify FastEmbed generates 384-dimensional vector embeddings."""
        from app.services.rag.embedding_service import embedding_service
        vec = embedding_service.embed_query("testing pgvector rag pipeline")
        self.assertEqual(len(vec), 384)
        self.assertIsInstance(vec[0], float)

        # Test batch embedding
        batch_vecs = embedding_service.embed_documents(["hello world", "database migration"])
        self.assertEqual(len(batch_vecs), 2)
        self.assertEqual(len(batch_vecs[0]), 384)
        self.assertEqual(len(batch_vecs[1]), 384)



if __name__ == "__main__":
    unittest.main()
