# app.py
from fastapi import FastAPI, Request
from typing import Optional
import redis.asyncio as redis
import unicodedata

from message_buffer import buffer_message
from evolution_api import send_whatsapp_message
from config import REDIS_URL
from topics import TOPICS_BY_ID, MENU_TEXT  # << novo

app = FastAPI()
rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# ============ Config ============
STATE_TTL_SECONDS = 20 * 60  # 20 minutos

# ============ Estado (Redis) ============
def _key_state(chat_id: str) -> str:
    return f"iaires:state:{chat_id}"

async def get_user_state(chat_id: str) -> dict:
    h = await rds.hgetall(_key_state(chat_id))
    if not h:
        return {"awaiting_topic": False, "topic": None, "header_pending": False}
    return {
        "awaiting_topic": h.get("awaiting_topic") == "1",
        "topic": h.get("topic") or None,
        "header_pending": h.get("header_pending") == "1",
    }

async def set_user_state(
    chat_id: str,
    *,
    awaiting_topic: Optional[bool] = None,
    topic: Optional[Optional[str]] = None,
    header_pending: Optional[bool] = None,
):
    key = _key_state(chat_id)
    current = await get_user_state(chat_id)
    new_state = {
        "awaiting_topic": "1" if (awaiting_topic if awaiting_topic is not None else current["awaiting_topic"]) else "0",
        "topic": (topic if topic is not None else current["topic"]) or "",
        "header_pending": "1" if (header_pending if header_pending is not None else current["header_pending"]) else "0",
    }
    await rds.hset(key, mapping=new_state)
    await rds.expire(key, STATE_TTL_SECONDS)  # renova TTL a cada atualização

async def bump_state_ttl(chat_id: str):
    await rds.expire(_key_state(chat_id), STATE_TTL_SECONDS)

async def clear_user_state(chat_id: str):
    await rds.delete(_key_state(chat_id))

# ============ Utils ============
def extract_text_message(payload: dict) -> Optional[str]:
    msg = payload.get("data", {}).get("message", {}) or {}
    if "conversation" in msg:
        return msg.get("conversation")
    if "extendedTextMessage" in msg:
        return msg["extendedTextMessage"].get("text")
    if "imageMessage" in msg:
        return msg["imageMessage"].get("caption")
    if "videoMessage" in msg:
        return msg["videoMessage"].get("caption")
    return None

def normalize(s: str) -> str:
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if not unicodedata.combining(c))

def is_change_topic_command(text: str) -> bool:
    t = normalize(text)
    if "menu" in t:
        return True
    triggers = ("trocar", "mudar", "alterar", "reset", "reiniciar", "outro", "novo")
    scope = ("topico", "assunto")
    return any(x in t for x in triggers) and any(y in t for y in scope)

# ======== NOVO: usa TOPICS_BY_ID e MENU_TEXT centralizados ========
def option_label(num: int) -> str:
    return TOPICS_BY_ID.get(num, "Alternativa Inválida")

async def send_menu(chat_id: str):
    send_whatsapp_message(number=chat_id, text=MENU_TEXT)
    await set_user_state(chat_id, awaiting_topic=True, topic=None, header_pending=False)

# ============ Webhook ============
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    chat_id = data.get("data", {}).get("key", {}).get("remoteJid")
    if not chat_id or "@g.us" in chat_id:
        return {"status": "ignored"}

    text = extract_text_message(data)
    if not text:
        send_whatsapp_message(number=chat_id, text="Não entendi. Envie uma mensagem de texto ou digite 'menu'.")
        return {"status": "no_text"}

    text = text.strip()

    # Troca de tópico instantânea
    if is_change_topic_command(text):
        await send_menu(chat_id)
        return {"status": "menu_by_user_request"}

    state = await get_user_state(chat_id)

    # Primeira interação (ou estado expirado > 20min): pede menu
    if not state["awaiting_topic"] and not state["topic"]:
        await send_menu(chat_id)
        return {"status": "menu_first_time_or_expired"}

    # Aguardando escolha do tópico
    if state["awaiting_topic"]:
        if text.isdigit():
            opt = int(text)
            label = option_label(opt)
            if label == "Alternativa Inválida":
                send_whatsapp_message(
                    number=chat_id,
                    text="Opção inválida. Escolha um número válido.\n\n" + MENU_TEXT  # << aqui também
                )
                return {"status": "invalid_option"}
            # Define tópico e marca cabeçalho para a próxima mensagem
            await set_user_state(chat_id, awaiting_topic=False, topic=label, header_pending=True)
            send_whatsapp_message(number=chat_id, text=f"Tópico definido: *{label}*.")
            send_whatsapp_message(number=chat_id, text="Olá, eu sou IAires, uma IA de suporte.\nIrei te auxiliar referente ao tópico escolhido.\nNo que posso ajudar?")
            return {"status": "topic_selected", "topic": label}
        else:
            send_whatsapp_message(number=chat_id, text="Responda só com o número do tópico.")
            return {"status": "awaiting_numeric_option"}

    # Já temos um tópico escolhido → conversa livre
    await bump_state_ttl(chat_id)
    topic = state["topic"] or "Tópico"

    # Cabeçalho de contexto só na 1ª mensagem após a escolha
    if state["header_pending"]:
        header = f"[TÓPICO: {topic}] "
        await buffer_message(chat_id=chat_id, message=header)
        await set_user_state(chat_id, header_pending=False)

    # Encaminha fala do usuário para a IA (buffer/debounce)
    await buffer_message(chat_id=chat_id, message=text)

    return {"status": "ok", "topic": topic}
