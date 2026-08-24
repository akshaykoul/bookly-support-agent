"""
ElevenLabs text-to-speech for spoken replies.

Server-side only -- the API key must never reach the browser. Called from
POST /speak (see main.py), which the frontend hits after getting a chat
reply while the UI is in Voice mode. Speech *input* (the mic button) stays
on the browser's free Web Speech API; only output quality is worth spending
API budget on for this demo.

Returns None (rather than raising) on any failure -- missing key, quota
exceeded, network error -- so the frontend can fall back to the browser's
built-in speechSynthesis instead of breaking the chat experience. A demo
should never go down because a free-tier voice quota ran out.
"""

import os
from typing import Optional


def synthesize_speech(text: str) -> Optional[bytes]:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key or not text:
        return None

    try:
        from elevenlabs.client import ElevenLabs
        from elevenlabs.types import VoiceSettings

        client = ElevenLabs(api_key=api_key)
        voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "6mx1OKUPIqVRiAHt0nYw")
        # eleven_turbo_v2_5 over the default eleven_flash_v2_5 -- flash is
        # tuned for lowest latency at some cost to naturalness; turbo sounds
        # noticeably more human for a small latency cost that's a non-issue
        # for a support-chat reply. Override via ELEVENLABS_MODEL_ID if you
        # want flash's speed back.
        model_id = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")

        audio_chunks = client.text_to_speech.convert(
            voice_id=voice_id,
            model_id=model_id,
            text=text,
            output_format="mp3_44100_128",
            # Tuned for a conversational support-call tone rather than a flat
            # narration read: lower stability lets more natural pitch/pace
            # variation through, a touch of style keeps it expressive without
            # drifting from the voice, speaker boost keeps clarity on a
            # phone-call-style short reply.
            voice_settings=VoiceSettings(
                stability=0.45,
                similarity_boost=0.8,
                style=0.15,
                use_speaker_boost=True,
            ),
        )
        return b"".join(audio_chunks)
    except Exception as e:  # pragma: no cover - depends on external service/quota
        print(f"WARNING: ElevenLabs TTS failed, frontend will fall back to browser TTS ({e})")
        return None
