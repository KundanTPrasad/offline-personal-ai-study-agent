import ollama

r = ollama.chat(
    model="qwen3.5:4b",
    messages=[{"role": "user", "content": "Say hi in one short line."}],
    think=False,
)
print(r["message"]["content"])