import asyncio
import websockets
import logging
import sys
from typing import Optional
import wave
import argparse

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

class TestClient:
    def __init__(self, server_uri: str, audio_file: str):
        self.server_uri = server_uri
        self.audio_file = audio_file
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        
    async def connect(self):
        """连接到服务器"""
        try:
            self.ws = await websockets.connect(
                self.server_uri,
                extra_headers={
                    "Authorization": "Bearer test_token",
                    "Device-Id": "test_device",
                    "Protocol-Version": "3"
                }
            )
            logging.info("Connected to server")
            return True
        except Exception as e:
            logging.error(f"Connection failed: {e}")
            return False

    def read_audio_file(self) -> bytes:
        """读取音频文件"""
        try:
            with wave.open(self.audio_file, 'rb') as wav_file:
                return wav_file.readframes(wav_file.getnframes())
        except Exception as e:
            logging.error(f"Error reading audio file: {e}")
            raise

    async def send_audio(self, audio_data: bytes):
        """发送音频数据"""
        try:
            await self.ws.send(audio_data)
            logging.info("Audio data sent")
        except Exception as e:
            logging.error(f"Error sending audio: {e}")
            raise

    async def receive_response(self) -> bytes:
        """接收响应"""
        try:
            response = await self.ws.recv()
            logging.info("Response received")
            return response
        except Exception as e:
            logging.error(f"Error receiving response: {e}")
            raise

    def save_response(self, audio_data: bytes, output_file: str):
        """保存响应音频"""
        try:
            with wave.open(output_file, 'wb') as wav_file:
                wav_file.setnchannels(1)  # 单声道
                wav_file.setsampwidth(2)  # 16位
                wav_file.setframerate(16000)  # 16kHz
                wav_file.writeframes(audio_data)
            logging.info(f"Response saved to {output_file}")
        except Exception as e:
            logging.error(f"Error saving response: {e}")
            raise

    async def close(self):
        """关闭连接"""
        if self.ws:
            await self.ws.close()
            logging.info("Connection closed")

    async def run_test(self, output_file: str = "response.wav"):
        """运行测试"""
        try:
            # 连接服务器
            if not await self.connect():
                return

            # 读取音频文件
            audio_data = self.read_audio_file()
            
            # 发送音频数据
            await self.send_audio(audio_data)
            
            # 接收响应
            response = await self.receive_response()
            
            # 保存响应
            self.save_response(response, output_file)
            
        except Exception as e:
            logging.error(f"Test failed: {e}")
        finally:
            await self.close()

async def main():
    parser = argparse.ArgumentParser(description='WebSocket Chat Client Test')
    parser.add_argument('--server', default='ws://127.0.0.1:8000/ws/chat',
                      help='Server URI (default: ws://127.0.0.1:8000/ws/chat)')
    parser.add_argument('--input', required=True,
                      help='Input audio file path (WAV format)')
    parser.add_argument('--output', default='response.wav',
                      help='Output audio file path (default: response.wav)')
    
    args = parser.parse_args()
    
    client = TestClient(args.server, args.input)
    await client.run_test(args.output)

if __name__ == "__main__":
    asyncio.run(main()) 