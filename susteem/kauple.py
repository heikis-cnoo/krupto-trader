"""
Kauple XRP — osta ja myy tegevused.
Kutsutakse GitHub Actions workflow_dispatch kaudu.
"""

import json
import os
import sys
import requests
from datetime import datetime

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN",    "8610980001:AAFpSLvBGQmqKjW2UNpnB43vA0ZrzkRzLdE")
TELEGRAM_CHAT_IDS = [
    os.environ.get("TELEGRAM_CHAT_ID", "1665605995"),
]
COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "CG-4Bsct34qk7h5cjj5JRuSzvuJ")
POSITION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position.json")


def read_position():
    if os.path.exists(POSITION_FILE):
        with open(POSITION_FILE, encoding="utf-8") as f:
            return json.load(f)
    return default_position()


def default_position():
    return {
        "status": "waiting",
        "buy_price_eur": None,
        "buy_amount_eur": None,
        "xrp_amount": None,
        "buy_date": None,
        "sell_price_eur": None,
        "sell_date": None,
        "profit_eur": None,
        "profit_pct": None,
    }


def write_position(pos):
    with open(POSITION_FILE, "w", encoding="utf-8") as f:
        json.dump(pos, f, indent=2, ensure_ascii=False)


def get_xrp_price():
    url     = "https://api.coingecko.com/api/v3/simple/price"
    params  = {"ids": "ripple", "vs_currencies": "eur", "include_24hr_change": "true"}
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    data = r.json().get("ripple", {})
    return data.get("eur", 0), data.get("eur_24h_change", 0)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        requests.post(url, json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }, timeout=10)


def action_buy(amount_eur: float):
    pos = read_position()
    if pos["status"] == "holding":
        send_telegram("Sul on juba avatud positsioon! Muua esmalt.")
        print("Viga: positsioon juba avatud.")
        return

    price, change = get_xrp_price()
    xrp_amount    = amount_eur / price
    now           = datetime.now().strftime("%d.%m.%Y %H:%M")

    pos = default_position()
    pos["status"]        = "holding"
    pos["buy_price_eur"] = price
    pos["buy_amount_eur"]= amount_eur
    pos["xrp_amount"]    = xrp_amount
    pos["buy_date"]      = now
    write_position(pos)

    msg = (
        f"*OSTO REGISTREERITUD*\n\n"
        f"Ostetud: *{xrp_amount:.2f} XRP*\n"
        f"Ostuhind: *{price:.4f} EUR*\n"
        f"Kulutus: *{amount_eur:.2f} EUR*\n"
        f"Aeg: {now}\n\n"
        f"_Jalgin nuu muugihetke ja annan teada..._"
    )
    send_telegram(msg)
    print(f"Ostetud {xrp_amount:.4f} XRP hinnaga {price:.4f} EUR")


def action_sell():
    pos = read_position()
    if pos["status"] == "waiting":
        send_telegram("Sul pole avatud positsiooni!")
        print("Viga: pole avatud positsiooni.")
        return

    price, _   = get_xrp_price()
    sell_value = pos["xrp_amount"] * price
    profit_eur = sell_value - pos["buy_amount_eur"]
    profit_pct = (profit_eur / pos["buy_amount_eur"]) * 100
    now        = datetime.now().strftime("%d.%m.%Y %H:%M")

    pos["status"]        = "waiting"
    pos["sell_price_eur"]= price
    pos["sell_date"]     = now
    pos["profit_eur"]    = round(profit_eur, 4)
    pos["profit_pct"]    = round(profit_pct, 2)
    write_position(pos)

    icon = "KASUM" if profit_eur >= 0 else "KAHJUM"
    msg = (
        f"*MUUK REGISTREERITUD*\n\n"
        f"Muudud: *{pos['xrp_amount']:.2f} XRP*\n"
        f"Muugihind: *{price:.4f} EUR*\n"
        f"Saadud: *{sell_value:.2f} EUR*\n\n"
        f"*{icon}: {profit_eur:+.2f} EUR ({profit_pct:+.1f}%)*\n\n"
        f"Ostetud: {pos['buy_date']} @ {pos['buy_price_eur']:.4f} EUR\n"
        f"Muudud: {now} @ {price:.4f} EUR\n\n"
        f"_Otsin nuu uut ostuhetke..._"
    )
    send_telegram(msg)
    print(f"Muudud {price:.4f} EUR, {icon}: {profit_eur:+.2f} EUR ({profit_pct:+.1f}%)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Kasutus: python susteem/kauple.py osta [summa] | myy")
        sys.exit(1)

    tegevus = sys.argv[1].lower()
    if tegevus == "osta":
        amount = float(sys.argv[2]) if len(sys.argv) > 2 else 100.0
        action_buy(amount)
    elif tegevus == "myy":
        action_sell()
    else:
        print(f"Tundmatu tegevus: {tegevus}")
        sys.exit(1)
