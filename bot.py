"""
bot.py — Paper Trading Bot para HYPE/USD en Binance
Conecta a Binance, obtiene velas en tiempo real y simula la estrategia del Backtester PRO.
"""

import json
import logging
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

from strategy import PaperEngine, calc_all_emas


# ══════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("bot")


# ══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════
def load_config(path="config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # Variables de entorno tienen prioridad (para Railway/Render)
    cfg["telegram_token"]   = os.getenv("TELEGRAM_TOKEN",   cfg.get("telegram_token", ""))
    cfg["telegram_chat_id"] = os.getenv("TELEGRAM_CHAT_ID", cfg.get("telegram_chat_id", ""))
    cfg["symbol"]     = os.getenv("SYMBOL",    cfg.get("symbol",    "HYPEUSDT"))
    cfg["timeframe"]  = os.getenv("TIMEFRAME", cfg.get("timeframe", "15m"))
    cfg["capital"]    = float(os.getenv("CAPITAL", cfg.get("capital", 1000)))
    return cfg


# ══════════════════════════════════════════════════════════════
#  BINANCE API (sin auth — solo datos públicos)
# ══════════════════════════════════════════════════════════════
BINANCE_BASE = "https://api.binance.com"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

def fetch_klines(symbol: str, interval: str, limit: int = 300) -> list:
    """Descarga velas OHLCV de Binance Spot. Si falla, intenta Futures."""
    endpoints = [
        f"{BINANCE_BASE}/api/v3/klines",
        f"{BINANCE_FUTURES_BASE}/fapi/v1/klines",
    ]
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    last_err = None
    for url in endpoints:
        try:
            r = requests.get(url, params=params, timeout=10)
            r.raise_for_status()
            raw = r.json()
            candles = []
            for k in raw:
                candles.append({
                    "t": int(k[0]),
                    "o": float(k[1]),
                    "h": float(k[2]),
                    "l": float(k[3]),
                    "c": float(k[4]),
                    "v": float(k[5]),
                    "closed": True,  # Binance excluye la vela activa en el limit
                })
            # La última vela de Binance suele estar abierta; la descartamos
            if candles:
                candles = candles[:-1]
            log.debug(f"Klines de {url.split('/')[2]} — {len(candles)} velas")
            return candles
        except Exception as e:
            last_err = e
            continue
    raise ConnectionError(f"No se pudo obtener klines: {last_err}")


def get_current_price(symbol: str) -> float:
    """Precio actual del ticker."""
    try:
        r = requests.get(
            f"{BINANCE_BASE}/api/v3/ticker/price",
            params={"symbol": symbol}, timeout=5
        )
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception:
        return float("nan")


# ══════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════
def send_telegram(token: str, chat_id: str, text: str):
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"Telegram error: {e}")


def fmt_event_telegram(event: dict, symbol: str, timeframe: str) -> str:
    ev = event["event"]
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if ev == "entry":
        dir_emoji = "🟢 LONG" if event["direction"] == "long" else "🔴 SHORT"
        return (
            f"<b>📥 ENTRADA — {symbol} {timeframe}</b>\n"
            f"{dir_emoji} @ <b>{event['price']:.4f}</b>\n"
            f"Qty: {event['qty']:.4f}  |  Margen: {event['margin']:.2f} USDT\n"
            f"Capital: {event['capital']:.2f} USDT\n"
            f"⏰ {ts}"
        )
    elif ev in ("tp_exit", "sl_exit"):
        t = event["trade"]
        pnl_sign = "+" if event["pnl"] >= 0 else ""
        emoji = "✅" if event["pnl"] >= 0 else "❌"
        tipo  = "TP" if ev == "tp_exit" else "SL"
        return (
            f"<b>{emoji} CIERRE {tipo} — {symbol} {timeframe}</b>\n"
            f"Resultado: <b>{t.resultado}</b>\n"
            f"Entry: {t.avg_entry:.4f}  →  Exit: {t.exit_price:.4f}\n"
            f"PnL: <b>{pnl_sign}{event['pnl']:.4f} USDT</b>\n"
            f"DCA steps: {t.n_pos - 1}  |  Target: {t.target:.2f}\n"
            f"Capital: {event['capital']:.2f} USDT\n"
            f"⏰ {ts}"
        )
    elif ev == "dca":
        return (
            f"<b>🔁 DCA #{event['step']} — {symbol} {timeframe}</b>\n"
            f"@ {event['price']:.4f}  |  Qty: {event['qty']:.4f}\n"
            f"Avg entry: {event['avg_entry']:.4f}\n"
            f"Capital: {event['capital']:.2f} USDT\n"
            f"⏰ {ts}"
        )
    return ""


# ══════════════════════════════════════════════════════════════
#  PERSISTENCIA DE TRADES (JSON)
# ══════════════════════════════════════════════════════════════
TRADES_FILE = Path("trades.json")

