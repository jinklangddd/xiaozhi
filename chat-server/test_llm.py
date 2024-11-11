# 使用绝对导    
import json
import os
from dotenv import load_dotenv
import time  # 添加在文件顶部

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

    conversation_id = ""
    while True:
        user_input = input("请输入问题 (输入 'q' 退出): ")
        if user_input.lower() == 'q':
            break
            
        start_time = time.time()
        response = llm_service.get_response_blocking({"assistant_name": "小明"}, user_input, conversation_id, "justin")
        response = json.loads(response)
        end_time = time.time()
        
        print("AI回答:", response['answer'])
        print(f"执行时间: {end_time - start_time:.2f} 秒")

if __name__ == "__main__":
    import asyncio
    #asyncio.run(test())
    test_blocking()

