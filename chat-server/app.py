import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Optional, Dict, Tuple, AsyncGenerator
import asyncio
from starlette import status
from websockets.client import connect as ws_connect, WebSocketClientProtocol
from websockets.exceptions import WebSocketException
from fastapi import WebSocket, FastAPI, HTTPException
from starlette.websockets import WebSocketDisconnect

# 加载环境变量
from dotenv import load_dotenv

from settings import Settings
from services.llm_service import LLMService

load_dotenv()


# 常量定义
LLM_API_KEY = os.getenv("LLM_API_KEY")
# 初始化配置
settings = Settings()

# 配置日志
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout
)

class ServiceSession:
    def __init__(self):
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
                    ping_interval=settings.WS_PING_INTERVAL,
                    ping_timeout=settings.WS_PING_TIMEOUT,
                    close_timeout=settings.WS_CLOSE_TIMEOUT
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
        self.asr_ws = await self._connect_service("ASR", settings.ASR_URI)
        return True

    async def ensure_tts_connection(self) -> bool:
        """确保TTS服务连接可用"""
        if self.tts_ws and not self.tts_ws.closed:
            return True
        self.tts_ws = await self._connect_service("TTS", settings.TTS_URI)
        return True

    async def speech_to_text(self, audio_data: bytes) -> str:
        """语音转文本"""
        try:
            await self.ensure_asr_connection()
            await asyncio.wait_for(
                self.asr_ws.send(audio_data),
                timeout=settings.SERVICE_TIMEOUT
            )
            text = await asyncio.wait_for(
                self.asr_ws.recv(),
                timeout=settings.SERVICE_TIMEOUT
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
                timeout=settings.SERVICE_TIMEOUT
            )
            audio = await asyncio.wait_for(
                self.tts_ws.recv(),
                timeout=settings.SERVICE_TIMEOUT
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
        self.service_session = ServiceSession()
        self.client_id = id(websocket)
        self.last_active_time = asyncio.get_event_loop().time()  # 添加最后活动时间

    async def update_activity(self):
        """更新最后活动时间"""
        self.last_active_time = asyncio.get_event_loop().time()

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
    def __init__(self):
        self.sessions: Dict[int, ChatSession] = {}
        self.cleanup_task: Optional[asyncio.Task] = None

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
                await self.cleanup_inactive_sessions(settings.SESSION_TIMEOUT)
                await asyncio.sleep(settings.CLEANUP_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"Error in periodic cleanup: {e}")
                await asyncio.sleep(settings.CLEANUP_INTERVAL)

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


session_manager = SessionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时的操作
    logging.info("Starting up chat server...")
    try:
        # 启动清理任务
        await session_manager.start_cleanup_task()
        yield
    finally:
        # 关闭时的操作
        logging.info("Shutting down chat server...")
        # 停止清理任务
        await session_manager.stop_cleanup_task()
        # 关闭所有会话
        for session in list(session_manager.sessions.values()):
            try:
                await session_manager.remove_session(session.client_id)
            except Exception as e:
                logging.error(f"Error closing session {session.client_id}: {e}")


# 使用 lifespan 创建 FastAPI 应用
app = FastAPI(
    title="Chat Server",
    lifespan=lifespan
)


async def get_token(websocket: WebSocket) -> Tuple[str, str, str]:
    """
    从 WebSocket headers 中获取认证信息
    返回: (authorization, device_id, protocol_version)
    """
    headers = websocket.headers

    # 获取 Authorization
    authorization = headers.get("authorization")
    if not authorization:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing authorization")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )

    # 验证 Authorization 格式
    if not authorization.startswith("Bearer "):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid authorization format")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization format"
        )

    # 获取 Device-Id
    device_id = headers.get("device-id")
    if not device_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing device-id")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing device-id header"
        )

    # 获取 Protocol-Version，默认为 1.0
    protocol_version = headers.get("protocol-version", "1.0")
    # 验证协议版本
    supported_versions = ["3"]  # 支持的协议版本列表
    if protocol_version not in supported_versions:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unsupported protocol version")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported protocol version: {protocol_version}"
        )

    return authorization, device_id, protocol_version

@app.websocket("/ws/chat")
async def websocket_endpoint(websocket:WebSocket):
    chat_session = None
    try:
        # 获取并验证 headers
        authorization, device_id, protocol_version = await get_token(websocket)
        logging.info(f"New connection request - Device ID: {device_id}, Protocol Version: {protocol_version}")

        chat_session = await session_manager.create_session(websocket)
        llm_service = LLMService(
            api_key=LLM_API_KEY,
            api_url=settings.LLM_API_URL
        )

        while True:
            # 更新活动时间
            await chat_session.update_activity()

            # 接收音频数据
            try:
                audio_data = await asyncio.wait_for(
                    websocket.receive_bytes(),
                    timeout=settings.WS_RECEIVE_TIMEOUT
                )
            except asyncio.TimeoutError:
                logging.warning("Timeout waiting for audio data")
                continue
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logging.error(f"Error receiving audio data: {e}")
                raise

            try:
                # 1. 语音转文本
                text = await chat_session.service_session.speech_to_text(audio_data)
                logging.info(f"Speech to text: {text}")

                # 2. 获取LLM响应（改用阻塞式调用）
                response_text = llm_service.get_response_blocking(text, "conversation_id", "user")
                logging.info(f"LLM response: {response_text}")
                
                # 3. 将响应转换为语音
                audio_response = await chat_session.service_session.text_to_speech(response_text)
                logging.debug("Audio response generated")

                # 4. 发送响应给客户端
                await websocket.send_bytes(audio_response)
                logging.debug("Response sent to client")

            except ConnectionError as e:
                logging.error(f"Service connection error: {e}")
                raise
            except Exception as e:
                logging.error(f"Error processing message: {e}")
                raise

    except WebSocketDisconnect:
        logging.info(f"Client disconnected{' - ID: ' + str(chat_session.client_id) if chat_session else ''}")
    except ConnectionError as e:
        logging.error(f"Service connection error: {e}")
    except Exception as e:
        logging.error(f"Error in websocket connection: {e}", exc_info=True)
    finally:
        if chat_session:
            try:
                await session_manager.remove_session(chat_session.client_id)
            except Exception as e:
                logging.error(f"Error removing session {chat_session.client_id}: {e}")


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "active_sessions": len(session_manager.sessions)
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app='app:app',
                host=settings.HOST,
                port=settings.PORT
                )