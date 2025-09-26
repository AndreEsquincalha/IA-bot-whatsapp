# retriever_api.py
import os
import httpx
from typing import Dict, Any

RETRIEVER_BASE_URL = os.getenv("RETRIEVER_BASE_URL", "http://retriever-api:9020")
RETRIEVER_API_KEY  = os.getenv("RETRIEVER_API_KEY", "")
TIMEOUT_SECONDS    = float(os.getenv("RETRIEVER_TIMEOUT", "60"))

_headers = {"Content-Type": "application/json"}
if RETRIEVER_API_KEY:
    _headers["X-API-Key"] = RETRIEVER_API_KEY

def _headers_copy() -> Dict[str, str]:
    # evita mutações acidentais
    return dict(_headers)

async def call_retrieve(question: str, index: str, doc_topic: str) -> Dict[str, Any]:
    """
    Chama POST /retrieve da sua API e retorna o JSON (raise em erro).
    """
    url = f"{RETRIEVER_BASE_URL.rstrip('/')}/retrieve"
    payload = {"question": question, "index": index, "doc_topic": doc_topic}

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        resp = await client.post(url, headers=_headers_copy(), json=payload)
        text = resp.text
        if not resp.is_success:
            raise RuntimeError(f"Retriever HTTP {resp.status_code}: {text[:500]}")
        try:
            return resp.json()
        except Exception as e:
            raise RuntimeError(f"Resposta não-JSON da retriever: {text[:500]}") from e
