# 使用绝对导    
from app import LLMService

llm_service = LLMService()

async def test():
    async for response_chunk in llm_service.get_response("你好", "", "justin"):
        print(response_chunk)

def test_blocking():
    response = llm_service.get_response_blocking("你知道我叫什么名字吗", "71e8acdf-360f-4a57-bc05-49d5f77b11fd", "justin")
    print(response)

if __name__ == "__main__":
    import asyncio
    #asyncio.run(test())
    test_blocking()

