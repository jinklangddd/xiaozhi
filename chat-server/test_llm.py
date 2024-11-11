# 使用绝对导    
import json
import os
from dotenv import load_dotenv

from settings import Settings
from services.llm_service import LLMService

load_dotenv()


# 常量定义
LLM_API_KEY = os.getenv("LLM_API_KEY")
# 初始化配置
settings = Settings()


llm_service = LLMService(
            api_key=LLM_API_KEY,
            api_url=settings.LLM_API_URL
        )

async def test():
    async for response_chunk in llm_service.get_response({"assistant_name": "小明"}, "你好", "", "justin"):
        print(response_chunk)

def test_blocking():
    response = llm_service.get_response_blocking({"assistant_name": "小明"}, "你知道我叫什么名字吗", "", "justin")
    
    response = json.loads(response)
    print(response['answer'])

if __name__ == "__main__":
    import asyncio
    #asyncio.run(test())
    test_blocking()

