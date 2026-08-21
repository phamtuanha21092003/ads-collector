import requests
import os


def send_telegram_message(
    message: str,
) -> bool:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "8843853014:AAEpSYJljGJYb4F6qsAFuq1FFWJUlkuEjng")
    if not bot_token:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": '-5451045176',
                "text": message,
            },
            timeout=10,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("ok", False)

    except requests.exceptions.RequestException as e:
        print(f"[TELEGRAM] Request failed: {e}")
        return False

    except Exception as e:
        print(f"[TELEGRAM] Unexpected error: {e}")
        return False
