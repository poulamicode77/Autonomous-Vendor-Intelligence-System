from dotenv import load_dotenv
from groq import Groq
load_dotenv()
import os

API_KEY = os.getenv("GROQ_API_KEY")
LLM_MODEL = os.getenv("GROQ_MODEL")

client = Groq(api_key = API_KEY)
response = client.chat.completions.create(
    model = LLM_MODEL ,
    messages = [
        { "role" : "user","content" : "hello"}
            ]
    
)

print(response.choices[0].message.content)
