import os
import requests

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

KEYBOARD = {
    "keyboard": [["سلام"]],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}


def send_message(chat_id, text, reply_markup=None):
    data = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        data["reply_markup"] = reply_markup

    requests.post(
        f"{API}/sendMessage",
        json=data,
        timeout=10,
    )


def main():
    offset = 0

    while True:
        response = requests.get(
            f"{API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 30,
            },
            timeout=35,
        )
        data = response.json()

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message:
                continue

            text = message.get("text")
            chat_id = message["chat"]["id"]

            if text == "/start":
                send_message(chat_id, "سلام 👋", KEYBOARD)
            elif text == "سلام":
                send_message(chat_id, "سلام 👋", KEYBOARD)


if __name__ == "__main__":
    main()
