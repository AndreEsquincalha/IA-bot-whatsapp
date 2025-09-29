import requests
import httpx

from config import (
    EVOLUTION_API_URL,
    EVOLUTION_INSTANCE_NAME,
    EVOLUTION_AUTHENTICATION_API_KEY,
)

async def send_whatsapp_message(number, text):
    url = f'{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE_NAME}'

    headers = {
        'apikey': EVOLUTION_AUTHENTICATION_API_KEY,
        'Content-Type':'application/json',
    }

    payload = {
        'number': number,
        'text': text,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code >= 400:
            print(f"[EVOLUTION_API] Erro {resp.status_code}: {resp.text}")

    # requests.post(
    #     url=url,
    #     json=payload,
    #     headers=headers,
    # )