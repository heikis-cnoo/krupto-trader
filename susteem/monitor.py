"""
Monitor - kontrollib müügisignaali iga 15 minuti järel.
Saadab kohese Telegrami hoiatuse kui müügisignaal on tugev.
Töötab ainult kui positsioon on avatud.
"""

import os, sys, json, requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trader import get_ohlc, get_price, calculate_indicators, generate_signal
from trader import TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS, COINGECKO_API_KEY

POSITION_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position.json")
ALERT_THRESHOLD  = 3  # saada hoiatus kui müüa skoor >= 3

def now_eesti():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")

def read_position():
    with open(POSITION_FILE, encoding="utf-8") as f:
        return json.load(f)

def send_alert(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        requests.post(url, json={
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
        }, timeout=10)

def main():
    pos = read_position()

    if pos.get("status") != "holding":
        print("Positsioon suletud — monitooringut pole vaja.")
        return

    print(f"Positsioon avatud — kontrollin müügisignaali...")

    price_data = get_price("ripple")
    price_eur  = price_data.get("eur", 0)
    change_24h = price_data.get("eur_24h_change", 0)

    df  = get_ohlc("ripple", days=30)
    ind = calculate_indicators(df)
    sig = generate_signal(ind, change_24h)

    sell = sig["sell"]
    buy  = sig["buy"]

    current_value = pos["xrp_amount"] * price_eur
    profit_eur    = current_value - pos["buy_amount_eur"]
    profit_pct    = (profit_eur / pos["buy_amount_eur"]) * 100
    sign          = "+" if profit_eur >= 0 else ""

    print(f"XRP: {price_eur:.4f} EUR | Müüa: {sell} | Osta: {buy} | P&L: {sign}{profit_eur:.2f} EUR")

    if sell >= ALERT_THRESHOLD and sell > buy:
        sell_reasons = [r for r in sig["reasons"] if "MÜÜA" in r]
        reasons_text = "\n".join(f"  • {r}" for r in sell_reasons)

        alert = (
            f"*MUUGIHOIATUS — {now_eesti()}*\n\n"
            f"XRP hind: *{price_eur:.4f} EUR*\n"
            f"Muua signaal: *{sell}* | Osta: {buy}\n\n"
            f"Sinu positsioon:\n"
            f"  {pos['xrp_amount']:.2f} XRP ostetud @ {pos['buy_price_eur']:.4f} EUR\n"
            f"  Praegune vaartus: {current_value:.2f} EUR\n"
            f"  *{sign}{profit_eur:.2f} EUR ({sign}{profit_pct:.1f}%)*\n\n"
            f"Signaalid:\n{reasons_text}\n\n"
            f"*Mine Bybit → muua XRP kohe!*\n"
            f"_Seejärel vajuta nupp.pyw → MUUA_"
        )
        send_alert(alert)
        print(f"Hoiatus saadetud! Müüa skoor: {sell}")
    else:
        print(f"Pole piisavat müügisignaali (sell:{sell} buy:{buy}) — jätkan jälgimist.")

if __name__ == "__main__":
    main()
