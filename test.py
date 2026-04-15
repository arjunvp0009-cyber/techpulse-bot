from google import genai
from google.genai import types

c = genai.Client(api_key="AIzaSyC7sRPI0XA1Jhw1ZB5lJ_VXjBsykfQlPT8")
r = c.models.generate_content(
    model="models/gemini-2.0-flash-lite",
    contents='Say HELLO in JSON like: {"status": "HELLO"}'
)
print(r.text)
print("SUCCESS - model works!")
