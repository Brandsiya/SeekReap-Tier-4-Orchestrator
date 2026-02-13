import os
import aiohttp
from typing import Dict, Any

class Tier3Client:
    def __init__(self):
        self.base_url = os.getenv("TIER3_BASE_URL")
        if not self.base_url:
            raise ValueError("TIER3_BASE_URL not set")

    async def execute_task(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        # Build payload correctly for Tier-3
        payload = {
            "task_id": envelope["task_id"],
            "task_type": envelope["task_type"],
            "inputs": envelope["inputs"],
            "pipeline": envelope["context"].get("pipeline"),
            "context": {k: v for k, v in envelope["context"].items() if k != "pipeline"}
        }

        print("DEBUG: Payload Tier-4 is sending to Tier-3:", payload)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/v3/verify",
                    json=payload
                ) as response:

                    text = await response.text()
                    print("DEBUG: Tier-3 response status:", response.status)
                    print("DEBUG: Tier-3 response body:", text)

                    if response.status == 200:
                        return await response.json()
                    else:
                        return {
                            "status": "failed",
                            "error": f"{response.status}: {text}"
                        }

        except Exception as e:
            print(f"[Tier-4] Tier-3 task execution failed: {str(e)}")
            return {"status": "failed", "error": str(e)}
