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
ALERT_THRESHOLD  = 3     # saada hoiatus kui müüa skoor >= 3
STOP_LOSS_PCT    = -10.0  # stop-loss hoiatus kui kahjum >= 10%
TAKE_PROFIT_PCT  =  20.0  # take-profit hoiatus kui kasum >= 20%
MIN_PROFIT_PCT   =   2.0  # müügihoiatus ainult kui kasum >= 2% (väldi müüki nulli lähedal)

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

    alerted = False

    # --- STOP-LOSS hoiatus ---
    if profit_pct <= STOP_LOSS_PCT:
        alert = (
            f"🛑 *STOP-LOSS HOIATUS — {now_eesti()}*\n\n"
            f"💵 XRP hind: *{price_eur:.4f} EUR*\n\n"
            f"📂 Sinu positsioon:\n"
            f"  {pos['xrp_amount']:.2f} XRP @ {pos['buy_price_eur']:.4f} EUR\n"
            f"  Praegune väärtus: {current_value:.2f} EUR\n"
            f"  📉 *{sign}{profit_eur:.2f} EUR ({sign}{profit_pct:.1f}%)*\n\n"
            f"⚠️ Kahjum on jõudnud {STOP_LOSS_PCT:.0f}% piirini!\n\n"
            f"*Kaalu müüki kahjumi piiramiseks.*\n"
            f"_Mine Bybit → müü XRP → nupp.pyw → MÜÜA_"
        )
        send_alert(alert)
        print(f"Stop-loss hoiatus saadetud! P&L: {profit_pct:.1f}%")
        alerted = True

    # --- TAKE-PROFIT hoiatus ---
    elif profit_pct >= TAKE_PROFIT_PCT:
        alert = (
            f"🎯 *TAKE-PROFIT HOIATUS — {now_eesti()}*\n\n"
            f"💵 XRP hind: *{price_eur:.4f} EUR*\n\n"
            f"📂 Sinu positsioon:\n"
            f"  {pos['xrp_amount']:.2f} XRP @ {pos['buy_price_eur']:.4f} EUR\n"
            f"  Praegune väärtus: {current_value:.2f} EUR\n"
            f"  📈 *{sign}{profit_eur:.2f} EUR ({sign}{profit_pct:.1f}%)*\n\n"
            f"✨ Kasum on jõudnud +{TAKE_PROFIT_PCT:.0f}%!\n\n"
            f"*Kaalugi kasumi lukustamist.*\n"
            f"_Mine Bybit → müü XRP → nupp.pyw → MÜÜA_"
        )
        send_alert(alert)
        print(f"Take-profit hoiatus saadetud! P&L: {profit_pct:.1f}%")
        alerted = True

    # --- MÜÜGISIGNAAL hoiatus ---
    # Tingimused müügihoiatuseks:
    # 1. Müügisignaal piisavalt tugev (>= 3)
    # 2. Kasum vähemalt 2% (ei müü nulli lähedal)
    # 3. Ostusignaalid on kadunud (buy <= 1) — tõusutrend on lõppenud
    if sell >= ALERT_THRESHOLD and sell > buy:
        if profit_pct < MIN_PROFIT_PCT:
            print(f"Müügisignaal {sell} aga kasum liiga väike ({profit_pct:.1f}%) — ootan tõusu.")
        elif buy > 1:
            print(f"Müügisignaal {sell} aga ostusignaal veel {buy} — tõusutrend kestab, ootan pöördumist.")
        else:
            sell_reasons = [r for r in sig["reasons"] if "MÜÜA" in r]
            reasons_text = "\n".join(f"  • {r}" for r in sell_reasons)

            alert = (
                f"🚨 *MÜÜGIHOIATUS — {now_eesti()}*\n\n"
                f"💵 XRP hind: *{price_eur:.4f} EUR*\n"
                f"📊 Müüa signaal: *{sell}* | Osta: {buy}\n\n"
                f"📂 Sinu positsioon:\n"
                f"  {pos['xrp_amount']:.2f} XRP @ {pos['buy_price_eur']:.4f} EUR\n"
                f"  Praegune väärtus: {current_value:.2f} EUR\n"
                f"  *{sign}{profit_eur:.2f} EUR ({sign}{profit_pct:.1f}%)*\n\n"
                f"📉 Signaalid:\n{reasons_text}\n\n"
                f"*Mine Bybit → müü XRP kohe!*\n"
                f"_Seejärel vajuta nupp.pyw → MÜÜA_"
            )
            send_alert(alert)
            print(f"Müügihoiatus saadetud! Müüa: {sell}, Osta: {buy}, P&L: {profit_pct:.1f}%")
            alerted = True

    if not alerted:
        print(f"Pole hoiatusi (sell:{sell} buy:{buy} P&L:{profit_pct:.1f}%) — jätkan jälgimist.")

if __name__ == "__main__":
    main()
