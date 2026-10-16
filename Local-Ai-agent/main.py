from langchain_ollama.llms import OllamaLLM
from langchain_core.prompts import ChatPromptTemplate

model = OllamaLLM(model="llama:3.2")

template = """
You are a data analysis assistant working with a CSV file.

Dataset: {file_name}
Rows: {n_rows}
Columns and types:
{schema}

First rows (sample):
{sample}

Rules:
- Answer only from the data above or from the results of tools you run. Never invent values.
- The data is messy: watch for inconsistent casing, extra spaces, duplicate rows,
  missing values, mixed date formats and impossible values (e.g. negative hours).
- If the question needs a calculation, write pandas code and show the result.
- If the data can't answer the question, say so.
- Keep answers short and state any assumptions.

Question: {question}

Answer:

"""
prompt = ChatPromptTemplate.from_template(template)


chain = prompt | model

while True:
    user_input = input("User: ")
    if user_input.lower() in ["exit", "quit"]:
        break

result = chain.invoke({"input": "What is the capital of France?"})
print(result)

