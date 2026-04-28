"""
Krüpto Trading Signaalide Süsteem
Analüüsib XRP ja teisi münte ning annab osta/müüa/hoida soovitusi.
Andmed: CoinGecko avalik API (tasuta, ei vaja API võtit)
"""

import requests
import pandas as pd
import ta
import time
import os
from datetime import datetime
from colorama import Fore, Style, init

# GitHub Actions keskkonnas värvid ei tööta - lülita välja
if os.environ.get("CI"):
    class _NoColor:
        def __getattr__(self, _): return ""
    Fore = Style = _NoColor()
else:
    init(autoreset=True)

COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "CG-4Bsct34qk7h5cjj5JRuSzvuJ")
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN",    "8610980001:AAFpSLvBGQmqKjW2UNpnB43vA0ZrzkRzLdE")
TELEGRAM_CHAT_IDS = [
    os.environ.get("TELEGRAM_CHAT_ID", "1665605995"),  # isiklik
    "-5103881140",                                       # grupp Cnoo
]

COINS = {
    "XRP": "ripple",
    "ADA": "cardano",
    "SOL": "solana",
}

STABLECOINS = ["USDT", "USDC", "EURC"]

PORTFOLIO = {
    "XRP": {"amount": None, "buy_price_eur": None},
}


HEADERS = {"x-cg-demo-api-key": COINGECKO_API_KEY}


def _get_with_retry(url: str, params: dict, retries: int = 3) -> requests.Response:
    for attempt in range(retries):
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if r.status_code == 429:
            wait = 10 * (attempt + 1)
            print(f"  {Fore.YELLOW}Rate limit, ootan {wait}s...{Style.RESET_ALL}")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise Exception("Rate limit ületas korduvalt")


def get_ohlc(coin_id: str, days: int = 30) -> pd.DataFrame:
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc"
    params = {"vs_currency": "eur", "days": days}
    r = _get_with_retry(url, params)
    data = r.json()
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def get_price(coin_id: str) -> dict:
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        "ids": coin_id,
        "vs_currencies": "eur",
        "include_24hr_change": "true",
        "include_24hr_vol": "true",
    }
    r = _get_with_retry(url, params)
    return r.json().get(coin_id, {})


