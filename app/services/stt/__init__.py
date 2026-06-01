"""
STT provider package.

Exposes ``make_transcriber()`` — a factory that returns the configured
speech-to-text provider based on ``settings.stt_provider``:

- ``"sarvam"``  -> :class:`app.services.stt.sarvam.SarvamTranscriber`
- ``"whisper"`` -> :class:`app.services.stt.whisper.WhisperTranscriber`
- ``"none"``    -> raises ``RuntimeError`` (transcript-only mode; audio
  ingest is not supported)

Both transcriber classes share the duck-typed interface
``async transcribe_file(*, call_id, audio_path) -> list[UtteranceSchema]``,
so callers (the ``v2t.transcribe`` worker task) are provider-agnostic.

Provider imports are lazy inside the factory so importing this package does
NOT pull in ``faster_whisper`` / ``sarvamai`` unless the corresponding
provider is actually selected.
"""

from __future__ import annotations

from app.core.config import get_settings


def make_transcriber():
    """Return the transcriber for the configured ``stt_provider``.

    Raises ``RuntimeError`` for ``"none"`` (audio ingest is unsupported in
    transcript-only mode) or an unknown provider value.
    """
    provider = get_settings().stt_provider

    if provider == "sarvam":
        from app.services.stt.sarvam import SarvamTranscriber

        return SarvamTranscriber()

    if provider == "whisper":
        from app.services.stt.whisper import WhisperTranscriber

        return WhisperTranscriber()

    if provider == "none":
        raise RuntimeError(
            "STT_PROVIDER='none' — audio transcription is disabled "
            "(transcript-only mode). Ingest pre-transcribed JSON instead."
        )

    raise RuntimeError(f"Unknown STT_PROVIDER={provider!r}.")


__all__ = ["make_transcriber"]
