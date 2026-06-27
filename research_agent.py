from dotenv import load_dotenv
from tavily import TavilyClient
load_dotenv()
import os

API_KEY = os.getenv("TAVILY_API_KEY")
client = TavilyClient(api_key = API_KEY)

def search_web(query):
    search = client.search(query)
    output = ""
    for result in search["results"]:
        title = result["title"]
        content = result["content"]
    output += f"Title: {title}, Content : {content}\n"
    return (output)
print(search_web("Tesla recent financial news"))
