"""
Monitor - kontrollib müügisignaali iga 15 minuti järel.
Saadab kohese Telegrami hoiatuse kui müügisignaal on tugev.
Töötab ainult kui positsioon on avatud.

Kasumi maksimeerimise loogika:
- Jälgib hinna tippu (trailing high)
- Müügihoiatus kui hind langeb 8% tipust + müügisignaal
- Ei müü kui tõusutrend kestab (ostusignaalid aktiivsed)
- Ei müü alla 2% kasumi (v.a stop-loss)
"""

import os, sys, json, requests
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trader import get_ohlc, get_price, calculate_indicators, generate_signal
from trader import TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS, COINGECKO_API_KEY

POSITION_FILE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "position.json")
PEAK_FILE        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "peak.json")
ALERT_THRESHOLD  = 3     # müügihoiatus kui müüa skoor >= 3
STOP_LOSS_PCT    = -10.0  # stop-loss kui kahjum >= 10%
TAKE_PROFIT_PCT  =  20.0  # take-profit hoiatus kui kasum >= 20%
MIN_PROFIT_PCT   =   2.0  # müügihoiatus ainult kui kasum >= 2%
TRAILING_DROP    =   8.0  # trailing stop: hoiatus kui hind langeb 8% tipust

def now_eesti():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")

def read_position():
    with open(POSITION_FILE, encoding="utf-8") as f:
        return json.load(f)

def read_peak():
    if os.path.exists(PEAK_FILE):
        with open(PEAK_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"peak_price": 0, "peak_date": None}

def update_peak(price_eur):
    peak = read_peak()
    if price_eur > peak["peak_price"]:
        peak["peak_price"] = price_eur
        peak["peak_date"]  = now_eesti()
        with open(PEAK_FILE, "w", encoding="utf-8") as f:
            json.dump(peak, f, indent=2)
        print(f"Uus tipphind salvestatud: {price_eur:.4f} EUR")
    return peak

def reset_peak():
    """Kustuta tipp kui positsioon suletud."""
    if os.path.exists(PEAK_FILE):
        os.remove(PEAK_FILE)

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
        reset_peak()
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

    # Uuenda tipphinda
    peak = update_peak(price_eur)
    peak_price    = peak["peak_price"]
    peak_date     = peak.get("peak_date", "—")
    drop_from_peak = ((price_eur - peak_price) / peak_price) * 100  # negatiivne = langus tipust

    print(f"XRP: {price_eur:.4f} EUR | Tipphind: {peak_price:.4f} EUR ({drop_from_peak:+.1f}%) | Müüa: {sell} | Osta: {buy} | P&L: {sign}{profit_eur:.2f} EUR ({sign}{profit_pct:.1f}%)")

    alerted = False

    # --- STOP-LOSS hoiatus (alati, sõltumata kasumist) ---
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

    # --- TAKE-PROFIT hoiatus (kasum jõudis 20%-ni) ---
    elif profit_pct >= TAKE_PROFIT_PCT:
        alert = (
            f"🎯 *TAKE-PROFIT HOIATUS — {now_eesti()}*\n\n"
            f"💵 XRP hind: *{price_eur:.4f} EUR*\n"
            f"📈 Tipphind: {peak_price:.4f} EUR ({peak_date})\n\n"
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

    # --- TRAILING STOP hoiatus ---
    # Kui hind on langenud 8% tipust JA müügisignaal olemas JA kasumis
    elif (drop_from_peak <= -TRAILING_DROP
          and profit_pct >= MIN_PROFIT_PCT
          and sell >= 2):
        alert = (
            f"📉 *TRAILING STOP — {now_eesti()}*\n\n"
            f"💵 XRP hind: *{price_eur:.4f} EUR*\n"
            f"🔺 Tipphind oli: {peak_price:.4f} EUR ({peak_date})\n"
            f"📉 Langus tipust: *{drop_from_peak:.1f}%*\n\n"
            f"📂 Sinu positsioon:\n"
            f"  {pos['xrp_amount']:.2f} XRP @ {pos['buy_price_eur']:.4f} EUR\n"
            f"  Praegune väärtus: {current_value:.2f} EUR\n"
            f"  *{sign}{profit_eur:.2f} EUR ({sign}{profit_pct:.1f}%)*\n\n"
            f"⚠️ Hind on kukkunud tipust {TRAILING_DROP:.0f}% — tipp ilmselt möödas!\n\n"
            f"*Müü kasumi lukustamiseks.*\n"
            f"_Mine Bybit → müü XRP → nupp.pyw → MÜÜA_"
        )
        send_alert(alert)
        print(f"Trailing stop hoiatus saadetud! Langus tipust: {drop_from_peak:.1f}%, P&L: {profit_pct:.1f}%")
        alerted = True

    # --- MÜÜGISIGNAAL hoiatus ---
    # Ainult kui: signaal tugev + kasumis + tõusutrend lõppenud (buy <= 1)
    if not alerted and sell >= ALERT_THRESHOLD and sell > buy:
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
                f"📊 Müüa signaal: *{sell}* | Osta: {buy}\n"
                f"🔺 Tipphind: {peak_price:.4f} EUR ({drop_from_peak:+.1f}% tipust)\n\n"
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
        print(f"Pole hoiatusi — jätkan jälgimist. (sell:{sell} buy:{buy} P&L:{profit_pct:.1f}% tipust:{drop_from_peak:.1f}%)")

if __name__ == "__main__":
    main()
