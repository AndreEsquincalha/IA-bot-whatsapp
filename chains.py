from langchain.chains import create_history_aware_retriever, create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_openai import ChatOpenAI

from config import (
    OPENAI_MODEL_NAME,
    OPENAI_MODEL_TEMPERATURE,
)

from vectorstore import get_vectorstore
from prompts import contextualize_prompt, qa_prompt
from memory import get_session_history
from elasticstore import *

def get_rag_chain():
    llm = ChatOpenAI(
        model=OPENAI_MODEL_NAME,
        temperature=OPENAI_MODEL_TEMPERATURE,
    )
    
    retriever = get_elastic_retriever(
        k_broad=40,          
        num_candidates=100,  
        k_final=10           
    )

    history_aware_chain = create_history_aware_retriever(llm, retriever, contextualize_prompt)
    question_answer_chain = create_stuff_documents_chain(
        llm=llm,
        prompt=qa_prompt,
    )
    return create_retrieval_chain(history_aware_chain, question_answer_chain)

def get_conversational_rag_chain():
    rag_chain = get_rag_chain()
    return RunnableWithMessageHistory(
        runnable=rag_chain,
        get_session_history=get_session_history,
        input_messages_key='input',
        history_messages_key='chat_history',
        output_messages_key='answer',
    )
    