def calculate_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]

    rsi_series = ta.momentum.RSIIndicator(close, window=14).rsi()
    rsi_now  = rsi_series.iloc[-1]
    rsi_prev = rsi_series.iloc[-4]   # ~4 periodi tagasi (umbes 1 päev 6h küünlaid)

    macd_obj    = ta.trend.MACD(close)
    macd_line   = macd_obj.macd()
    signal_line = macd_obj.macd_signal()
    hist_series = macd_obj.macd_diff()

    # Kas MACD just ületas signaaljoone? (ristumispunkt)
    macd_bullish_cross = (macd_line.iloc[-2] < signal_line.iloc[-2]) and (macd_line.iloc[-1] >= signal_line.iloc[-1])
    macd_bearish_cross = (macd_line.iloc[-2] > signal_line.iloc[-2]) and (macd_line.iloc[-1] <= signal_line.iloc[-1])

    # Histogramm kasvab või kahaneb (momentum muutus)
    hist_now  = hist_series.iloc[-1]
    hist_prev = hist_series.iloc[-3]
    hist_growing  = hist_now > hist_prev  # negatiivne histogramm muutub vähem negatiivseks = põhi läheneb
    hist_shrinking = hist_now < hist_prev

    ma20_series = ta.trend.SMAIndicator(close, window=20).sma_indicator()
    ma50_series = ta.trend.SMAIndicator(close, window=50).sma_indicator()
    ma20 = ma20_series.iloc[-1]
    ma50 = ma50_series.iloc[-1] if len(ma50_series.dropna()) >= 1 else None

    # Kas hind just ületas MA20? (trendi muutus)
    price_crossed_above_ma20 = (close.iloc[-2] < ma20_series.iloc[-2]) and (close.iloc[-1] >= ma20_series.iloc[-1])
    price_crossed_below_ma20 = (close.iloc[-2] > ma20_series.iloc[-2]) and (close.iloc[-1] <= ma20_series.iloc[-1])

    # Golden/Death cross: MA20 ületab MA50
    golden_cross = False
    death_cross  = False
    if ma50 is not None and len(ma50_series.dropna()) >= 2:
        golden_cross = (ma20_series.iloc[-2] < ma50_series.iloc[-2]) and (ma20_series.iloc[-1] >= ma50_series.iloc[-1])
        death_cross  = (ma20_series.iloc[-2] > ma50_series.iloc[-2]) and (ma20_series.iloc[-1] <= ma50_series.iloc[-1])

    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]

    # RSI divergents: hind teeb uue põhja aga RSI ei tee (bullish divergence = tõus tulemas)
    prices_last_10 = close.iloc[-10:]
    rsi_last_10    = rsi_series.iloc[-10:]
    price_new_low  = close.iloc[-1] < prices_last_10.min() * 1.005
    rsi_not_new_low = rsi_series.iloc[-1] > rsi_last_10.min() * 1.05
    bullish_divergence = price_new_low and rsi_not_new_low and rsi_now < 45

    price_new_high  = close.iloc[-1] > prices_last_10.max() * 0.995
    rsi_not_new_high = rsi_series.iloc[-1] < rsi_last_10.max() * 0.95
    bearish_divergence = price_new_high and rsi_not_new_high and rsi_now > 55

    return {
        "price":               close.iloc[-1],
        "rsi_now":             rsi_now,
        "rsi_prev":            rsi_prev,
        "macd_bullish_cross":  macd_bullish_cross,
        "macd_bearish_cross":  macd_bearish_cross,
        "hist_now":            hist_now,
        "hist_growing":        hist_growing,
        "hist_shrinking":      hist_shrinking,
        "ma20":                ma20,
        "ma50":                ma50,
        "price_crossed_above_ma20": price_crossed_above_ma20,
        "price_crossed_below_ma20": price_crossed_below_ma20,
        "golden_cross":        golden_cross,
        "death_cross":         death_cross,
        "bb_upper":            bb_upper,
        "bb_lower":            bb_lower,
        "bullish_divergence":  bullish_divergence,
        "bearish_divergence":  bearish_divergence,
    }


