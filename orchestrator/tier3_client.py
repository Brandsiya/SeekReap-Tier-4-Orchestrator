from typing import Dict
import aiohttp
import asyncio
import logging

class Tier3Client:

import os

class Tier3Client:
    def __init__(self, base_url: str = None, max_retries: int = 3):
        self.base_url = base_url or os.getenv(
            "TIER3_BASE_URL",
            "http://localhost:8000"  # fallback for local dev
        )
        self.max_retries = max_retries

    async def execute_task(self, task_payload: Dict) -> Dict:
        attempt = 0
        while attempt < self.max_retries:
            attempt += 1
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"{self.base_url}/execute", json=task_payload, timeout=10) as resp:
                        resp.raise_for_status()
                        return await resp.json()
            except Exception as e:
                logging.error(f"Tier-3 task execution failed (attempt {attempt}): {e}")
                await asyncio.sleep(2 ** attempt)  # exponential backoff
        return {
            "status": "failed",
            "message": f"All {self.max_retries} attempts failed",
            "task_id": task_payload.get("task_id"),
            "pipeline": task_payload.get("context", {}).get("pipeline")
        }
