"""
strategy.py — Lógica de estrategia idéntica al Backtester PRO HTML
EMAs, evaluación de condiciones, gestión de posición + DCA
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict


# ──────────────────────────────────────────────────────────────
#  CÁLCULO DE INDICADORES
# ──────────────────────────────────────────────────────────────

def calc_ema(closes: List[float], period: int) -> List[float]:
    """EMA Wilder (igual que el JS del backtester)."""
    k = 2.0 / (period + 1)
    out = [float("nan")] * len(closes)
    s, count = 0.0, 0
    for i, c in enumerate(closes):
        if math.isnan(c):
            continue
        if count < period:
            s += c
            count += 1
            if count == period:
                out[i] = s / period
        else:
            out[i] = c * k + out[i - 1] * (1 - k)
    return out


def calc_all_emas(closes: List[float]) -> Dict[int, List[float]]:
    """Calcula todas las EMAs disponibles."""
    periods = [8, 13, 20, 34, 50, 70, 100, 150, 200]
    return {p: calc_ema(closes, p) for p in periods}


# ──────────────────────────────────────────────────────────────
#  EVALUACIÓN DE CONDICIONES (AND lógico)
# ──────────────────────────────────────────────────────────────

def evaluate_conditions(candles, emas: Dict[int, List[float]],
                        conditions: list, i: int) -> bool:
    """
    Evalúa condiciones sobre la vela i.
    Signal detectada en close[i-1], entrada en open[i] — igual que el backtester.
    """
    if i < 2 or not conditions:
        return False

    for cond in conditions:
        ctype  = cond["type"]
        ema_a  = cond["ema_a"]
        ema_b  = cond.get("ema_b", ema_a)

        eA_p2  = emas.get(ema_a, [float("nan")] * (i + 1))[i - 2]
        eA_p1  = emas.get(ema_a, [float("nan")] * (i + 1))[i - 1]
        eB_p2  = emas.get(ema_b, [float("nan")] * (i + 1))[i - 2]
        eB_p1  = emas.get(ema_b, [float("nan")] * (i + 1))[i - 1]
        close_p1 = candles[i - 1]["c"]

        ok = False

        if ctype == "cross_above":
            if any(math.isnan(v) for v in [eA_p2, eA_p1, eB_p2, eB_p1]):
                return False
            ok = (eA_p2 <= eB_p2) and (eA_p1 > eB_p1)

        elif ctype == "cross_below":
            if any(math.isnan(v) for v in [eA_p2, eA_p1, eB_p2, eB_p1]):
                return False
            ok = (eA_p2 >= eB_p2) and (eA_p1 < eB_p1)

        elif ctype == "above":
            if any(math.isnan(v) for v in [eA_p1, eB_p1]):
                return False
            ok = eA_p1 > eB_p1

        elif ctype == "below":
            if any(math.isnan(v) for v in [eA_p1, eB_p1]):
                return False
            ok = eA_p1 < eB_p1

        elif ctype == "price_above_ema":
            if any(math.isnan(v) for v in [eA_p1, close_p1]):
                return False
            ok = close_p1 > eA_p1

        elif ctype == "price_below_ema":
            if any(math.isnan(v) for v in [eA_p1, close_p1]):
                return False
            ok = close_p1 < eA_p1

        if not ok:
            return False

    return True


# ──────────────────────────────────────────────────────────────
#  GESTOR DE POSICIÓN (paper trading)
# ──────────────────────────────────────────────────────────────

@dataclass
class DcaStep:
    idx:   int
    time:  int       # timestamp ms
    price: float
    qty:   float


@dataclass
class Trade:
    entry_time:  int
    entry_idx:   int
    avg_entry:   float
    exit_time:   Optional[int]   = None
    exit_idx:    Optional[int]   = None
    exit_price:  Optional[float] = None
    n_pos:       int             = 1
    initial_qty: float           = 0.0
    total_qty:   float           = 0.0
    pnl_neto:    float           = 0.0
    target:      float           = 0.0
    resultado:   str             = ""
    direction:   str             = "long"
    dca_steps:   List[DcaStep]   = field(default_factory=list)


@dataclass
class PositionState:
    in_trade:        bool  = False
    avg_entry:       float = 0.0
    total_qty:       float = 0.0
    initial_qty:     float = 0.0
    initial_margin:  float = 0.0
    n_pos:           int   = 0
    entry_time:      int   = 0
    entry_idx:       int   = 0
    last_dca_ref:    Optional[float] = None
    total_comm_paid: float = 0.0
    dca_steps:       List[DcaStep] = field(default_factory=list)


class PaperEngine:
    """
    Motor de paper-trading en tiempo real.
    Mantiene estado entre llamadas a process_candle().
    """

    def __init__(self, config: dict):
        self.cfg = config
        self.capital    = config["capital"]
        self.peak_cap   = config["capital"]
        self.max_dd     = 0.0
        self.trades: List[Trade] = []
        self.pos  = PositionState()

        # Estadísticas
        self.total_ops  = 0
        self.wins       = 0
        self.losses     = 0
        self.total_pnl  = 0.0

    # ── Propiedades de config ──────────────────────────
    @property
    def leverage(self):   return self.cfg["leverage"]
    @property
    def risk_pct(self):   return self.cfg["risk_pct"] / 100.0
    @property
    def commission(self): return self.cfg["commission"]
    @property
    def tp_offset(self):  return self.cfg["tp_offset"]
    @property
    def tp_div(self):     return self.cfg["tp_div"]
    @property
    def sl_pct(self):     return self.cfg.get("sl_pct", 0.0) / 100.0
    @property
    def dca_pct(self):    return self.cfg["dca_pct"] / 100.0
    @property
    def dca_mode(self):   return self.cfg["dca_mode"]
    @property
    def dca_ema_filter(self): return self.cfg.get("dca_ema_filter", True)
    @property
    def is_long(self):    return self.cfg["entry_dir"] == "long"

    # ── Proceso de vela cerrada ───────────────────────
    def process_candle(self, candles, emas, i) -> Optional[dict]:
        """
        Procesa la vela i. Devuelve un evento si algo ocurrió:
        {"event": "entry"|"exit"|"dca"|"sl", "trade": ..., "price": ...}
        """
        if self.capital <= 0:
            return None

        c     = candles[i]
        price = c["o"]   # entrada al open de la vela actual
        event = None

        if self.pos.in_trade:
            event = self._check_exit_or_dca(c, price, emas, i)

        if not self.pos.in_trade:
            if evaluate_conditions(candles, emas, self.cfg["conditions"], i):
                event = self._open_position(c, price, i)

        # Actualizar drawdown con equity actual
        if self.pos.in_trade:
            eq = self.capital + self._unrealized_pnl(c["c"])
        else:
            eq = self.capital
        self.peak_cap = max(self.peak_cap, eq)
        if self.peak_cap > 0:
            self.max_dd = max(self.max_dd, (self.peak_cap - eq) / self.peak_cap * 100)

        return event

    def _open_position(self, candle, price, idx) -> dict:
        margin    = self.capital * self.risk_pct
        qty       = (margin * self.leverage) / price
        com       = price * qty * self.commission
        self.capital     -= com

        p = self.pos
        p.in_trade        = True
        p.avg_entry       = price
        p.entry_time      = candle["t"]
        p.entry_idx       = idx
        p.initial_qty     = qty
        p.total_qty       = qty
        p.initial_margin  = margin
        p.n_pos           = 1
        p.last_dca_ref    = None
        p.total_comm_paid = com
        p.dca_steps       = []

        return {
            "event":     "entry",
            "price":     price,
            "qty":       qty,
            "margin":    margin,
            "direction": self.cfg["entry_dir"],
            "capital":   self.capital,
        }

    def _check_exit_or_dca(self, candle, price, emas, i) -> Optional[dict]:
        p = self.pos
        pnl_bruto = (
            (price - p.avg_entry) * p.total_qty if self.is_long
            else (p.avg_entry - price) * p.total_qty
        )
        com_sal  = price * p.total_qty * self.commission
        pnl_neto = pnl_bruto - com_sal - p.total_comm_paid
        target   = (self.leverage * p.n_pos - self.tp_offset) / self.tp_div

        # ── Stop Loss ──────────────────────────────────
        sl_hit = False
        if self.sl_pct > 0:
            move = (
                (price - p.avg_entry) / p.avg_entry if self.is_long
                else (p.avg_entry - price) / p.avg_entry
            )
            if move <= -self.sl_pct:
                sl_hit = True

        # ── Take Profit o SL ──────────────────────────
        if (pnl_bruto - com_sal) >= target or sl_hit:
            self.capital += (pnl_bruto - com_sal)
            trade = Trade(
                entry_time  = p.entry_time,
                entry_idx   = p.entry_idx,
                avg_entry   = p.avg_entry,
                exit_time   = candle["t"],
                exit_idx    = i,
                exit_price  = price,
                n_pos       = p.n_pos,
                initial_qty = p.initial_qty,
                total_qty   = p.total_qty,
                pnl_neto    = pnl_neto,
                target      = target,
                resultado   = ("WIN(SL)" if sl_hit else "WIN") if pnl_neto > 0 else ("LOSS(SL)" if sl_hit else "LOSS"),
                direction   = self.cfg["entry_dir"],
                dca_steps   = list(p.dca_steps),
            )
            self.trades.append(trade)
            self.total_ops += 1
            if pnl_neto > 0:
                self.wins += 1
            else:
                self.losses += 1
            self.total_pnl += pnl_neto

            self._reset_position()
            return {
                "event":    "sl_exit" if sl_hit else "tp_exit",
                "trade":    trade,
                "price":    price,
                "pnl":      pnl_neto,
                "capital":  self.capital,
            }

        # ── DCA ───────────────────────────────────────
        if not sl_hit:
            ref     = p.last_dca_ref if p.last_dca_ref is not None else p.avg_entry
            trigger = ref * (1 - self.dca_pct) if self.is_long else ref * (1 + self.dca_pct)
            dca_triggered = price <= trigger if self.is_long else price >= trigger

            ema20 = emas.get(20, [float("nan")] * (i + 1))[i - 1] if i > 0 else float("nan")
            ema70 = emas.get(70, [float("nan")] * (i + 1))[i - 1] if i > 0 else float("nan")
            ema_ok = (
                not self.dca_ema_filter
                or (not math.isnan(ema20) and not math.isnan(ema70) and ema20 > ema70)
            )

            if dca_triggered and ema_ok:
                added_qty = (
                    max(1e-12, p.n_pos * p.total_qty) if self.dca_mode == "npos"
                    else max(1e-12, p.initial_qty)
                )
                com_dca  = price * added_qty * self.commission
                self.capital    -= com_dca
                p.total_comm_paid += com_dca

                step = DcaStep(idx=i, time=candle["t"], price=price, qty=added_qty)
                p.dca_steps.append(step)
                p.avg_entry = (p.avg_entry * p.total_qty + price * added_qty) / (p.total_qty + added_qty)
                p.total_qty += added_qty
                p.n_pos     += 1
                p.last_dca_ref = price

                return {
                    "event":     "dca",
                    "step":      p.n_pos - 1,
                    "price":     price,
                    "qty":       added_qty,
                    "avg_entry": p.avg_entry,
                    "capital":   self.capital,
                }

        return None

    def _reset_position(self):
        self.pos = PositionState()

    def _unrealized_pnl(self, current_price: float) -> float:
        p = self.pos
        if not p.in_trade:
            return 0.0
        return (
            (current_price - p.avg_entry) * p.total_qty if self.is_long
            else (p.avg_entry - current_price) * p.total_qty
        )

    def summary(self) -> dict:
        wr = (self.wins / self.total_ops * 100) if self.total_ops > 0 else 0.0
        rend = (self.capital - self.cfg["capital"]) / self.cfg["capital"] * 100
        return {
            "capital_inicial": self.cfg["capital"],
            "capital_actual":  round(self.capital, 4),
            "rendimiento_pct": round(rend, 3),
            "operaciones":     self.total_ops,
            "wins":            self.wins,
            "losses":          self.losses,
            "win_rate_pct":    round(wr, 2),
            "max_drawdown_pct": round(self.max_dd, 3),
            "pnl_total":       round(self.total_pnl, 4),
            "en_trade":        self.pos.in_trade,
            "avg_entry":       self.pos.avg_entry if self.pos.in_trade else None,
            "n_pos":           self.pos.n_pos if self.pos.in_trade else 0,
        }
