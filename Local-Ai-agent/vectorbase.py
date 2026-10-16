from langchain_ollama import OllamaEmbeddings
from langchain.chroma import Chroma
from langchain_core.documents import Document
import os
import pandas as pd

df = pd.read_csv("C:\\Users\\DELL\\OneDrive\\Desktop\\Local AI Agent\\support_tickets.csv")
embedding = OllamaEmbeddings(model = "mxbai-embed-large")

db_location = "./chromea_langchain_db"

add_documents = not os.path.exists(db_location)

if add_documents:
    documents = []
    ids = []

    for i,row in df.iterrows():
        documents.append(Document(page_content=row['content'], 
                                  metadata={"ticket_id": row['ticket_id']}))
        ids.append(str(row['ticket_id']))
        document.append(document)
        vectorstore = chroma(
            collection_name="support_tickets",
            persistence_directory=db_location,
            embedding_function=embedding
        )

        if add_documents:
            vectorstore.add_documents(documents, ids=ids)
            retirieve_documents = vector_store.as_retriever(search_kwargs={"k": 3})