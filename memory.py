from langchain_community.chat_message_histories import RedisChatMessageHistory

from config import REDIS_URL

#O session_idserá o RemoteJid, ou seja, com isso será possivel crirar historico de conversas por RemoteJid,
#Um historico por conversa ou grupo
def get_session_history(session_id):
    return RedisChatMessageHistory(
        session_id=session_id,
        url=REDIS_URL,
    )
    
