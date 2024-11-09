from typing import Dict, Any

from pydantic.v1 import BaseSettings, Field


class Settings(BaseSettings):
    # 服务配置
    ASR_URI: str = Field(default="ws://localhost:8001", description="ASR service websocket URI")
    TTS_URI: str = Field(default="ws://localhost:8002", description="TTS service websocket URI")
    LLM_API_URL: str = Field(default="http://localhost:8003", description="LLM API URL")

    # 服务器配置
    HOST: str = Field(default="0.0.0.0", description="Server host")
    PORT: int = Field(default=8000, description="Server port")

    # 重连配置
    RECONNECT_ATTEMPTS: int = Field(default=3, description="Number of reconnection attempts")
    RECONNECT_DELAY: float = Field(default=1.0, description="Initial delay between reconnection attempts in seconds")

    # WebSocket 配置
    WS_PING_INTERVAL: float = Field(default=20.0, description="WebSocket ping interval in seconds")
    WS_PING_TIMEOUT: float = Field(default=20.0, description="WebSocket ping timeout in seconds")
    WS_CLOSE_TIMEOUT: float = Field(default=10.0, description="WebSocket close timeout in seconds")

    # 请示超时
    REQUEST_TIMEOUT: float = Field(default=30.0, description="llm http request timeout in seconds")

    # 会话管理配置
    SESSION_TIMEOUT: float = Field(
        default=1800.0,  # 30分钟超时
        description="Session timeout in seconds"
    )
    CLEANUP_INTERVAL: float = Field(
        default=300.0,  # 5分钟清理一次
        description="Interval between cleanup runs in seconds"
    )

    # 日志配置
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        case_sensitive = True

        # 添加配置说明
        @staticmethod
        def schema_extra(schema: Dict[str, Any]) -> None:
            for prop in schema.get("properties", {}).values():
                prop.pop("title", None)