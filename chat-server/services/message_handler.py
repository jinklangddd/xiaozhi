import logging
import json
import struct
from typing import Optional
from fastapi import WebSocket
from services.llm_service import LLMService

class MessageHandler:
    def __init__(self, chat_session):
        self.chat_session = chat_session
        self.websocket: WebSocket = chat_session.websocket

    async def handle_message(self, message, llm_service: Optional[LLMService] = None):
        """处理接收到的消息"""
        try:
            if isinstance(message, str):
                await self._handle_text_message(json.loads(message))
            elif isinstance(message, bytes):
                await self._handle_binary_message(message, llm_service)
        except Exception as e:
            logging.error(f"Error handling message: {e}", exc_info=True)
            raise

    async def _handle_text_message(self, message_json: dict):
        """处理文本消息"""
        message_type = message_json.get('type')
        
        if message_type == 'hello':
            await self._handle_hello(message_json)
        elif message_type == 'state':
            await self._handle_state(message_json)
        elif message_type == 'abort':
            await self._handle_abort()
        else:
            logging.warning(f"Unknown message type: {message_type}")

    async def _handle_hello(self, message: dict):
        """处理 hello 消息"""
        logging.info(f"Received hello message: {message}")
        self.chat_session.response_mode = message.get('response_mode', 'auto')
        self.chat_session.audio_params.update(message.get('audio_params', {}))
        logging.info(f"Session {self.chat_session.client_id} initialized with response_mode: {self.chat_session.response_mode}, audio_params: {self.chat_session.audio_params}")

    async def _handle_state(self, message: dict):
        """处理 state 消息"""
        new_state = message.get('state')
        logging.info(f"Received state message: {new_state}")
        
        if new_state == 'idle':
            await self.chat_session.set_state('idle')
            if self.chat_session.response_mode == 'manual':
                await self.chat_session.process_pending_audio()
                
        elif new_state == 'wake_word_detected':
            await self.chat_session.set_state('wake_word_detected')
            await self.websocket.send_json({
                "type": "state_ack",
                "state": "wake_word_detected"
            })
            
        elif new_state == 'listening':
            await self.chat_session.set_state('listening')
            if self.chat_session.response_mode == 'auto':
                await self.chat_session.start_vad_detection()
            
        elif new_state == 'speaking':
            await self.chat_session.set_state('speaking')
            if self.chat_session.response_mode != 'real_time':
                self.chat_session.pause_audio_input()

    async def _handle_abort(self):
        """处理 abort 消息"""
        logging.info("Received abort message")
        if self.chat_session.vad_task:
            self.chat_session.vad_task.cancel()
        self.chat_session.pending_audio.clear()
        await self.chat_session.set_state('idle')
        await self.websocket.send_json({
            "type": "tts",
            "state": "stop"
        })

    async def _handle_binary_message(self, audio_data: bytes, llm_service: LLMService):
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
        text = await self.chat_session.service_session.speech_to_text(payload)
        
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
            "sample_rate": self.chat_session.audio_params["sample_rate"]
        })
        
        # 发送句子开始消息
        await self.websocket.send_json({
            "type": "tts",
            "state": "sentence_start",
            "text": text
        })
        
        # 转换为语音并发送
        audio_response = await self.chat_session.service_session.text_to_speech(text)
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