def generate_signal(ind: dict, change_24h: float) -> dict:
    """
    Eesmärk: tuvastada PÖÖRDEPUNKTID, mitte kirjeldada hetkeseisu.
    OSTA  = langus lõppeb, tõus algab
    MÜÜA  = tõus lõppeb, langus algab -> stablecoini
    HOIDA = pole selget signaali, oota
    """
    buy_score  = 0
    sell_score = 0
    reasons    = []

    rsi      = ind["rsi_now"]
    rsi_prev = ind["rsi_prev"]

    # --- OSTA signaalid (pöördumine ülespoole) ---

    # MACD ristub signaaljoone üle alt üles = kõige tugevam ostuhetk
    if ind["macd_bullish_cross"]:
        buy_score += 3
        reasons.append(f"[OSTA ++] MACD ristus signaaljoone ÜLES — klassikaline ostuhetk")

    # RSI oli ülemüüdud (<30) ja hakkab tõusma = põhi ilmselt möödas
    if rsi_prev < 32 and rsi > rsi_prev + 2:
        buy_score += 3
        reasons.append(f"[OSTA ++] RSI tõuseb ülemüüdud tsoonist ({rsi_prev:.0f} -> {rsi:.0f}) — tõus algab")
    elif rsi < 35 and rsi > rsi_prev:
        buy_score += 2
        reasons.append(f"[OSTA +] RSI {rsi:.0f} ülemüüdud ja tõuseb — põhi lähedal")

    # Bullish divergents: hind langeb aga RSI ei lange = nõrk langus, käändumine tulemas
    if ind["bullish_divergence"]:
        buy_score += 2
        reasons.append(f"[OSTA +] Bullish divergents — hind langeb aga RSI mitte, käändumine tulemas")

    # Hind ületas MA20 alt üles = trendimuutus kinnitub
    if ind["price_crossed_above_ma20"]:
        buy_score += 2
        reasons.append(f"[OSTA +] Hind ületas MA20 ülespoole — trend muutub positiivseks")

    # Golden cross: lühiajaline MA ületas pikaajalise = tugev tõusutrendialgus
    if ind["golden_cross"]:
        buy_score += 3
        reasons.append(f"[OSTA ++] Golden Cross (MA20 ületas MA50) — tugev pikaajaline ostuhetk")

    # MACD histogramm muutub — negatiivne aga kasvab = languse momentum nõrgeneb
    if ind["hist_now"] < 0 and ind["hist_growing"]:
        buy_score += 1
        reasons.append(f"[OSTA] Languse momentum nõrgeneb (MACD hist kasvab) — käändumine võimalik")

    # Hind Bollingeri ala riba tsoonis = statistiliselt odav
    if ind["price"] <= ind["bb_lower"] * 1.015:
        buy_score += 1
        reasons.append(f"[OSTA] Hind Bollinger ala riba juures — statistiliselt odav tsoon")

    # --- MÜÜA signaalid (pöördumine allapoole) ---

    # MACD ristub signaaljoone alla = kõige tugevam müügihetk
    if ind["macd_bearish_cross"]:
        sell_score += 3
        reasons.append(f"[MÜÜA ++] MACD ristus signaaljoone ALLA — müü ja liigu stablecoini")

    # RSI oli üleostetud (>70) ja hakkab langema = tipp ilmselt möödas
    if rsi_prev > 68 and rsi < rsi_prev - 2:
        sell_score += 3
        reasons.append(f"[MÜÜA ++] RSI langeb üleostetud tsoonist ({rsi_prev:.0f} -> {rsi:.0f}) — tipp möödas, müü!")
    elif rsi > 65 and rsi < rsi_prev:
        sell_score += 2
        reasons.append(f"[MÜÜA +] RSI {rsi:.0f} üleostetud ja langeb — tipp lähedal")

    # Bearish divergents: hind tõuseb aga RSI ei tõuse = nõrk tõus, langus tulemas
    if ind["bearish_divergence"]:
        sell_score += 2
        reasons.append(f"[MÜÜA +] Bearish divergents — hind tõuseb aga RSI mitte, langus tulemas")

    # Hind ületas MA20 ülevalt alla = trendimuutus languse suunas
    if ind["price_crossed_below_ma20"]:
        sell_score += 2
        reasons.append(f"[MÜÜA +] Hind murdis MA20 allapoole — trend pöördub negatiivseks, kaalugi müüki")

    # Death cross: lühiajaline MA kukkus pikaajalise alla = tugev langustrendialgus
    if ind["death_cross"]:
        sell_score += 3
        reasons.append(f"[MÜÜA ++] Death Cross (MA20 kukkus alla MA50) — müü stablecoini!")

    # MACD histogramm positiivne aga kahaneb = tõusu momentum nõrgeneb
    if ind["hist_now"] > 0 and ind["hist_shrinking"]:
        sell_score += 1
        reasons.append(f"[MÜÜA] Tõusu momentum nõrgeneb (MACD hist kahaneb) — jälgi tähelepanelikult")

    # Hind Bollingeri üla riba tsoonis = statistiliselt kallis
    if ind["price"] >= ind["bb_upper"] * 0.985:
        sell_score += 1
        reasons.append(f"[MÜÜA] Hind Bollinger üla riba juures — statistiliselt kallis tsoon")

    # --- Otsus ---
    net = buy_score - sell_score

    if not reasons:
        reasons.append(f"RSI {rsi:.0f} — pole selget pöördepunkti, oota signaali")

    if buy_score >= 4 and buy_score > sell_score:
        action = "OSTA KOHE"
        color  = Fore.GREEN + Style.BRIGHT
        emoji  = "[***]"
    elif buy_score >= 2 and buy_score > sell_score:
        action = "VALMIS OSTMA — jälgi kinnitust"
        color  = Fore.GREEN
        emoji  = "[+]"
    elif sell_score >= 4 and sell_score > buy_score:
        action = "MÜÜA -> STABLECOINI"
        color  = Fore.RED + Style.BRIGHT
        emoji  = "[!!!]"
    elif sell_score >= 2 and sell_score > buy_score:
        action = "VALMIS MÜÜMA — jälgi kinnitust"
        color  = Fore.YELLOW
        emoji  = "[-]"
    else:
        action = "OOTA — pole selget signaali"
        color  = Fore.CYAN
        emoji  = "[=]"

    return {"action": action, "buy": buy_score, "sell": sell_score,
            "color": color, "emoji": emoji, "reasons": reasons}


