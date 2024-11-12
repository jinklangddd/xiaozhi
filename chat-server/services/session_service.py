import asyncio
import logging
from typing import Dict, Optional
import uuid
import time
from services.message_handler import MessageHandler
from fastapi import WebSocket, WebSocketException
from websockets import WebSocketClientProtocol, connect as ws_connect

class ServiceSession:
    def __init__(self, settings):
        self.asr_ws: Optional[WebSocketClientProtocol] = None
        self.tts_ws: Optional[WebSocketClientProtocol] = None
        self.reconnect_attempts = settings.RECONNECT_ATTEMPTS
        self.reconnect_delay = settings.RECONNECT_DELAY

    async def _connect_service(self, service_type: str, uri: str) -> WebSocketClientProtocol:
        """通用的服务连接方法"""
        for attempt in range(self.reconnect_attempts):
            try:
                ws = await ws_connect(
                    uri,
                    ping_interval=self.settings.WS_PING_INTERVAL,
                    ping_timeout=self.settings.WS_PING_TIMEOUT,
                    close_timeout=self.settings.WS_CLOSE_TIMEOUT
                )
                logging.info(f"Connected to {service_type} service")
                return ws
            except Exception as e:
                logging.warning(f"{service_type} connection attempt {attempt + 1}/{self.reconnect_attempts} failed: {e}")
                if attempt < self.reconnect_attempts - 1:
                    await asyncio.sleep(self.reconnect_delay * (attempt + 1))
        raise ConnectionError(f"Failed to connect to {service_type} service after multiple attempts")

    async def ensure_asr_connection(self) -> bool:
        """确保ASR服务连接可用"""
        if self.asr_ws and not self.asr_ws.closed:
            return True
        self.asr_ws = await self._connect_service("ASR", self.settings.ASR_URI)
        return True

    async def ensure_tts_connection(self) -> bool:
        """确保TTS服务连接可用"""
        if self.tts_ws and not self.tts_ws.closed:
            return True
        self.tts_ws = await self._connect_service("TTS", self.settings.TTS_URI)
        return True

    async def speech_to_text(self, audio_data: bytes) -> str:
        """语音转文本"""
        try:
            await self.ensure_asr_connection()
            await asyncio.wait_for(
                self.asr_ws.send(audio_data),
                timeout=self.settings.SERVICE_TIMEOUT
            )
            text = await asyncio.wait_for(
                self.asr_ws.recv(),
                timeout=self.settings.SERVICE_TIMEOUT
            )
            return text
        except asyncio.TimeoutError as e:
            logging.error("ASR service timeout")
            self.asr_ws = None
            raise ConnectionError("ASR service timeout") from e
        except WebSocketException as e:
            logging.error(f"ASR WebSocket error: {e}")
            self.asr_ws = None
            raise ConnectionError(f"ASR service communication failed: {e}")

    async def text_to_speech(self, text: str) -> bytes:
        """文本转语音"""
        try:
            await self.ensure_tts_connection()
            await asyncio.wait_for(
                self.tts_ws.send(text),
                timeout=self.settings.SERVICE_TIMEOUT
            )
            audio = await asyncio.wait_for(
                self.tts_ws.recv(),
                timeout=self.settings.SERVICE_TIMEOUT
            )
            return audio
        except asyncio.TimeoutError as e:
            logging.error("TTS service timeout")
            self.tts_ws = None
            raise ConnectionError("TTS service timeout") from e
        except WebSocketException as e:
            logging.error(f"TTS WebSocket error: {e}")
            self.tts_ws = None
            raise ConnectionError(f"TTS service communication failed: {e}")

    async def close(self):
        try:
            if self.asr_ws:
                await self.asr_ws.close()
            if self.tts_ws:
                await self.tts_ws.close()
        except Exception as e:
            logging.error(f"Error closing service connections: {e}")


class ChatSession:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.client_id = str(uuid.uuid4())
        self.last_activity = time.time()
        self.service_session = None
        
        # 状态管理
        self.state = 'idle'
        self.response_mode = 'auto'
        self.audio_params = {
            "format": "opus",
            "sample_rate": 16000,
            "channels": 1
        }
        
        # 音频处理
        self.audio_input_enabled = True
        self.pending_audio = []
        self.vad_task = None
        
        # 消息处理器
        self.message_handler = MessageHandler(self)

    async def set_state(self, new_state: str):
        """设置会话状态"""
        self.state = new_state
        self.last_activity = time.time()
        logging.info(f"Session {self.client_id} state changed to: {new_state}")

    def pause_audio_input(self):
        """暂停音频输入"""
        self.audio_input_enabled = False

    def resume_audio_input(self):
        """恢复音频输入"""
        self.audio_input_enabled = True

    async def start_vad_detection(self):
        """启动 VAD 检测"""
        if self.response_mode == 'auto':
            self.vad_task = asyncio.create_task(self._vad_detection())

    async def process_pending_audio(self):
        """处理待处理的音频数据"""
        if self.pending_audio:
            # 处理累积的音频数据
            # 实现具体的处理逻辑
            pass

    async def _vad_detection(self):
        """VAD 检测任务"""
        # 实现 VAD 检测逻辑
        pass

    async def update_activity(self):
        """更新最后活动时间"""
        self.last_activity = time.time()


class SessionManager:
    def __init__(self, settings):
        self.sessions: Dict[int, ChatSession] = {}
        self.cleanup_task: Optional[asyncio.Task] = None
        self.settings = settings

    async def create_session(self, websocket: WebSocket) -> ChatSession:
        # 接受连接
        await websocket.accept()

        # 创建会话
        session = ChatSession(
            websocket=websocket
        )

        # 存储会话
        self.sessions[session.client_id] = session

        # 记录日志
        logging.info(
            f"New session created: client_id={session.client_id}")

        return session

    async def remove_session(self, client_id: int):
        if client_id in self.sessions:
            session = self.sessions[client_id]
            logging.info(f"Removing session: client_id={client_id}")
            try:
                await session.close()
            except Exception as e:
                logging.error(f"Error while closing session {client_id}: {e}")
            finally:
                del self.sessions[client_id]

    async def start_cleanup_task(self):
        """启动清理任务"""
        self.cleanup_task = asyncio.create_task(self.periodic_cleanup())

    async def stop_cleanup_task(self):
        """停止清理任务"""
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass

    async def periodic_cleanup(self):
        """定期清理过期会话"""
        while True:
            try:
                await self.cleanup_inactive_sessions(self.settings.SESSION_TIMEOUT)
                await asyncio.sleep(self.settings.CLEANUP_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Error in periodic cleanup: {e}")
                await asyncio.sleep(self.settings.CLEANUP_INTERVAL)

    async def cleanup_inactive_sessions(self, max_idle_time: float):
        """清理超时的会话"""
        current_time = asyncio.get_event_loop().time()
        for client_id, session in list(self.sessions.items()):  # 使用 list 创建副本
            try:
                if current_time - session.last_active_time > max_idle_time:
                    logging.info(f"Cleaning up inactive session {client_id}")
                    await self.remove_session(client_id)
            except Exception as e:
                logging.error(f"Error cleaning up session {client_id}: {e}")