def save_trade(trade, symbol: str, timeframe: str):
    existing = []
    if TRADES_FILE.exists():
        try:
            existing = json.loads(TRADES_FILE.read_text())
        except Exception:
            existing = []
    record = {
        "symbol":      symbol,
        "timeframe":   timeframe,
        "entry_time":  datetime.fromtimestamp(trade.entry_time / 1000, tz=timezone.utc).isoformat(),
        "exit_time":   datetime.fromtimestamp(trade.exit_time  / 1000, tz=timezone.utc).isoformat(),
        "direction":   trade.direction,
        "avg_entry":   round(trade.avg_entry,  6),
        "exit_price":  round(trade.exit_price, 6),
        "n_pos":       trade.n_pos,
        "initial_qty": round(trade.initial_qty, 6),
        "pnl_neto":    round(trade.pnl_neto, 6),
        "target":      round(trade.target, 4),
        "resultado":   trade.resultado,
        "dca_steps": [
            {"price": round(s.price, 6), "qty": round(s.qty, 6)}
            for s in trade.dca_steps
        ],
    }
    existing.append(record)
    TRADES_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False))


def save_summary(summary: dict):
    Path("summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )


# ══════════════════════════════════════════════════════════════
#  UTILIDADES DE TIEMPO
# ══════════════════════════════════════════════════════════════
INTERVAL_MS = {
    "1m":   60_000, "3m":  180_000, "5m":  300_000,
    "15m": 900_000, "30m": 1_800_000,
    "1h":  3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

def ms_until_next_close(interval: str) -> float:
    """Milisegundos hasta el próximo cierre de vela."""
    period = INTERVAL_MS.get(interval, 900_000)
    now_ms = int(time.time() * 1000)
    next_close = ((now_ms // period) + 1) * period
    return max(0, next_close - now_ms)


def fmt_duration(ms: float) -> str:
    s = int(ms / 1000)
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


# ══════════════════════════════════════════════════════════════
#  BUCLE PRINCIPAL
# ══════════════════════════════════════════════════════════════
def run():
    log.info("=" * 60)
    log.info("  HYPE/USD Paper Trading Bot — Backtester PRO Engine")
    log.info("=" * 60)

    cfg = load_config()
    symbol    = cfg["symbol"]
    timeframe = cfg["timeframe"]
    tg_token  = cfg.get("telegram_token", "")
    tg_chat   = cfg.get("telegram_chat_id", "")

    log.info(f"Par: {symbol}  |  TF: {timeframe}  |  Capital: {cfg['capital']} USDT")
    log.info(f"Estrategia: {cfg['entry_dir'].upper()} | LEV×{cfg['leverage']} | RISK {cfg['risk_pct']}%")
    log.info(f"TP: (lev×nPos − {cfg['tp_offset']}) / {cfg['tp_div']}  |  DCA: {cfg['dca_pct']}%  {cfg['dca_mode']}")
    log.info(f"Condiciones ({len(cfg['conditions'])}): {cfg['conditions']}")
    log.info(f"Telegram: {'✓ activo' if tg_token else '✗ no configurado'}")
    log.info("-" * 60)

    engine = PaperEngine(cfg)

    # ── Warm-up: carga velas históricas para inicializar EMAs ──
    log.info("Cargando historial para inicializar indicadores...")
    try:
        initial_candles = fetch_klines(symbol, timeframe, limit=300)
        closes = [c["c"] for c in initial_candles]
        emas   = calc_all_emas(closes)
        log.info(f"  {len(initial_candles)} velas cargadas. EMAs inicializadas.")
    except Exception as e:
        log.error(f"Error en warm-up: {e}")
        sys.exit(1)

    # ── Marcar las velas históricas como ya procesadas ──
    last_processed_t = initial_candles[-1]["t"] if initial_candles else 0
    processed_candle_times = set(c["t"] for c in initial_candles)

    if tg_token:
        send_telegram(
            tg_token, tg_chat,
            f"🤖 <b>Bot iniciado</b>\n{symbol} {timeframe} | Capital: {cfg['capital']} USDT\n"
            f"Estrategia cargada — esperando señales..."
        )

    log.info("Bot activo. Esperando cierre de vela...")
    log.info("")

    candles_buffer = list(initial_candles)

    while True:
        try:
            wait_ms = ms_until_next_close(timeframe)
            if wait_ms > 5_000:
                log.info(f"⏳  Próximo cierre en {fmt_duration(wait_ms)}")
                time.sleep(min(wait_ms / 1000 - 3, 55))   # sleep casi hasta el cierre
                continue

            # Esperar el cierre exacto + 2s de buffer
            time.sleep(wait_ms / 1000 + 2)

            # ── Obtener velas nuevas ──────────────────────
            new_candles = fetch_klines(symbol, timeframe, limit=300)
            if not new_candles:
                log.warning("Sin velas recibidas, reintentando...")
                time.sleep(5)
                continue

            # Detectar velas nuevas (no procesadas aún)
            fresh = [c for c in new_candles if c["t"] not in processed_candle_times]
            if not fresh:
                log.debug("Sin velas nuevas todavía, esperando...")
                time.sleep(3)
                continue

            # Actualizar buffer
            candles_buffer = new_candles
            closes = [c["c"] for c in candles_buffer]
            emas   = calc_all_emas(closes)
            n      = len(candles_buffer)

            for candle in fresh:
                processed_candle_times.add(candle["t"])
                idx = next((j for j, c in enumerate(candles_buffer) if c["t"] == candle["t"]), None)
                if idx is None or idx < 2:
                    continue

                ts_str = datetime.fromtimestamp(
                    candle["t"] / 1000, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M")

                ema20 = emas.get(20, [float("nan")] * (idx + 1))[idx - 1]
                ema70 = emas.get(70, [float("nan")] * (idx + 1))[idx - 1]
                ema200 = emas.get(200, [float("nan")] * (idx + 1))[idx - 1]

                log.info(
                    f"🕯  {ts_str}  C={candle['c']:.4f}  "
                    f"EMA20={ema20:.4f}  EMA70={ema70:.4f}  EMA200={ema200:.4f}"
                )

                event = engine.process_candle(candles_buffer, emas, idx)

                if event:
                    ev = event["event"]

                    if ev == "entry":
                        log.info(
                            f"  📥 ENTRADA {event['direction'].upper()} @ {event['price']:.4f} "
                            f"| Qty: {event['qty']:.4f} | Margen: {event['margin']:.2f}"
                        )
                        send_telegram(tg_token, tg_chat,
                                      fmt_event_telegram(event, symbol, timeframe))

                    elif ev in ("tp_exit", "sl_exit"):
                        t   = event["trade"]
                        pnl = event["pnl"]
                        emoji = "✅" if pnl >= 0 else "❌"
                        tipo  = "TP" if ev == "tp_exit" else "SL"
                        log.info(
                            f"  {emoji} CIERRE {tipo}: {t.resultado}  "
                            f"PnL={pnl:+.4f}  DCA×{t.n_pos-1}  "
                            f"Capital={event['capital']:.2f}"
                        )
                        save_trade(t, symbol, timeframe)
                        send_telegram(tg_token, tg_chat,
                                      fmt_event_telegram(event, symbol, timeframe))

                    elif ev == "dca":
                        log.info(
                            f"  🔁 DCA #{event['step']} @ {event['price']:.4f} "
                            f"| Avg: {event['avg_entry']:.4f} | Qty: {event['qty']:.4f}"
                        )
                        send_telegram(tg_token, tg_chat,
                                      fmt_event_telegram(event, symbol, timeframe))

                # Estado actual de la posición abierta
                if engine.pos.in_trade:
                    current_price = candle["c"]
                    upnl = engine._unrealized_pnl(current_price)
                    log.info(
                        f"    ↳ Posición abierta: avg={engine.pos.avg_entry:.4f} "
                        f"nPos={engine.pos.n_pos} uPnL={upnl:+.4f}"
                    )

            # ── Resumen periódico ─────────────────────────
            summary = engine.summary()
            save_summary(summary)

            rend = summary["rendimiento_pct"]
            log.info(
                f"  📊 Capital: {summary['capital_actual']:.2f} ({'+' if rend>=0 else ''}{rend:.2f}%)  "
                f"Ops: {summary['operaciones']}  WR: {summary['win_rate_pct']:.1f}%  "
                f"MaxDD: {summary['max_drawdown_pct']:.2f}%"
            )
            log.info("")

        except KeyboardInterrupt:
            log.info("Bot detenido por el usuario.")
            summary = engine.summary()
            log.info(f"Resumen final: {json.dumps(summary, indent=2)}")
            if tg_token:
                send_telegram(
                    tg_token, tg_chat,
                    f"🛑 <b>Bot detenido</b>\n"
                    f"Capital final: {summary['capital_actual']:.2f} USDT "
                    f"({'+' if summary['rendimiento_pct']>=0 else ''}{summary['rendimiento_pct']:.2f}%)\n"
                    f"Ops: {summary['operaciones']}  WR: {summary['win_rate_pct']:.1f}%"
                )
            sys.exit(0)

        except requests.exceptions.ConnectionError:
            log.error("Sin conexión a Binance. Reintentando en 30s...")
            time.sleep(30)

        except Exception as e:
            log.error(f"Error inesperado: {e}", exc_info=True)
            time.sleep(15)


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    run()
