# elasticstore.py
import re
import os
import json
import math
from typing import List, Optional, Dict, Any
from collections import defaultdict
from pydantic import ConfigDict

from langchain_ollama import OllamaEmbeddings
from langchain_elasticsearch import ElasticsearchStore
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

# ========= CONFIG =========
#CA_PATH = os.getenv("CA_PATH", "/app/http_ca.crt")
FINGERPRINT = os.getenv("ES_CERT_FINGERPRINT")
ES_URL = "https://172.16.200.237:9000"
ES_INDEX = "manuais"
ES_USER = "elastic"
ES_PASSWORD = "Aires2025"

# Embeddings (texto) — BGE-M3 no Ollama
_embedding = OllamaEmbeddings(
    base_url="http://172.16.200.20:11434",
    model="bge-m3",
)

# Vector store compartilhado
_vector_store = ElasticsearchStore(
    es_url=ES_URL,
    index_name=ES_INDEX,
    embedding=_embedding,
    es_user=ES_USER,
    es_password=ES_PASSWORD,
    es_params={
        #"ca_certs": CA_PATH,
        "ssl_assert_fingerprint": FINGERPRINT,
        "verify_certs": True
    }
)

# ========= Utils (mesmas ideias do seu código) =========
def _cosine(a, b):
    s = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(y*y for y in b))
    return s / (na*nb + 1e-12)

def extract_keywords(question: str) -> List[str]:
    q = question.lower()
    kws = []
    technical = [
        "o3", "ozônio", "ozonio", "csn", "vale", "calibração", "calibracao",
        "sensor", "no2", "so2", "pm2.5", "pm10", "analisador", "certificado",
        "osiris", "monitoramento", "qualidade", "ar", "particulado"
    ]
    for t in technical:
        if t in q:
            kws.append(t)

    words = re.findall(r"\b\w{4,}\b", q)
    stop = {"sobre"}  # pode incrementar depois
    for w in words:
        if w not in stop and w not in kws:
            kws.append(w)
    return list(set(kws))

def pick_best_source(question: str, candidates: List[Document]) -> str:
    if not candidates:
        return ""
    keywords = extract_keywords(question)

    buckets = defaultdict(list)
    for d in candidates:
        src = (d.metadata or {}).get("source", "")
        if src:
            buckets[src].append(d)

    if len(buckets) == 1:
        return list(buckets.keys())[0]

    source_scores: Dict[str, float] = {}
    for src, docs in buckets.items():
        score = 0.0
        src_lower = src.lower()
        # match no filename
        for kw in keywords:
            if kw in src_lower:
                score += 2.0
        # match no conteúdo
        content_matches = 0
        for doc in docs[:5]:
            content = (doc.page_content or "").lower()
            if any(kw in content for kw in keywords):
                content_matches += 1
        score += content_matches * 1.5
        # média de score do ES se tiver
        rel = [d.metadata.get("score", 0) for d in docs if d.metadata.get("score") is not None]
        if rel:
            score += (sum(rel) / len(rel)) * 0.5
        if score > 0:
            source_scores[src] = score

    if source_scores:
        return max(source_scores.items(), key=lambda x: x[1])[0]

    # fallback por média do score
    avg_scores: Dict[str, float] = {}
    for src, docs in buckets.items():
        scores = [d.metadata.get("score", 0) for d in docs if d.metadata.get("score") is not None]
        if scores:
            avg_scores[src] = sum(scores) / len(scores)
    if avg_scores:
        return max(avg_scores.items(), key=lambda x: x[1])[0]

    # último fallback: maior bucket
    if buckets:
        return max(buckets.items(), key=lambda kv: len(kv[1]))[0]
    return ""

def _format_context(docs: List[Document]) -> str:
    parts = []
    for d in docs:
        md = d.metadata or {}
        src = md.get("source", "?")
        pg  = md.get("page", "?")
        kind = md.get("kind", "?")
        score = md.get("score", "N/A")
        if isinstance(score, (int, float)):
            parts.append(f"[Source: {src} | página: {pg} | tipo: {kind} | score: {score:.3f}]\n{d.page_content}")
        else:
            parts.append(f"[Source: {src} | página: {pg} | tipo: {kind} | score: {score}]\n{d.page_content}")
    return "\n\n".join(parts)

# ========= Retriever 2 estágios =========
class LockedSourceElasticsearchRetriever(BaseRetriever):
    """Retrieval em 2 fases: amplo → decide melhor 'source' → refina filtrando pelo source."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    vector_store: ElasticsearchStore
    k_broad: int = 40
    num_candidates: int = 100
    k_final: int = 10

    # campo privado para guardar o retriever do 1º estágio
    _broad: Any = None

    # >>> REMOVA o __init__ personalizado <<<
    def model_post_init(self, __context: Any) -> None:
        # chamado após a validação Pydantic
        self._broad = self.vector_store.as_retriever(
            search_kwargs={"k": self.k_broad, "num_candidates": self.num_candidates}
        )
    
    def _filter_by_source(self, chosen_source: str, k_final: int) -> List[Document]:
        # 1) tenta .keyword (requer mapeamento com subcampo keyword)
        for flt in [
            {"term": {"metadata.source.keyword": chosen_source}},
            {"term": {"metadata.source": chosen_source}},
            {"match_phrase": {"metadata.source": chosen_source}},
        ]:
            try:
                retr = self.vector_store.as_retriever(
                    search_kwargs={"k": k_final, "num_candidates": k_final * 2, "filter": flt}
                )
                locked = retr.invoke(chosen_source)
                if locked:
                    return locked
            except Exception:
                pass
        return []

    def _get_relevant_documents(
        self, query: str, *, run_manager=None, **kwargs
    ) -> List[Document]:
        try:
            broad_docs: List[Document] = self._broad.invoke(query)
        except Exception:
            # fallback: keywords simples
            broad_docs = []
            for kw in extract_keywords(query):
                try:
                    broad_docs.extend(self.vector_store.similarity_search(kw, k=5))
                except Exception:
                    pass

        # fontes únicas
        unique_sources = {
            (d.metadata or {}).get("source", "")
            for d in broad_docs if (d.metadata or {}).get("source")
        }

        if len(unique_sources) <= 1:
            return broad_docs[: self.k_final]

        chosen = pick_best_source(query, broad_docs)
        if chosen:
            locked = self._filter_by_source(chosen, self.k_final)
            if locked:
                return locked
            # fallback manual se filtro não trouxe nada
            same_src = [d for d in broad_docs if (d.metadata or {}).get("source") == chosen]
            return same_src[: self.k_final]

        return broad_docs[: self.k_final]


# ---------- Fábricas públicas ----------
def get_elastic_vectorstore() -> ElasticsearchStore:
    return _vector_store

def get_elastic_retriever(
    k_broad: int = 40,
    num_candidates: int = 100,
    k_final: int = 10
) -> BaseRetriever:
    return LockedSourceElasticsearchRetriever(
        vector_store=_vector_store,
        k_broad=k_broad,
        num_candidates=num_candidates,
        k_final=k_final,
    )
