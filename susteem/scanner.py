"""
Scanner - analüüsib kõiki Bybit EU krüptosid iga päev kell 6:30.
Leiab potentsiaalsed tõusjad ja saadab top 3 soovitust Telegrami.
Töötab täiesti eraldi XRP trader süsteemist.
"""

import os, sys, time, requests
import pandas as pd
import ta
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trader import TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS, COINGECKO_API_KEY

HEADERS = {"x-cg-demo-api-key": COINGECKO_API_KEY}

# Kõik Bybit EU krüptod (ilma stablecoinideta)
BYBIT_EU_COINS = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "XRP":  "ripple",
    "SOL":  "solana",
    "ADA":  "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "LTC":  "litecoin",
    "TON":  "the-open-network",
    "NEAR": "near",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
    "ENA":  "ethena",
    "ONDO": "ondo-finance",
    "WIF":  "dogwifcoin",
    "WLD":  "worldcoin-wld",
    "CATI": "catizen",
    "DOGS": "dogs",
}

def now_eesti():
    return (datetime.now(timezone.utc) + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")

def get_with_retry(url, params, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                print(f"  Rate limit, ootan {wait}s...")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                return None
            return r
        except Exception as e:
            print(f"  Viga: {e}")
            return None
    return None

def get_price(coin_id):
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": coin_id,
        "vs_currencies": "eur",
        "include_24hr_change": "true",
        "include_7d_change": "true",
    }
    r = get_with_retry(url, params)
    if r:
        return r.json().get(coin_id, {})
    return {}

def get_ohlc(coin_id, days=30):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {"vs_currency": "eur", "days": days}
    r = get_with_retry(url, params)
    if r is None:
        return None
    data = r.json()
    if not data or len(data) < 30:
        return None
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df.sort_values("timestamp").reset_index(drop=True)

def calculate_buy_score(df, change_24h, change_7d):
    """Arvuta ostusignaalide skoor — kõrgem = parem ostuhetk."""
    close = df["close"]
    score = 0
    reasons = []

    # RSI
    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi_now  = rsi_series.iloc[-1]
    rsi_prev = rsi_series.iloc[-4]

    # MACD
    macd_obj    = ta.trend.MACD(close)
    macd_line   = macd_obj.macd()
    signal_line = macd_obj.macd_signal()
    hist_series = macd_obj.macd_diff()
    hist_now    = hist_series.iloc[-1]
    hist_prev   = hist_series.iloc[-3]

    macd_bullish_cross = (macd_line.iloc[-2] < signal_line.iloc[-2]) and (macd_line.iloc[-1] >= signal_line.iloc[-1])

    # MA
    ma20_series = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    ma50_series = ta.trend.SMAIndicator(close, window=50).sma_indicator()

    price_crossed_above_ma20 = (close.iloc[-2] < ma20_series.iloc[-2]) and (close.iloc[-1] >= ma20_series.iloc[-1])
    golden_cross = False
    if len(ma50_series.dropna()) >= 2:
        golden_cross = (ma20_series.iloc[-2] < ma50_series.iloc[-2]) and (ma20_series.iloc[-1] >= ma50_series.iloc[-1])

    # Bollinger
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_lower = bb.bollinger_lband().iloc[-1]

    # Bullish divergents
    prices_last_10 = close.iloc[-10:]
    rsi_last_10    = rsi_series.iloc[-10:]
    price_new_low  = close.iloc[-1] < prices_last_10.min() * 1.005
    rsi_not_new_low = rsi_series.iloc[-1] > rsi_last_10.min() * 1.05
    bullish_div = price_new_low and rsi_not_new_low and rsi_now < 45

    # Punktid
    if macd_bullish_cross:
        score += 3
        reasons.append("MACD ristus üles")

    if rsi_prev < 32 and rsi_now > rsi_prev + 2:
        score += 3
        reasons.append(f"RSI tõuseb ülemüüdud tsoonist ({rsi_prev:.0f}→{rsi_now:.0f})")
    elif rsi_now < 35 and rsi_now > rsi_prev:
        score += 2
        reasons.append(f"RSI {rsi_now:.0f} — ülemüüdud, tõuseb")

    if bullish_div:
        score += 2
        reasons.append("Bullish divergents")

    if price_crossed_above_ma20:
        score += 2
        reasons.append("Hind ületas MA20 üles")

    if golden_cross:
        score += 3
        reasons.append("Golden Cross (MA20 > MA50)")

    if hist_now < 0 and hist_now > hist_prev:
        score += 1
        reasons.append("Languse momentum nõrgeneb")

    if close.iloc[-1] <= bb_lower * 1.015:
        score += 1
        reasons.append("Bollinger ala riba — odav tsoon")

    # 7 päeva trend — kui on langenud palju, võib põhi olla lähedal
    if change_7d is not None and change_7d < -15 and rsi_now < 40:
        score += 1
        reasons.append(f"7p langus {change_7d:.1f}% + RSI madal — põhi lähedal?")

    return score, round(rsi_now, 1), reasons

def analyze_all():
    results = []
    total = len(BYBIT_EU_COINS)

    for i, (symbol, coin_id) in enumerate(BYBIT_EU_COINS.items(), 1):
        print(f"[{i}/{total}] Analüüsin {symbol}...")
        try:
            price_data = get_price(coin_id)
            if not price_data:
                print(f"  {symbol}: hinnaandmed puuduvad, jätan vahele.")
                time.sleep(3)
                continue

            price_eur  = price_data.get("eur", 0)
            change_24h = price_data.get("eur_24h_change", 0)
            change_7d  = price_data.get("eur_7d_change", None)

            time.sleep(2)

            df = get_ohlc(coin_id, days=30)
            if df is None:
                print(f"  {symbol}: OHLC andmed puuduvad, jätan vahele.")
                time.sleep(3)
                continue

            score, rsi, reasons = calculate_buy_score(df, change_24h, change_7d)

            results.append({
                "symbol":    symbol,
                "coin_id":   coin_id,
                "price":     price_eur,
                "change_24h": change_24h,
                "change_7d": change_7d,
                "score":     score,
                "rsi":       rsi,
                "reasons":   reasons,
            })
            print(f"  {symbol}: skoor={score}, RSI={rsi}, 24h={change_24h:+.1f}%")

        except Exception as e:
            print(f"  {symbol}: viga — {e}")

        time.sleep(3)

    return results

def send_telegram(results):
    if not results:
        print("Pole tulemusi saata.")
        return

    # Sorteeri skoori järgi
    results.sort(key=lambda x: x["score"], reverse=True)
    top3 = [r for r in results if r["score"] >= 2][:3]

    now = now_eesti()
    lines = [
        f"🔍 *PÄEVA TÕUSJAD — {now}*",
        f"_Bybit EU top soovitused täna_",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    if not top3:
        lines.append("⏸ Täna pole tugevaid ostusignaale.")
        lines.append("_Turg vajab puhkust — oota homset analüüsi._")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for idx, r in enumerate(top3):
            medal = medals[idx] if idx < 3 else "▪️"
            change_icon = "📈" if r["change_24h"] >= 0 else "📉"
            lines.append(f"{medal} *{r['symbol']}*  —  skoor: {r['score']}")
            lines.append(f"   💵 {r['price']:.4f} EUR  {change_icon} {r['change_24h']:+.1f}% (24h)")
            lines.append(f"   📊 RSI: {r['rsi']}")
            if r["reasons"]:
                reasons_str = " · ".join(r["reasons"][:2])
                lines.append(f"   ✅ {reasons_str}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("⚠️ _Signaalid on informatiivsed, mitte garanteeritud._")
        lines.append("_Kasuta koos oma analüüsiga!_")

    # Lisa ülejäänud top 5 lühidalt
    rest = [r for r in results if r not in top3 and r["score"] >= 1][:5]
    if rest:
        lines.append("\n_Jälgi ka:_")
        for r in rest:
            lines.append(f"  • {r['symbol']}: skoor {r['score']}, RSI {r['rsi']}")

    text = "\n".join(lines)
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        resp = requests.post(url, json={
            "chat_id": chat_id, "text": text, "parse_mode": "Markdown"
        }, timeout=10)
        if resp.ok:
            print(f"Telegram saadetud → {chat_id}")
        else:
            print(f"Telegrami viga: {resp.text}")

def main():
    print(f"Scanner käivitub — {now_eesti()}")
    print(f"Analüüsin {len(BYBIT_EU_COINS)} münti...\n")
    results = analyze_all()
    print(f"\nAnalüüs valmis. {len(results)} münti analüüsitud.")
    send_telegram(results)

if __name__ == "__main__":
    main()
