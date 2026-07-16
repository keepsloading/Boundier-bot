with open("README.md", "r") as f:
    content = f.read()

content = content.replace("### 💬 Full ChatGPT on Your Discord Server", "### 💬 ChatGPT on Your Discord Server")

with open("README.md", "w") as f:
    f.write(content)
print("Patched.")
