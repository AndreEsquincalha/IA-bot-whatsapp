# app.py
from fastapi import FastAPI, Request
from typing import Optional
import redis.asyncio as redis
import unicodedata

from message_buffer import buffer_message
from evolution_api import send_whatsapp_message  # deve ser async
from config import REDIS_URL

from acl_config import (
    user_allowed,
    allowed_projects,
    build_project_menu_for,
    build_topics_menu,
    TOPICS_BY_ID,
    PROJECTS_BY_ID,
    project_index,
    project_skip_topics
)

app = FastAPI()
rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# ============ Config ============
STATE_TTL_SECONDS = 20 * 60  # 20 minutos

# ============ Estado (Redis) ============
def _key_state(chat_id: str) -> str:
    return f"iaires:state:{chat_id}"

async def get_user_state(chat_id: str) -> dict:
    """
    Estado da conversa por chat_id (remoteJid):
      - awaiting_project: aguardando escolha do projeto (fase 1)
      - project_id / project_index: projeto escolhido
      - awaiting_topic: aguardando escolha do tópico (fase 2)
      - topic: label do tópico escolhido
      - header_pending: sinaliza que devemos empurrar o cabeçalho na próxima mensagem do usuário
    """
    h = await rds.hgetall(_key_state(chat_id))
    if not h:
        return {
            "awaiting_project": True,
            "project_id": None,
            "project_index": None,
            "awaiting_topic": False,
            "topic": None,
            "header_pending": False,
        }
    return {
        "awaiting_project": h.get("awaiting_project") == "1",
        "project_id": int(h["project_id"]) if h.get("project_id") else None,
        "project_index": h.get("project_index") or None,
        "awaiting_topic": h.get("awaiting_topic") == "1",
        "topic": h.get("topic") or None,
        "header_pending": h.get("header_pending") == "1",
    }

