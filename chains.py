# chains.py
from typing import Dict, Any, List
import os
import redis.asyncio as redis

from langchain_openai import ChatOpenAI
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory

from config import (
    OPENAI_MODEL_NAME,
    OPENAI_MODEL_TEMPERATURE,
    REDIS_URL,
)
from prompts import contextualize_prompt, qa_prompt
from memory import get_session_history
from retriever_api import call_retrieve
from acl_config import LABEL_TO_DOC_TOPIC  # mapeia label -> doc_topic

# Defaults caso algo falhe/esteja ausente no estado
DEFAULT_INDEX = os.getenv("RETRIEVER_DEFAULT_INDEX", "manuais")
DEFAULT_DOC_TOPIC = os.getenv("RETRIEVER_DEFAULT_DOC_TOPIC", "Geral")

# Redis para ler estado atual (projeto/tópico) da sessão
rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)

def _state_key(chat_id: str) -> str:
    return f"iaires:state:{chat_id}"

async def _get_doc_topic_for_session(chat_id: str) -> str:
    """
    Retorna o doc_topic a partir do label de tópico salvo no estado.
    """
    st = await rds.hgetall(_state_key(chat_id))
    topic_label = (st.get("topic") or "").strip() if st else ""
    return LABEL_TO_DOC_TOPIC.get(topic_label, DEFAULT_DOC_TOPIC)

async def _get_index_for_session(chat_id: str) -> str:
    """
    Retorna o índice (Elasticsearch) definido pelo projeto escolhido.
    """
    st = await rds.hgetall(_state_key(chat_id))
    return (st.get("project_index") or "").strip() if st else ""

def _build_context_from_docs(docs: List[Dict[str, Any]]) -> str:
    """Concatena os documentos da API em um único contexto textual."""
    parts = []
    for d in docs:
        md = d.get("metadata", {}) or {}
        src = md.get("source", "?")
        pg = md.get("page", "?")
        kind = md.get("kind", "?")
        score = md.get("score", None)
        head = f"[Source: {src} | página: {pg} | tipo: {kind}"
        if isinstance(score, (int, float)):
            head += f" | score: {score:.3f}"
        head += "]"
        content = d.get("page_content") or ""
        parts.append(f"{head}\n{content}")
    return "\n\n".join(parts)

async def _contextualize_question(llm: ChatOpenAI, question: str, history_messages):
    msgs = contextualize_prompt.format_messages(chat_history=history_messages, input=question)
    resp = await llm.ainvoke(msgs)
    return (resp.content or question).strip()

async def _qa_with_context(llm: ChatOpenAI, question: str, context: str, history_messages):
    msgs = qa_prompt.format_messages(context=context, input=question, chat_history=history_messages)
    resp = await llm.ainvoke(msgs)
    return (resp.content or "").strip()

def get_rag_chain():
    llm = ChatOpenAI(
        model=OPENAI_MODEL_NAME,
        temperature=OPENAI_MODEL_TEMPERATURE,
    )

    async def _run(inputs: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """
        inputs: {'input': <pergunta>}
        config: {'configurable': {'session_id': <chat_id>}}
        """
        question = (inputs.get("input") or "").strip()
        if not question:
            return {"answer": "Não recebi uma pergunta válida."}

        chat_id = (config.get("configurable", {}) or {}).get("session_id", "")
        history = get_session_history(chat_id)

        # 1) contextualiza pergunta com o histórico
        contextualized = await _contextualize_question(llm, question, history.messages)

        # 2) resolve doc_topic e index a partir do estado do usuário
        doc_topic = await _get_doc_topic_for_session(chat_id)
        index = await _get_index_for_session(chat_id) or DEFAULT_INDEX

        # 3) chama sua API retriever
        try:
            data = await call_retrieve(contextualized, index=index, doc_topic=doc_topic)
        except Exception as e:
            # Retorno amigável caso a retriever falhe
            return {
                "answer": f"Não consegui acessar a base de conhecimento agora (erro na retriever). "
                          f"Tente novamente em instantes.\n\nDetalhes: {e}",
                "chosen_source": None,
                "n_docs": 0,
                "index": index,
                "doc_topic": doc_topic,
            }

        # 4) usa 'context' se vier pronto; senão monta com 'docs'
        docs = data.get("docs", []) or []
        context_text = data.get("context") or _build_context_from_docs(docs)

        # 5) QA com stuffing do contexto
        answer = await _qa_with_context(llm, contextualized, context_text, history.messages)

        return {
            "answer": answer,
            "chosen_source": data.get("chosen_source"),
            "n_docs": len(docs),
            "index": index,
            "doc_topic": doc_topic,
        }

    return RunnableWithMessageHistory(
        runnable=RunnableLambda(_run),
        get_session_history=get_session_history,
        input_messages_key="input",
        history_messages_key="chat_history",
        output_messages_key="answer",
    )

def get_conversational_rag_chain():
    return get_rag_chain()
