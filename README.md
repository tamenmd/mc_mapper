# 🩺 MC Mapper

An Anki add-on that automatically transforms unstructured past exam questions (text & screenshots) into clean multiple-choice cards.

## ✨ Features
* **AI Repair:** Analyzes text and **images** (screenshots) to recognize the question, options, and solution.
* **Turbo Mode (Auto-Secure):** Automatically processes hundreds of unambiguous questions in seconds.
* **Duplicate Protection:** Warns about duplicate or very similar questions.
* **Filters:** Automatically hides cards that have already been processed.

## 🛠 Installation
1.  Copy the `mc_mapper` folder into your Anki add-ons folder (find it via Anki: *Tools -> Add-ons -> View Files*).
2.  Restart Anki.

## ⚙️ Setup (Important!)
To make the AI work, you must provide your OpenAI key:
1.  In Anki, go to **Tools -> Add-ons**.
2.  Select **MC Mapper** and click on **Config**.
3.  Enter your API key under `openai_api_key`.

## 📖 Usage
1.  Select your past exam questions in the Anki Browser.
2.  Right-click -> **MC-Mapper…**
3.  In the window:
    * **Apply (or Ctrl+Enter):** Saves the card.
    * **AI-Fix (or Ctrl+A):** Lets the AI structure the content (including images).
    * **Auto-Secure:** Fully automatically processes all problem-free cards.