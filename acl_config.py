import json
import os
from typing import Dict, List, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_DIR  = os.path.join(BASE_DIR, "config")

def _load_json(name: str) -> Dict[str, Any]:
    path = os.path.join(CFG_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ---------- Projects ----------
_projects_raw = _load_json("projects.json")["projects"]
PROJECTS_BY_ID = {int(p["id"]): p for p in _projects_raw}

def build_project_menu_for(user_projects: List[int]) -> str:
    lines = ["Bem vindo ao assistente AI-res,", ""]
    lines.append("Informe sobre o que você deseja falar:\n")
    for pid in user_projects:
        p = PROJECTS_BY_ID.get(pid)
        if p:
            lines.append(f'{p["id"]} - {p["code"]} - {p["label"]}')
    lines.append("")
    lines.append("Responda com o **número** referente ao que você deseja.")
    return "\n".join(lines)

def project_skip_topics(pid: int) -> bool:
    p = PROJECTS_BY_ID.get(pid)
    return bool(p and p.get("skip_topics"))

# ---------- Users ----------
_users_raw = _load_json("users.json")["users"]
USER_TO_PROJECTS: Dict[str, List[int]] = {
    # número E.164 / remoto do Evolution
    u["number"]: [int(x) for x in u.get("projects", [])] for u in _users_raw
}
USER_NAMES: Dict[str, str] = {u["number"]: u.get("name", "") for u in _users_raw}

def user_allowed(number: str) -> bool:
    return number in USER_TO_PROJECTS and len(USER_TO_PROJECTS[number]) > 0

def allowed_projects(number: str) -> List[int]:
    return USER_TO_PROJECTS.get(number, [])

def project_index(pid: int) -> str:
    p = PROJECTS_BY_ID.get(pid)
    return p["index"] if p else ""

# ---------- Topics ----------
_topics_raw = _load_json("topics.json")["topics"]
TOPICS_BY_ID = {int(t["id"]): t["label"] for t in _topics_raw}
LABEL_TO_DOC_TOPIC = {t["label"]: t["doc_topic"] for t in _topics_raw}

def build_topics_menu() -> str:
    lines = ["Agora escolha o **Tópico**:", ""]
    for t in _topics_raw:
        lines.append(f'{t["id"]} - {t["label"]}')
    lines.append("")
    lines.append("Responda com o **número** referente ao tópico.")
    return "\n".join(lines)