async def set_user_state(
    chat_id: str,
    *,
    awaiting_project: Optional[bool] = None,
    project_id: Optional[Optional[int]] = None,
    project_index: Optional[Optional[str]] = None,
    awaiting_topic: Optional[bool] = None,
    topic: Optional[Optional[str]] = None,
    header_pending: Optional[bool] = None,
):
    key = _key_state(chat_id)
    current = await get_user_state(chat_id)
    new_state = {
        "awaiting_project": "1" if (awaiting_project if awaiting_project is not None else current["awaiting_project"]) else "0",
        "project_id": str(project_id if project_id is not None else (current["project_id"] or "")) if (project_id is not None or current["project_id"] is not None) else "",
        "project_index": (project_index if project_index is not None else current["project_index"]) or "",
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

async def reset_to_projects(chat_id: str):
    await set_user_state(
        chat_id,
        awaiting_project=True,
        project_id=None,
        project_index=None,
        awaiting_topic=False,
        topic=None,
        header_pending=False,
    )

async def reset_to_topics(chat_id: str):
    st = await get_user_state(chat_id)
    await set_user_state(
        chat_id,
        awaiting_project=False,
        project_id=st["project_id"],
        project_index=st["project_index"],
        awaiting_topic=True,
        topic=None,
        header_pending=False,
    )

# ============ Utils ============
def _extract_leading_int(text: str) -> Optional[int]:
    t = normalize(text)
    num = ""
    for ch in t:
        if ch.isdigit():
            num += ch
        else:
            break
    return int(num) if num else None

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

def is_change_project_command(text: str) -> bool:
    """
    Comandos que levam ao menu de PROJETOS (fase 1).
    Exemplos: "menu", "trocar projeto", "mudar projeto", "reset projeto"
    """
    t = normalize(text)
    if t == "menu":
        return True
    return ("projeto" in t) and any(x in t for x in ("trocar", "mudar", "alterar", "reset", "reiniciar", "menu"))

def is_change_topic_command(text: str) -> bool:
    """
    Comandos que levam ao menu de TÓPICOS (fase 2).
    Exemplos: "trocar tópico", "menu tópico", "mudar assunto"
    """
    t = normalize(text)
    if "menu topico" in t or "menu tópico" in t:
        return True
    triggers = ("trocar", "mudar", "alterar", "reset", "reiniciar", "outro", "novo")
    scope = ("topico", "tópico", "assunto")
    return any(x in t for x in triggers) and any(y in t for y in scope)

def topic_label(num: int) -> str:
    return TOPICS_BY_ID.get(num, "Alternativa Inválida")

# ============ Menus dinâmicos ============
async def send_projects_menu(chat_id: str):
    ps = allowed_projects(chat_id)  # chat_id deve bater com o "number" do users.json
    if not ps:
        await send_whatsapp_message(
            number=chat_id,
            text="Você não tem permissão para acessar as informações. Por gentileza fale com o administrador."
        )
        await reset_to_projects(chat_id)
        return
    menu = build_project_menu_for(ps)
    await send_whatsapp_message(number=chat_id, text=menu)
    await set_user_state(chat_id, awaiting_project=True, awaiting_topic=False, topic=None, header_pending=False)

async def send_topics_menu(chat_id: str):
    st = await get_user_state(chat_id)

    # Sem projeto -> redireciona
    if st.get("awaiting_project") or not st.get("project_id"):
        await send_whatsapp_message(number=chat_id, text="Antes, escolha um *Projeto*.")
        await reset_to_projects(chat_id)
        await send_projects_menu(chat_id)
        return

    # Projeto que pula tópicos -> redireciona
    if project_skip_topics(st["project_id"]):
        await send_whatsapp_message(
            number=chat_id,
            text="O projeto selecionado não possui *Tópicos*. Escolha um *Projeto* diferente para ver tópicos."
        )
        await reset_to_projects(chat_id)
        await send_projects_menu(chat_id)
        return

    # Caso normal
    menu = build_topics_menu()
    await send_whatsapp_message(number=chat_id, text=menu)
    await set_user_state(chat_id, awaiting_topic=True, header_pending=False)

# ============ Webhook ============
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    chat_id = data.get("data", {}).get("key", {}).get("remoteJid")
    if not chat_id or "@g.us" in chat_id:
        return {"status": "ignored"}

    # -------- Allow-list --------
    if not user_allowed(chat_id):
        await send_whatsapp_message(
            number=chat_id,
            text="Olá! Seu número ainda não está autorizado a usar o IAires. "
                 "Peça habilitação ao administrador."
        )
        # Não inicia fluxo para não vazar menus
        return {"status": "forbidden"}

    text = extract_text_message(data)
    if not text:
        await send_whatsapp_message(
            number=chat_id,
            text="Não entendi. Envie uma mensagem de texto. Digite 'menu' para começar."
        )
        return {"status": "no_text"}

    text = text.strip()

    state = await get_user_state(chat_id)

    # -------- Comandos de troca --------
    if is_change_project_command(text):
        await reset_to_projects(chat_id)
        await send_projects_menu(chat_id)
        return {"status": "menu_projects"}

    if is_change_topic_command(text):
        # pega o estado atual
        state = await get_user_state(chat_id)

        # Sem projeto escolhido -> volta para projetos
        if state.get("awaiting_project") or not state.get("project_id"):
            await send_whatsapp_message(number=chat_id, text="Antes, escolha um *Projeto*.")
            await reset_to_projects(chat_id)
            await send_projects_menu(chat_id)
            return {"status": "menu_topics_blocked_no_project"}

        # Projeto atual pula tópicos (ex.: Manuais) -> manda para projetos
        if project_skip_topics(state["project_id"]):
            await send_whatsapp_message(
                number=chat_id,
                text="Esse Índice não possui *Tópicos*. Escolha um *Projeto*, para ver os seu respectivos tópicos."
            )
            await reset_to_projects(chat_id)
            await send_projects_menu(chat_id)
            return {"status": "menu_topics_blocked_skip_project"}

        # Caso normal: pode abrir menu de tópicos
        await reset_to_topics(chat_id)
        await send_topics_menu(chat_id)
        return {"status": "menu_topics"}

    # -------- Fase 1: aguardando escolha do PROJETO --------
    if state["awaiting_project"]:
        pid = _extract_leading_int(text)
        if pid is not None:
            if pid not in allowed_projects(chat_id):
                await send_projects_menu(chat_id)
                return {"status": "invalid_project"}

            idx = project_index(pid)
            proj = PROJECTS_BY_ID[pid]

            if project_skip_topics(pid):
                # Pula a seleção de tópicos e vai direto para a conversa
                await set_user_state(
                    chat_id,
                    awaiting_project=False,
                    project_id=pid,
                    project_index=idx,
                    awaiting_topic=False,     # << não vai abrir menu de tópicos
                    topic=None,               # sem tópico → doc_topic cairá no DEFAULT ("Geral")
                    header_pending=True,      # adiciona cabeçalho na próxima msg
                )
                await send_whatsapp_message(
                    number=chat_id,
                    text=f"Índice definido: *{proj['code']} - {proj['label']}*.\n"
                        "Tudo certo!\n\nAgora me diga, no que posso te ajudar hoje?"
                )
                return {"status": "project_selected_skip_topics", "project_index": idx}

            # Fluxo normal (com tópicos)
            await set_user_state(
                chat_id,
                awaiting_project=False,
                project_id=pid,
                project_index=idx,
                awaiting_topic=True,
                topic=None,
                header_pending=False,
            )
            await send_whatsapp_message(
                number=chat_id,
                text=f"Projeto definido: *{proj['code']} - {proj['label']}*"
            )
            await send_topics_menu(chat_id)
            return {"status": "project_selected", "project_index": idx}
        else:
            await send_projects_menu(chat_id)
            return {"status": "awaiting_project_number"}

    # -------- Fase 2: aguardando escolha do TÓPICO --------
    if state["awaiting_topic"]:
        opt = _extract_leading_int(text)
        if opt is not None:
            label = topic_label(opt)
            if label == "Alternativa Inválida":
                await send_whatsapp_message(number=chat_id, text="Tópico inválido.\n")
                await send_topics_menu(chat_id)
                return {"status": "invalid_topic"}
            await set_user_state(chat_id, awaiting_topic=False, topic=label, header_pending=True)
            await send_whatsapp_message(number=chat_id, text=f"Tópico definido: *{label}*.")
            await send_whatsapp_message(
                number=chat_id,
                text="Tudo certo!\nAgora me diga, no que posso te ajudar hoje?"
            )
            return {"status": "topic_selected", "topic": label}
        else:
            await send_topics_menu(chat_id)
            return {"status": "awaiting_topic_number"}

    # -------- Conversa livre (já tem projeto + tópico) --------
    await bump_state_ttl(chat_id)
    topic = state["topic"] or "Tópico"

    # Cabeçalho de contexto só na 1ª mensagem após a escolha
    if state["header_pending"]:
        proj = PROJECTS_BY_ID.get(state["project_id"], {"code": "?", "label": "?"})
        header = f"[PROJETO: {proj['code']} - {proj['label']}] [TÓPICO: {topic}] "
        # adiciona quebra de linha para separar do texto seguinte no buffer
        await buffer_message(chat_id=chat_id, message=header + "\n")
        await set_user_state(chat_id, header_pending=False)

    # Encaminha fala do usuário para a IA (buffer/debounce)
    await buffer_message(chat_id=chat_id, message=text + "\n")

    return {
        "status": "ok",
        "project_index": state["project_index"],
        "project_id": state["project_id"],
        "topic": topic,
    }
