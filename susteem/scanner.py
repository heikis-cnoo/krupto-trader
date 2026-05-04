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

def calculate_bottom_score(df, change_24h, change_7d):
    """
    Otsi münte mis on PÕHJAS ja hakkavad pöörduma ülespoole.
    Välistab mündid mis on juba tõusnud (RSI kõrge, hind tipus).
    Kõrgem skoor = tugevam põhjapöördumise signaal.
    """
    close = df["close"]
    score = 0
    reasons = []
    warnings = []  # Miks münti EI soovitata

    # --- Indikaatorid ---
    rsi_series  = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi_now     = rsi_series.iloc[-1]
    rsi_prev    = rsi_series.iloc[-4]

    macd_obj    = ta.trend.MACD(close)
    macd_line   = macd_obj.macd()
    signal_line = macd_obj.macd_signal()
    hist_series = macd_obj.macd_diff()
    hist_now    = hist_series.iloc[-1]
    hist_prev   = hist_series.iloc[-3]

    ma20_series = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    ma50_series = ta.trend.SMAIndicator(close, window=50).sma_indicator()

    bb          = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper    = bb.bollinger_hband().iloc[-1]
    bb_lower    = bb.bollinger_lband().iloc[-1]
    bb_mid      = bb.bollinger_mavg().iloc[-1]

    prices_last_10 = close.iloc[-10:]
    rsi_last_10    = rsi_series.iloc[-10:]

    macd_bullish_cross = (macd_line.iloc[-2] < signal_line.iloc[-2]) and (macd_line.iloc[-1] >= signal_line.iloc[-1])
    price_crossed_above_ma20 = (close.iloc[-2] < ma20_series.iloc[-2]) and (close.iloc[-1] >= ma20_series.iloc[-1])

    golden_cross = False
    if len(ma50_series.dropna()) >= 2:
        golden_cross = (ma20_series.iloc[-2] < ma50_series.iloc[-2]) and (ma20_series.iloc[-1] >= ma50_series.iloc[-1])

    bullish_div = (
        close.iloc[-1] < prices_last_10.min() * 1.005 and
        rsi_series.iloc[-1] > rsi_last_10.min() * 1.05 and
        rsi_now < 45
    )

    # --- VÄLISTAMINE: münt on juba tõusu tipus ---
    # RSI üle 60 = juba kallis, tõus käimas → ei soovita
    if rsi_now > 60:
        warnings.append(f"RSI {rsi_now:.0f} — juba tõusu piirkonnas, kaugel põhjast")
        return -1, round(rsi_now, 1), warnings

    # Hind Bollinger üla riba lähedal = statistiliselt kallis
    if close.iloc[-1] >= bb_upper * 0.95:
        warnings.append("Hind Bollinger üla riba lähedal — tipp, mitte põhi")
        return -1, round(rsi_now, 1), warnings

    # 7 päeva +15% tõus = juba liikumas, hilineme
    if change_7d is not None and change_7d > 15:
        warnings.append(f"7p tõus {change_7d:.1f}% — juba tõusnud, võib olla hilja")
        return -1, round(rsi_now, 1), warnings

    # --- PÕHJA SIGNAALID (positiivsed punktid) ---

    # 1. RSI oli sügaval ülemüüdud ja hakkab tõusma = klassikaline põhjapöördumine
    if rsi_prev < 32 and rsi_now > rsi_prev + 2:
        score += 4
        reasons.append(f"RSI tõuseb ülemüüdud tsoonist ({rsi_prev:.0f}→{rsi_now:.0f}) ⭐")
    elif rsi_now < 35 and rsi_now > rsi_prev:
        score += 3
        reasons.append(f"RSI {rsi_now:.0f} — ülemüüdud, hakkab tõusma")
    elif rsi_now < 45 and rsi_now > rsi_prev + 3:
        score += 1
        reasons.append(f"RSI {rsi_now:.0f} madal ja tõuseb")

    # 2. MACD ristus ülespoole = langus lõppenud, tõus algab
    if macd_bullish_cross:
        score += 4
        reasons.append("MACD ristus signaaljoone ÜLES ⭐")

    # 3. MACD histogramm negatiivne aga paraneb = languse jõud nõrgeneb
    if hist_now < 0 and hist_now > hist_prev:
        score += 2
        reasons.append("Languse momentum nõrgeneb — pöördumine tulemas")

    # 4. Bullish divergents = hind langeb aga RSI ei lange = nõrk langus
    if bullish_div:
        score += 3
        reasons.append("Bullish divergents — hind langeb aga RSI mitte ⭐")

    # 5. Hind Bollinger ala riba juures = statistiliselt odavaim tsoon
    if close.iloc[-1] <= bb_lower * 1.02:
        score += 2
        reasons.append("Bollinger ala riba — ajalooliselt odav tsoon")

    # 6. Hind ületas MA20 üles = esimene trendimuutuse kinnitus
    if price_crossed_above_ma20:
        score += 2
        reasons.append("Hind ületas MA20 üles — trend pöördub")

    # 7. Golden Cross = pikaajaline tugevuse kinnitus
    if golden_cross:
        score += 2
        reasons.append("Golden Cross (MA20 > MA50)")

    # 8. Tugev langus + madal RSI = potentsiaalne põhi
    if change_7d is not None and change_7d < -20 and rsi_now < 38:
        score += 2
        reasons.append(f"Tugevalt langenud ({change_7d:.1f}% 7p) + RSI {rsi_now:.0f} — põhi lähedal?")
    elif change_7d is not None and change_7d < -10 and rsi_now < 42:
        score += 1
        reasons.append(f"Langenud {change_7d:.1f}% (7p), RSI {rsi_now:.0f} — odavnenud")

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

            score, rsi, reasons = calculate_bottom_score(df, change_24h, change_7d)

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

    # Filtreeri välja välistatud mündid (skoor = -1) ja sorteeri
    valid   = [r for r in results if r["score"] >= 2]
    invalid = [r for r in results if r["score"] == -1]
    valid.sort(key=lambda x: x["score"], reverse=True)
    top3 = valid[:3]

    now = now_eesti()
    lines = [
        f"🌅 *PÄEVA TÕUSJAD — {now}*",
        f"_Põhjas olevad mündid tõusupotentsiaaliga_",
        "━━━━━━━━━━━━━━━━━━━━━━\n",
    ]

    if not top3:
        lines.append("⏸ Täna pole münte selges põhjapöördumises.")
        lines.append("_Turg vajab puhkust — oota homset analüüsi._")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for idx, r in enumerate(top3):
            medal = medals[idx]
            change_icon = "📈" if r["change_24h"] >= 0 else "📉"
            lines.append(f"{medal} *{r['symbol']}*  —  skoor: {r['score']}")
            lines.append(f"   💵 {r['price']:.4f} EUR  {change_icon} {r['change_24h']:+.1f}% (24h)")
            if r.get("change_7d") is not None:
                icon7 = "📈" if r["change_7d"] >= 0 else "📉"
                lines.append(f"   {icon7} 7 päeva: {r['change_7d']:+.1f}%")
            lines.append(f"   📊 RSI: {r['rsi']} — {'madal ✅' if r['rsi'] < 40 else 'normaalne'}")
            if r["reasons"]:
                for reason in r["reasons"][:3]:
                    lines.append(f"   • {reason}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    # Näita ka järgmisi kandidaate (skoor 1)
    watch = [r for r in valid if r not in top3 and r["score"] >= 1][:4]
    if watch:
        lines.append("👀 *Jälgi ka:*")
        for r in watch:
            lines.append(f"  • {r['symbol']}: RSI {r['rsi']}, skoor {r['score']}")
        lines.append("")

    # Näita tippudes olevaid münte (välistatud)
    topped = [r for r in invalid if r["reasons"]][:4]
    if topped:
        lines.append("🚫 *Välista praegu (liiga kõrgel):*")
        for r in topped:
            lines.append(f"  • {r['symbol']}: {r['reasons'][0]}")
        lines.append("")

    lines.append("⚠️ _Signaalid on informatiivsed, mitte garanteeritud._")
    lines.append("_Kasuta koos oma analüüsiga!_")

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
