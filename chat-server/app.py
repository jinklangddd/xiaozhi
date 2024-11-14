import logging
import os
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
async def websocket_endpoint(websocket: WebSocket):
    chat_session = None

    try:
        # 获取并验证 headers
        authorization, device_id, protocol_version = await get_token(websocket)
        logging.info(f"New connection request - authorization:{authorization}, Device ID: {device_id}, Protocol Version: {protocol_version}")

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
                
                # 使用消息处理器处理消息
                await chat_session.message_handler.handle_message(message, llm_service)

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