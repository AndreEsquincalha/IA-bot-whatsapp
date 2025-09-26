# topics.py (estrito)
import os, json

raw = os.getenv("TOPICS_SPEC")
if not raw:
    raise RuntimeError("TOPICS_SPEC ausente no .env")

try:
    spec = json.loads(raw)
except Exception as e:
    raise RuntimeError("TOPICS_SPEC inválido (JSON malformado)") from e

if not all(isinstance(x, dict) and {"id","label","doc_topic"} <= set(x.keys()) for x in spec):
    raise RuntimeError("TOPICS_SPEC inválido (esperado [{id,label,doc_topic}, ...])")

TOPICS_BY_ID = {int(x["id"]): x["label"] for x in spec}
LABEL_TO_DOC_TOPIC = {x["label"]: x["doc_topic"] for x in spec}

def build_menu_text() -> str:
    lines = [
        "Olá,",
        "Antes de começar a conversar com a IAires, escolha o tópico do assunto que será abordado.\n",
    ]
    for x in spec:
        lines.append(f'{x["id"]} - {x["label"]}')
    lines.append("\nEscolha o tópico digitando o número correspondente:")
    return "\n".join(lines)

MENU_TEXT = build_menu_text()
