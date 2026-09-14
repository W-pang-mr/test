import os

import requests

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"


def main():
    offset = 0

    while True:
        response = requests.get(
            f"{API}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35,
        )
        data = response.json()

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message or not message.get("text"):
                continue

            chat_id = message["chat"]["id"]
            requests.post(
                f"{API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "درحال کاریم",
                },
                timeout=10,
            )


if __name__ == "__main__":
    main()
