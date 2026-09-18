import os
import logging
from typing import List, Optional
from app.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Lightweight, fast CPU embedding service powered by FastEmbed (ONNX)."""

    def __init__(self):
        self._model = None
        self._model_name = settings.EMBEDDING_MODEL

    def _get_model(self):
        """Lazy load fastembed TextEmbedding model."""
        if self._model is None:
            try:
                from fastembed import TextEmbedding
                logger.info("Initializing FastEmbed TextEmbedding model: %s", self._model_name)
                # Model cache directory in /app/cache or OS default
                cache_dir = os.getenv("FASTEMBED_CACHE_PATH", None)
                self._model = TextEmbedding(model_name=self._model_name, cache_dir=cache_dir)
                logger.info("FastEmbed model loaded successfully.")
            except ImportError:
                logger.error("fastembed is not installed. Please install fastembed>=0.5.0")
                raise
            except Exception as e:
                logger.error("Failed to load FastEmbed model: %s", e)
                raise
        return self._model

    def embed_query(self, text: str) -> List[float]:
        """Embed a single text query into vector."""
        if not text or not text.strip():
            return [0.0] * settings.EMBEDDING_DIM
        model = self._get_model()
        # FastEmbed returns a generator of numpy arrays
        embeddings = list(model.embed([text.strip()]))
        return embeddings[0].tolist()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embed a list of text documents into vectors."""
        cleaned = [t.strip() if t and t.strip() else " " for t in texts]
        if not cleaned:
            return []
        model = self._get_model()
        embeddings = list(model.embed(cleaned))
        return [vec.tolist() for vec in embeddings]

    @staticmethod
    def chunk_text(text: str, max_chars: int = 600, overlap: int = 80) -> List[str]:
        """Split longer messages or notes into overlapping semantic chunks."""
        text = text.strip()
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + max_chars
            if end >= len(text):
                chunks.append(text[start:].strip())
                break
            
            # Find the best breakpoint (newline, period, or space)
            break_point = text.rfind("\n", start, end)
            if break_point == -1 or break_point <= start + (max_chars // 2):
                break_point = text.rfind(". ", start, end)
                if break_point != -1:
                    break_point += 1
            if break_point == -1 or break_point <= start + (max_chars // 2):
                break_point = text.rfind(" ", start, end)
            
            if break_point == -1 or break_point <= start:
                break_point = end

            chunks.append(text[start:break_point].strip())
            start = break_point - overlap if break_point - overlap > start else break_point

        return [c for c in chunks if c]


embedding_service = EmbeddingService()