def print_header():
    print(f"\n{Fore.CYAN + Style.BRIGHT}{'='*60}")
    print(f"  KRÜPTO TRADING SIGNAALIDE SÜSTEEM")
    print(f"  {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"{'='*60}{Style.RESET_ALL}\n")


def print_stablecoin_note():
    coins = ", ".join(STABLECOINS)
    print(f"{Fore.WHITE}STABLECOINID (turvasadam): {coins}")
    print(f"   Bybit EU-s saad XRP muua ja hoida USDT/USDC-s kuni")
    print(f"   jargmise ostuvõimaluseni.\n")


def analyze_coin(symbol: str, coin_id: str) -> dict | None:
    print(f"{Fore.WHITE + Style.BRIGHT}--- {symbol} ---")
    try:
        price_data = get_price(coin_id)
        price_eur = price_data.get("eur", 0)
        change_24h = price_data.get("eur_24h_change", 0)

        df = get_ohlc(coin_id, days=30)
        ind = calculate_indicators(df)
        sig = generate_signal(ind, change_24h)

        change_color = Fore.GREEN if change_24h >= 0 else Fore.RED
        print(f"  Hind:      {price_eur:.4f} EUR")
        print(f"  24h muutus: {change_color}{change_24h:+.2f}%{Style.RESET_ALL}")
        print(f"  RSI:       {ind['rsi_now']:.1f}")
        print(f"  MA20:      {ind['ma20']:.4f} EUR")
        if ind["ma50"]:
            print(f"  MA50:      {ind['ma50']:.4f} EUR")

        print(f"\n  {sig['color']}{sig['emoji']}  {sig['action']}{Style.RESET_ALL}")
        print(f"  Osta signaal: {sig['buy']}  |  Müüa signaal: {sig['sell']}")
        print(f"\n  Signaalid:")
        for r in sig["reasons"]:
            print(f"    {r}")

        if "MÜÜA" in sig["action"]:
            print(f"\n  {Fore.RED}>>> Liigu USDT/USDC-sse Bybit EU-s. Oota põhja enne tagasiostmist.{Style.RESET_ALL}")
        elif "OSTA" in sig["action"]:
            print(f"\n  {Fore.GREEN}>>> Hea sisenemine. Osta osade kaupa (nt 30-50% positsioonist).{Style.RESET_ALL}")
        elif "OOTA" in sig["action"]:
            print(f"\n  {Fore.CYAN}>>> Hoia praegune positsioon. Käivita analüüs uuesti 2-4h parast.{Style.RESET_ALL}")

        sig["rsi"]    = f"{ind['rsi_now']:.1f}"
        sig["price"]  = price_eur
        sig["change"] = change_24h
        print()
        return sig
    except Exception as e:
        print(f"  {Fore.RED}Viga andmete laadimisel: {e}{Style.RESET_ALL}")
        print()
        return None


def send_telegram(all_results: list, prices: dict) -> None:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    lines = [f"📊 *KRUPTO SIGNAALID* — {now}"]
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n")

    for symbol, sig in all_results:
        action = sig["action"]
        if "OSTA KOHE" in action:
            icon = "🟢🟢"
        elif "VALMIS OSTMA" in action:
            icon = "🟢"
        elif "STABLECOIN" in action:
            icon = "🔴🔴"
        elif "VALMIS MÜÜMA" in action:
            icon = "🔴"
        else:
            icon = "⚪"

        price_eur, change_24h = prices.get(symbol, (0, 0))
        change_icon = "📈" if change_24h >= 0 else "📉"

        lines.append(f"{icon} *{symbol}* — {price_eur:.4f} EUR {change_icon} {change_24h:+.2f}%")
        lines.append(f"*{action}*")
        lines.append(f"Osta signaal: {sig['buy']} | Müüa signaal: {sig['sell']}")
        lines.append(f"RSI: {sig.get('rsi', '—')}")
        lines.append("")
        lines.append("Signaalid:")
        for r in sig["reasons"]:
            lines.append(f"  • {r}")

        if "MÜÜA" in action:
            lines.append("\n⚠️ _Liigu USDT/USDC-sse Bybit EU-s._")
            lines.append("_Oota põhja enne tagasiostmist._")
        elif "OSTA" in action:
            lines.append("\n✅ _Hea sisenemine._")
            lines.append("_Osta osade kaupa (DCA strateegia)._")
        else:
            lines.append("\n⏸ _Hoia positsioon. Oota selget signaali._")

        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")

    buys  = [(s, r) for s, r in all_results if r["buy"] >= 2 and r["buy"] > r["sell"]]
    sells = [(s, r) for s, r in all_results if r["sell"] >= 2 and r["sell"] > r["buy"]]

    lines.append("")
    if buys:
        lines.append("*KOKKUVÕTE: OSTA* 💰")
        for sym, res in buys:
            lines.append(f"  🟢 {sym}: {res['action']}")
    elif sells:
        lines.append("*KOKKUVÕTE: MÜÜA* ⚠️")
        for sym, res in sells:
            lines.append(f"  🔴 {sym}: {res['action']}")
    else:
        lines.append("*KOKKUVÕTE: OOTA* ⏸")
        lines.append("Pole selget signaali — hoia positsioon.")

    text = "\n".join(lines)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        resp = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "Markdown",
        }, timeout=10)
        if resp.ok:
            print(f"{Fore.GREEN}Telegram saadetud → {chat_id}{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}Telegrami viga ({chat_id}): {resp.text}{Style.RESET_ALL}")


