import logging
from typing import Optional, List, Dict, Any
from app.config import settings
from app.services.google.auth import get_google_credentials

logger = logging.getLogger(__name__)


class KeepService:
    """Service to interact with Google Keep (supports official Google Keep API and gkeepapi)."""

    def __init__(self):
        self._official_service = None
        self._gkeep = None
        self._gkeep_logged_in = False

    def _get_official_service(self):
        creds = get_google_credentials(scopes=["https://www.googleapis.com/auth/keep"])
        if creds:
            try:
                from googleapiclient.discovery import build
                if not self._official_service or creds.expired:
                    self._official_service = build("keep", "v1", credentials=creds)
                return self._official_service
            except Exception as e:
                logger.debug("Official Keep API initialization failed: %s", e)
        return None

    def _get_gkeep_client(self):
        if self._gkeep_logged_in and self._gkeep:
            return self._gkeep

        try:
            import gkeepapi
            keep = gkeepapi.Keep()
            username = settings.GOOGLE_KEEP_USERNAME
            password = settings.GOOGLE_KEEP_PASSWORD
            master_token = settings.GOOGLE_KEEP_MASTER_TOKEN

            if master_token and username:
                keep.resume(username, master_token)
                self._gkeep = keep
                self._gkeep_logged_in = True
                return self._gkeep
            elif username and password:
                keep.authenticate(username, password)
                self._gkeep = keep
                self._gkeep_logged_in = True
                return self._gkeep
        except Exception as e:
            logger.warning("gkeepapi authentication failed: %s", e)

        return None

    def list_notes(self, query: str = "", max_results: int = 10) -> List[Dict[str, Any]]:
        """List or search notes from Google Keep."""
        # 1. Try official API (Google Workspace)
        official = self._get_official_service()
        if official:
            try:
                res = official.notes().list().execute()
                notes = res.get("notes", [])
                results = []
                for n in notes:
                    title = n.get("title", "")
                    body = n.get("body", {}).get("text", {}).get("text", "")
                    if not query or query.lower() in title.lower() or query.lower() in body.lower():
                        results.append({
                            "id": n.get("name"),
                            "title": title or "Untitled",
                            "text": body,
                        })
                    if len(results) >= max_results:
                        break
                return results
            except Exception as e:
                logger.debug("Official keep notes.list failed: %s", e)

        # 2. Try gkeepapi (Personal accounts)
        gkeep = self._get_gkeep_client()
        if gkeep:
            try:
                gkeep.sync()
                notes_gen = gkeep.find(query=query) if query else gkeep.all()
                results = []
                for n in notes_gen:
                    results.append({
                        "id": n.id,
                        "title": n.title or "Untitled",
                        "text": n.text or "",
                    })
                    if len(results) >= max_results:
                        break
                return results
            except Exception as e:
                logger.warning("gkeep search failed: %s", e)

        raise PermissionError(
            "Google Keep is not configured. For Google Workspace accounts, ensure OAuth scope "
            "'https://www.googleapis.com/auth/keep' is granted in token.json. For personal Gmail accounts, "
            "provide GOOGLE_KEEP_USERNAME and GOOGLE_KEEP_PASSWORD (App Password) in .env."
        )

    def create_note(self, title: str, text: str) -> Dict[str, Any]:
        """Create a new note in Google Keep."""
        # 1. Try official API
        official = self._get_official_service()
        if official:
            try:
                body = {
                    "title": title,
                    "body": {"text": {"text": text}},
                }
                created = official.notes().create(body=body).execute()
                return {
                    "id": created.get("name"),
                    "title": created.get("title"),
                    "text": text,
                    "status": "created",
                }
            except Exception as e:
                logger.debug("Official keep create failed: %s", e)

        # 2. Try gkeepapi
        gkeep = self._get_gkeep_client()
        if gkeep:
            try:
                note = gkeep.createNote(title=title, text=text)
                gkeep.sync()
                return {
                    "id": note.id,
                    "title": note.title,
                    "text": note.text,
                    "status": "created",
                }
            except Exception as e:
                logger.warning("gkeep createNote failed: %s", e)

        raise PermissionError(
            "Google Keep is not configured. Please set GOOGLE_KEEP_USERNAME and GOOGLE_KEEP_PASSWORD "
            "in .env or grant Keep scope in Google OAuth."
        )

    def append_to_note(self, note_id: str, text: str) -> Dict[str, Any]:
        """Append text to an existing note."""
        gkeep = self._get_gkeep_client()
        if gkeep:
            try:
                note = gkeep.get(note_id)
                if not note:
                    return {"error": f"Note with ID '{note_id}' not found."}
                note.text = (note.text + "\n" + text).strip()
                gkeep.sync()
                return {
                    "id": note.id,
                    "title": note.title,
                    "text": note.text,
                    "status": "updated",
                }
            except Exception as e:
                logger.warning("gkeep append failed: %s", e)

        raise PermissionError("Appending to note requires configured Google Keep integration.")


keep_service = KeepService()
