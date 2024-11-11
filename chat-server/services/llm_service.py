import json
import logging
from typing import AsyncGenerator, Tuple

import aiohttp
import requests

class LLMService:
    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_url = api_url

    def _prepare_request(self, inputs: dict, query: str, conversation_id: str, user: str, response_mode: str = "blocking") -> Tuple[str, dict, dict]:
        """准备 LLM 请求的通用参数"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }
        data = {
            "inputs": inputs,
            "query": query,
            "response_mode": response_mode,
            "conversation_id": conversation_id,
            "user": user
        }
        return self.api_url, headers, data

    def get_response_blocking(self, inputs: dict, query: str, conversation_id: str, user: str) -> str:
        """获取 LLM 的阻塞式响应"""
        url, headers, data = self._prepare_request(inputs, query, conversation_id, user)
        
        try:
            response = requests.post(url, headers=headers, json=data)
            if response.status_code != 200:
                logging.error(f"LLM API error: {response.status_code}, {response.text}")
                raise ConnectionError(f"LLM service error: {response.status_code}")
            
            response_text = response.text
            logging.info(f"LLM response received for conversation {conversation_id}")
            return response_text
            
        except requests.RequestException as e:
            logging.error(f"LLM API request failed: {e}")
            raise ConnectionError(f"LLM service connection failed: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in LLM service: {e}")
            raise 


    async def get_response(self, inputs: dict, query: str, conversation_id: str, user: str) -> AsyncGenerator[str, None]:
        """获取 LLM 的流式响应"""
        url, headers, data = self._prepare_request(inputs, query, conversation_id, user, "streaming")
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logging.error(f"LLM API error: {response.status}, {error_text}")
                        raise ConnectionError(f"LLM service error: {response.status}")

                    async for line in response.content:
                        if line:
                            try:
                                line_text = line.decode('unicode_escape').strip()
                                if line_text:
                                    json_data = json.loads(line_text)
                                    if 'answer' in json_data:
                                        yield json_data['answer']
                            except json.JSONDecodeError as e:
                                logging.error(f"JSON decode error: {e}")
                                continue
                            except Exception as e:
                                logging.error(f"Error processing response line: {e}")
                                continue

            except aiohttp.ClientError as e:
                logging.error(f"LLM API request failed: {e}")
                raise ConnectionError(f"LLM service connection failed: {e}")
            except Exception as e:
                logging.error(f"Unexpected error in LLM service: {e}")
                raise        