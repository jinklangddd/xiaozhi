import json
import logging
import os
import struct
import sys
from contextlib import asynccontextmanager
from typing import Tuple
import asyncio
from starlette import status
from fastapi import WebSocket, FastAPI, HTTPException
from starlette.websockets import WebSocketDisconnect

# 加载环境变量
from dotenv import load_dotenv

from settings import Settings
from services.llm_service import LLMService
from services.session_service import SessionManager

load_dotenv()


# 常量定义
LLM_API_KEY = os.getenv("LLM_API_KEY")
# 初始化配置
settings = Settings()
session_manager = SessionManager(settings)

# 配置日志
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout
)


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
    current_state = "idle"
    response_mode = "auto"
    audio_params = {
        "format": "opus",
        "sample_rate": 16000,
        "channels": 1
    }

    try:
        # 获取并验证 headers
        authorization, device_id, protocol_version = await get_token(websocket)
        logging.info(f"New connection request - Authorization: {authorization}, Device ID: {device_id}, Protocol Version: {protocol_version}")

        chat_session = await session_manager.create_session(websocket)
        llm_service = LLMService(
            api_key=LLM_API_KEY,
            api_url=settings.LLM_API_URL
        )

        while True:
            # 更新活动时间
            await chat_session.update_activity()

            try:
                # 统一接收消息
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=settings.WS_RECEIVE_TIMEOUT
                )
                
                # 处理文本消息
                if isinstance(message, str):
                    message_json = json.loads(message)
                    if message_json['type'] == 'hello':
                        # 处理 hello
                        logging.info(f"Received hello message: {message_json}")
                        response_mode = message_json.get('response_mode')
                        audio_params = message_json.get('audio_params')
                       
                    elif message_json['type'] == 'state':
                        # 处理 state  unknown,idle,connecting, listening, speaking, wake_word_detected, testing, upgrading, invalid_state
                        state = message_json.get('state')
                        logging.info(f"Received state message: {state}")

                        if state == 'idle':
                            pass

                        elif state == 'listening':
                            # 正在监听用户输入
                            pass
                        elif state == 'speaking':
                            # 正在播放语音
                            pass
                        elif state == 'wake_word_detected':
                            # 检测到唤醒词,开始应答
                            pass
                        elif state == 'testing':
                            # 测试状态
                            pass
                        elif state == 'upgrading':
                            # 升级状态
                            pass
                        else:
                            logging.error(f"未知的状态: {state}")

                        # 更新当前状态
                        current_state = state

                    elif message_json['type'] == 'abort':
                         # 处理 abort
                        logging.info(f"Received abort message: {message_json}")
                        await chat_session.close()
                       
                        
                # 处理二进制消息
                elif isinstance(message, bytes):
                    audio_data = message
                    if len(audio_data) < 4:
                        logging.error("Invalid protocol data: too short")
                        continue
                    
                    # 解析协议头部
                    header = audio_data[:4]
                    msg_type, reserved, payload_size = struct.unpack('>BBH', header)

                    # 验证数据完整性
                    if len(audio_data) != payload_size + 4:
                        logging.error(f"Invalid data length. Expected {payload_size + 4}, got {len(audio_data)}")
                        continue

                    # 获取负载数据
                    payload = audio_data[4:]

                    # 根据消息类型处理数据
                    if msg_type == 0:  # 音频流数据
                        # 1. 语音转文本
                        text = await chat_session.service_session.speech_to_text(payload)
                        
                        # 2. 获取LLM响应
                        response_text = llm_service.get_response_blocking(text, "conversation_id", "user")
                        logging.info(f"LLM response: {response_text}")
                        
                        # 3. 将响应转换为语音
                        audio_response = await chat_session.service_session.text_to_speech(response_text)
                        logging.debug("Audio response generated")

                        # 4. 发送响应给客户端
                        await websocket.send_bytes(audio_response)
                        logging.debug("Response sent to client")
                        
                    elif msg_type == 1:  # JSON数据
                        try:
                            json_payload = json.loads(payload)
                            logging.info(f"Received JSON payload: {json_payload}")
                        except json.JSONDecodeError:
                            logging.error("Invalid JSON payload")
                            continue
                    else:
                        logging.error(f"Unknown message type: {msg_type}")
                        continue

            except asyncio.TimeoutError:
                logging.warning("Timeout waiting for data")
                continue

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