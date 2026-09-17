import json
from langchain_core.tools import tool
from app.services.google.keep_service import keep_service


@tool
def list_keep_notes(query: str = "", max_results: int = 10) -> str:
    """
    Search or list notes from Google Keep.
    Args:
        query: Optional search keyword to filter notes.
        max_results: Max notes to return (default 10).
    """
    try:
        notes = keep_service.list_notes(query=query, max_results=max_results)
        if not notes:
            return f"No Google Keep notes found{' matching ' + query if query else ''}."
        return json.dumps(notes, indent=2)
    except PermissionError as e:
        return f"Google Keep Configuration Required: {e}"
    except Exception as e:
        return f"Error listing Keep notes: {e}"


@tool
def create_keep_note(title: str, text: str) -> str:
    """
    Create a new note in Google Keep.
    Args:
        title: Title of the note.
        text: Note text content, markdown, or task items.
    """
    try:
        result = keep_service.create_note(title=title, text=text)
        return f"Successfully created Google Keep note '{result.get('title')}'. ID: {result.get('id')}"
    except PermissionError as e:
        return f"Google Keep Configuration Required: {e}"
    except Exception as e:
        return f"Error creating Keep note: {e}"


@tool
def append_to_keep_note(note_id: str, text: str) -> str:
    """
    Append text or a new line to an existing Google Keep note.
    Args:
        note_id: The ID of the existing note.
        text: Content to append to the note.
    """
    try:
        result = keep_service.append_to_note(note_id=note_id, text=text)
        if "error" in result:
            return result["error"]
        return f"Successfully appended to Google Keep note '{result.get('title')}'."
    except PermissionError as e:
        return f"Google Keep Configuration Required: {e}"
    except Exception as e:
        return f"Error updating Keep note: {e}"


keep_tools = [list_keep_notes, create_keep_note, append_to_keep_note]
