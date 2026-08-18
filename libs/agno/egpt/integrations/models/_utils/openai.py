import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from egpt.media import (
    Audio,
    File,
    Image,  # noqa: F401  (re-exported: the deprecated egpt.utils.openai shims star-import this namespace)
)
from egpt.utils.log import log_error, log_warning
from egpt.utils.media import (  # noqa: F401  (re-exported: provider modules import these from here)
    _process_bytes_image,
    _process_image_path,
    _process_image_url,
    images_to_message,
    process_image,
    resolve_image_mime_type,
)


def audio_to_message(audio: Sequence[Audio]) -> List[Dict[str, Any]]:
    """
    Add audio to a message for the model. By default, we use the OpenAI audio format but other Models
    can override this method to use a different audio format.

    Args:
        audio: Pre-formatted audio data like {
                    "content": encoded_string,
                    "format": "wav"
                }

    Returns:
        Message content with audio added in the format expected by the model
    """
    from urllib.parse import urlparse

    audio_messages = []
    for audio_snippet in audio:
        encoded_string: Optional[str] = None
        audio_format: Optional[str] = audio_snippet.format

        # The audio is raw data
        if audio_snippet.content:
            encoded_string = base64.b64encode(audio_snippet.content).decode("utf-8")
            if not audio_format:
                audio_format = "wav"  # Default format if not provided

        # The audio is a URL
        elif audio_snippet.url:
            audio_bytes = audio_snippet.get_content_bytes()
            if audio_bytes is not None:
                encoded_string = base64.b64encode(audio_bytes).decode("utf-8")
                if not audio_format:
                    # Try to guess format from URL extension
                    try:
                        # Parse the URL first to isolate the path
                        parsed_url = urlparse(audio_snippet.url)
                        # Get suffix from the path component only
                        audio_format = Path(parsed_url.path).suffix.lstrip(".")
                        if not audio_format:  # Handle cases like URLs ending in /
                            log_warning(
                                f"Could not determine audio format from URL path: {parsed_url.path}. Defaulting."
                            )
                            audio_format = "wav"
                    except Exception as e:
                        log_warning(
                            f"Could not determine audio format from URL: {audio_snippet.url}. Defaulting.: {e}",
                        )

                        audio_format = "wav"  # Default if guessing fails

        # The audio is a file path
        elif audio_snippet.filepath:
            path = Path(audio_snippet.filepath)
            if path.exists() and path.is_file():
                try:
                    with open(path, "rb") as audio_file:
                        encoded_string = base64.b64encode(audio_file.read()).decode("utf-8")
                    if not audio_format:
                        audio_format = path.suffix.lstrip(".")
                except Exception as e:
                    log_error(f"Failed to read audio file {path}: {str(e)}")
                    continue  # Skip this audio snippet if file reading fails
            else:
                log_error(f"Audio file not found or is not a file: {path}")
                continue  # Skip if file doesn't exist

        # Append the message if we successfully processed the audio
        if encoded_string and audio_format:
            audio_messages.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": encoded_string,
                        "format": audio_format,
                    },
                },
            )
        else:
            log_error(f"Could not process audio snippet: {audio_snippet}")

    return audio_messages


def _format_file_for_message(file: File) -> Optional[Dict[str, Any]]:
    """
    Add a document url, base64 encoded content or OpenAI file to a message.
    """
    import base64
    import mimetypes
    from pathlib import Path

    # Case 1: Document is a URL
    if file.url is not None:
        from urllib.parse import urlparse

        result = file.file_url_content
        if not result:
            log_error(f"Failed to fetch file from URL: {file.url}")
            return None
        content_bytes, mime_type = result
        name = Path(urlparse(file.url).path).name or "file"
        _mime = mime_type or file.mime_type or mimetypes.guess_type(name)[0] or "application/pdf"
        _encoded = base64.b64encode(content_bytes).decode("utf-8")
        _data_url = f"data:{_mime};base64,{_encoded}"
        return {"type": "file", "file": {"filename": name, "file_data": _data_url}}

    # Case 2: Document is a local file path
    if file.filepath is not None:
        path = Path(file.filepath)
        if not path.is_file():
            log_error(f"File not found: {path}")
            return None
        data = path.read_bytes()

        _mime = file.mime_type or mimetypes.guess_type(path.name)[0] or "application/pdf"
        _encoded = base64.b64encode(data).decode("utf-8")
        _data_url = f"data:{_mime};base64,{_encoded}"
        return {"type": "file", "file": {"filename": path.name, "file_data": _data_url}}

    # Case 3: Document is bytes content
    if file.content is not None:
        name = file.filename or "file"
        _mime = file.mime_type or mimetypes.guess_type(name)[0] or "application/pdf"
        _encoded = base64.b64encode(file.content).decode("utf-8")
        _data_url = f"data:{_mime};base64,{_encoded}"
        return {"type": "file", "file": {"filename": name, "file_data": _data_url}}

    return None
