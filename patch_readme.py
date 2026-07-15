import re

with open("README.md", "r") as f:
    content = f.read()

# 1. Add read-msg.png to Demo section
old_demo = """### 🖼️ Image Generation (ChatGPT Image 2)"""
new_demo = """### 📖 Context Reading (/read Summarization)
![Context Reading](read-msg.png)

### 🖼️ Image Generation (ChatGPT Image 2)"""

if old_demo in content:
    content = content.replace(old_demo, new_demo)

# 2. Remove em dash and use alternative grammar
content = content.replace("### 💬 Full ChatGPT on Your Discord Server — **for free, no API needed.** 🚀", "### 💬 Full ChatGPT on Your Discord Server **for free, no API needed.** 🚀")
content = content.replace("— giving you full access", "giving you full access")
content = content.replace("— open a fresh thread for the response", ", opening a fresh thread for the response")
content = content.replace("— log in via terminal for 30", ", log in via terminal for 30")

# 3. Remove 'full' in chatgpt
content = content.replace("brings the full ChatGPT experience", "brings the complete ChatGPT experience")
content = content.replace("Full ChatGPT Feature Set:", "Complete ChatGPT Feature Set:")

# 4. Make robotic phrases sound humane
content = content.replace("performs page actions such as submitting prompts and files via JavaScript, polling generation streams, and capturing diagnostic screenshots.", "handles sending your prompts and files, reading the AI's response as it streams, and taking screenshots if something goes wrong.")
content = content.replace("custom JS scraping and throttled poll loops keep memory and CPU usage extremely low.", "carefully tuned scraping and paused checks keep memory and CPU usage nice and low.")
content = content.replace("Initializes the Discord client, registers slash commands (`/ask`, `/new`, `/read`), and listens to message events.", "Gets the Discord bot online, sets up the slash commands (`/ask`, `/new`, `/read`), and listens for your messages.")


with open("README.md", "w") as f:
    f.write(content)
print("README Patched")
