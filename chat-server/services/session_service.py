import asyncio
import logging
from typing import Dict, Optional
import uuid
import time
import json
import struct

from fastapi import WebSocket, WebSocketException
from websockets import WebSocketClientProtocol, connect as ws_connect
from services.llm_service import LLMService

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
        

    async def handle_hello(self, message: dict):
        """处理 hello 消息"""
        self.response_mode = message.get('response_mode', 'auto')
        self.audio_params.update(message.get('audio_params', {}))
        logging.info(f"Session {self.client_id} initialized with response_mode: {self.response_mode}, audio_params: {self.audio_params}")

    async def handle_state(self, message: dict):
        """处理 state 消息"""
        new_state = message.get('state')
        logging.info(f"Received state message: {new_state}")
        
        if new_state == 'idle':
            await self.set_state('idle')
         
                
        elif new_state == 'wake_word_detected':
            await self.set_state('wake_word_detected')
           
            
        elif new_state == 'listening':
            await self.set_state('listening')
          
            
        elif new_state == 'speaking':
            await self.set_state('speaking')
        

    async def handle_abort(self):
        """处理 abort 消息"""
        if self.vad_task:
            self.vad_task.cancel()
        self.pending_audio.clear()
        await self.set_state('idle')
        await self.websocket.send_json({
            "type": "tts",
            "state": "stop"
        })

    async def handle_binary_message(self, audio_data: bytes, llm_service: LLMService):
        """处理二进制消息"""
        if len(audio_data) < 4:
            logging.error("Invalid protocol data: too short")
            return
        
        # 解析协议头部
        header = audio_data[:4]
        msg_type, reserved, payload_size = struct.unpack('>BBH', header)

        # 验证数据完整性
        if len(audio_data) != payload_size + 4:
            logging.error(f"Invalid data length. Expected {payload_size + 4}, got {len(audio_data)}")
            return

        payload = audio_data[4:]

        if msg_type == 0:  # 音频流数据
            await self._handle_audio_data(payload, llm_service)
        elif msg_type == 1:  # JSON数据
            await self._handle_json_payload(payload)
        else:
            logging.error(f"Unknown message type: {msg_type}")

    async def _handle_audio_data(self, payload: bytes, llm_service: LLMService):
        """处理音频数据"""
        if len(payload) == 0:  # 句子边界标记
            return
            
        # 处理音频数据
        text = await self.service_session.speech_to_text(payload)
        
        # 发送 STT 结果
        await self.websocket.send_json({
            "type": "stt",
            "text": text
        })
        
        # 获取 LLM 响应
        response_text = llm_service.get_response_blocking(text, "conversation_id", "user")
        logging.info(f"LLM response: {response_text}")
        
        await self._send_tts_response(response_text)

    async def _handle_json_payload(self, payload: bytes):
        """处理 JSON 负载"""
        try:
            json_data = json.loads(payload)
            logging.info(f"Received JSON payload: {json_data}")
        except json.JSONDecodeError:
            logging.error("Invalid JSON payload")

    async def _send_tts_response(self, text: str):
        """发送 TTS 响应"""
        # 发送 TTS 开始消息
        await self.websocket.send_json({
            "type": "tts",
            "state": "start",
            "sample_rate": self.audio_params["sample_rate"]
        })
        
        # 发送句子开始消息
        await self.websocket.send_json({
            "type": "tts",
            "state": "sentence_start",
            "text": text
        })
        
        # 转换为语音并发送
        audio_response = await self.service_session.text_to_speech(text)
        await self.websocket.send_bytes(audio_response)
        
        # 发送句子结束和 TTS 结束消息
        await self.websocket.send_json({
            "type": "tts",
            "state": "sentence_end"
        })
        await self.websocket.send_json({
            "type": "tts",
            "state": "stop"
        })

    async def set_state(self, new_state: str):
        """设置会话状态"""
        self.state = new_state
        self.last_activity = time.time()
        logging.info(f"Session {self.client_id} state changed to: {new_state}")


    async def update_activity(self):
        """更新最后活动时间"""
        self.last_activity = time.time()

    async def close(self):
        """关闭会话相关的连接"""
        try:
            await self.service_session.close()
        except Exception as e:
            logging.error(f"Error closing service session: {e}")

        try:
            if not self.websocket.client_state.DISCONNECTED:
                await self.websocket.close()
        except Exception as e:
            logging.error(f"Error closing websocket: {e}")


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