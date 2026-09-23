"""
Agent

High-level API for managing Agora Conversational AI Agents.
"""
import logging
import os
import time
from typing import Any, Dict, Optional

from agora_agent import Area, AsyncAgora
from agora_agent.agentkit import Agent as AgoraAgent
from agora_agent.agentkit.preview import GeminiSTT, GeminiTTS
from agora_agent.agentkit.vendors import Gemini

logger = logging.getLogger("uvicorn.error")

AGENT_PROMPT = """You are Gemini, an agentic developer advocate from Agora. You help developers understand and build with Agora's Conversational AI platform.

Agora is a real-time communications company. The product you represent is the Agora Conversational AI Engine.

Your runtime setup: Agora orchestrates a cascading Gemini ASR -> Gemini LLM -> Gemini TTS voice pipeline. Gemini ASR transcribes the user's speech; you are the Gemini language model generating replies; Gemini TTS synthesizes them, and Agora delivers audio over RTC. This is not OpenAI or a Gemini Live native-audio session. Describe this setup accurately when asked, but do not recite it in every response. Do not claim access to raw audio, cameras, tools, or capabilities that this demo has not provided.

For natural spoken delivery, you may sparingly include <laugh>, <breath>, <sigh>, or <short pause> in your reply when appropriate. These are performance directions for TTS, not words to explain to the user. Most replies need no cue; never add a cue to every sentence. Use <breath> and <short pause> only between complete sentences during a reply, never at the beginning or end. Start with spoken words unless opening laughter is appropriate; <laugh> may open a reply when it fits naturally.

If you do not know a specific fact about Agora, say so plainly and suggest checking docs.agora.io. Keep most replies to one or two sentences unless the user explicitly asks for more detail.
"""


class Agent:
    """
    High-level wrapper for Agora Conversational AI Agent operations.
    
    Uses AgentSession for full lifecycle management (start/stop),
    which handles Token007 authentication automatically.
    """
    
    def __init__(self):
        self.app_id = os.getenv("AGORA_APP_ID")
        self.app_certificate = os.getenv("AGORA_APP_CERTIFICATE")
        self.greeting = os.getenv(
            "AGENT_GREETING",
            "Hi there! I'm Gemini, your virtual assistant from Agora. How can I help?",
        )

        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        if not self.google_api_key:
            raise ValueError("GOOGLE_API_KEY is required for GeminiSTT")

        if not self.app_id or not self.app_certificate:
            raise ValueError("AGORA_APP_ID and AGORA_APP_CERTIFICATE are required")

        self.client = AsyncAgora(
            area=Area.US,
            app_id=self.app_id,
            app_certificate=self.app_certificate,
        )

        # Track active sessions by agent_id
        self._sessions: Dict[str, Any] = {}

    async def start(
        self,
        channel_name: str,
        agent_uid: int,
        user_uid: int,
        output_audio_codec: Optional[str] = None,
        tts_voice: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Start agent with the same default vendor chain as the Next.js quickstart."""
        if not channel_name or not str(channel_name).strip():
            raise ValueError("channel_name is required and cannot be empty")
        if agent_uid <= 0:
            raise ValueError("agent_uid is required and cannot be empty")
        if user_uid <= 0:
            raise ValueError("user_uid is required and cannot be empty")

        if tts_voice is not None and (not tts_voice.strip() or len(tts_voice) > 64):
            raise ValueError("ttsVoice must be a nonempty string of at most 64 characters")

        voice = tts_voice.strip() if tts_voice is not None else (os.getenv("GEMINI_TTS_VOICE") or "Puck")
        tts_model = os.getenv("GEMINI_TTS_MODEL") or "gemini-3.8-flash-tts"
        instructions = f"{AGENT_PROMPT}\nCurrent session: LLM model gemini-3.6-flash; TTS model {tts_model}; TTS voice {voice}."

        llm = Gemini(
            api_key=self.google_api_key,
            model="gemini-3.6-flash",
            system_messages=[{"parts": [{"text": instructions}], "role": "user"}],
            greeting_message=self.greeting,
            failure_message="Please wait a moment.",
            max_history=15,
        )
        stt = GeminiSTT(
            api_key=self.google_api_key,
            language_codes=["en-US"],
            custom_vocabulary=["Agora", "Gemini"],
            word_timestamp=False,
        )
        tts = GeminiTTS(
            api_key=self.google_api_key,
            model=tts_model,
            voice=voice,
            style=os.getenv("GEMINI_TTS_STYLE", "warm and reassuring"),
        )

        parameters = {
            "audio_scenario": "chorus",
            "data_channel": "rtm",
            "enable_error_message": True,
            "enable_metrics": True,
        }
        if isinstance(output_audio_codec, str) and output_audio_codec.strip():
            parameters["output_audio_codec"] = output_audio_codec.strip()

        agora_agent = AgoraAgent(
            client=self.client,
            instructions=instructions,
            greeting=self.greeting,
            failure_message="Please wait a moment.",
            turn_detection={
                "language": "en-US",
                "config": {
                    "speech_threshold": 0.5,
                    "start_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "interrupt_duration_ms": 160,
                            "prefix_padding_ms": 300,
                        },
                    },
                    "end_of_speech": {
                        "mode": "vad",
                        "vad_config": {
                            "silence_duration_ms": 480,
                        },
                    },
                },
            },
            advanced_features={"enable_rtm": True, "enable_tools": True},
            parameters=parameters,
        )
        
        agora_agent = agora_agent.with_stt(stt).with_llm(llm).with_tts(tts)

        session = agora_agent.create_async_session(
            channel=channel_name,
            agent_uid=str(agent_uid),
            remote_uids=[str(user_uid)],
            enable_string_uid=False,
            idle_timeout=30,
            expires_in=3600,
        )

        logger.info(
            "Starting Agora agent channel=%s agent_uid=%s user_uid=%s",
            channel_name,
            agent_uid,
            user_uid,
        )

        try:
            agent_id = await session.start()
        except Exception:
            logger.exception(
                "Failed to start Agora agent channel=%s agent_uid=%s user_uid=%s",
                channel_name,
                agent_uid,
                user_uid,
            )
            raise

        # Save session for later stop
        self._sessions[agent_id] = session

        logger.info(
            "Started Agora agent agent_id=%s channel=%s agent_uid=%s user_uid=%s",
            agent_id,
            channel_name,
            agent_uid,
            user_uid,
        )
        
        return {
            "agent_id": agent_id,
            "channel_name": channel_name,
            "status": "started",
        }

    async def stop(self, agent_id: str) -> None:
        """Stop a running agent through its retained session."""
        if not agent_id or not str(agent_id).strip():
            raise ValueError("agent_id is required and cannot be empty")

        session = self._sessions.pop(agent_id, None)
        if session:
            await session.stop()
            logger.info("Stopped Agora agent from active session agent_id=%s", agent_id)