def trending_picks(all_results: list) -> None:
    """Näita kõige paremaid ostusoovitusi praegu."""
    buys = [(s, r) for s, r in all_results if r["buy"] >= 2 and r["buy"] > r["sell"]]
    buys.sort(key=lambda x: x[1]["buy"], reverse=True)

    print(f"{Fore.CYAN + Style.BRIGHT}{'='*60}")
    print(f"  PROFESSIONAALSE TREJDERI KOKKUVÕTE")
    print(f"{'='*60}{Style.RESET_ALL}")

    if buys:
        print(f"\n{Fore.GREEN}Parimad ostusoovitused praegu:{Style.RESET_ALL}")
        for sym, res in buys:
            print(f"  {res['emoji']} {sym}: {res['action']} (osta:{res['buy']} müüa:{res['sell']})")
    else:
        print(f"\n{Fore.YELLOW}Praegu pole selget ostuhetke — oota signaali.{Style.RESET_ALL}")

    sells = [(s, r) for s, r in all_results if r["sell"] >= 2 and r["sell"] > r["buy"]]
    if sells:
        print(f"\n{Fore.RED}Müügisoovitused — liigu stablecoini:{Style.RESET_ALL}")
        for sym, res in sells:
            print(f"  {res['emoji']} {sym}: {res['action']}")

    print(f"\n{Fore.WHITE}Strateegia meeldetuletus:{Style.RESET_ALL}")
    print("  • DCA (Dollar Cost Averaging) — osta väikeste osadena")
    print("  • Müügi korral liigu USDT/USDC peale Bybit EU-s")
    print("  • Ära pane kõike ühe mündi peale")
    print("  • Stop-loss: kaalu müüki kui -15% positsioonist")
    print()


def main():
    print_header()
    print_stablecoin_note()

    all_results = []

    for symbol, coin_id in COINS.items():
        sig = analyze_coin(symbol, coin_id)
        if sig:
            all_results.append((symbol, sig))
        time.sleep(3)  # Demo API: 30 req/min, lühike paus piisab

    trending_picks(all_results)
    if all_results:
        prices = {sym: (sig["price"], sig["change"]) for sym, sig in all_results}
        send_telegram(all_results, prices)


if __name__ == "__main__":
    main()
