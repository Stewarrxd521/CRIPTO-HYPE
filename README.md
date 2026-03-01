# 🤖 HYPE/USD Paper Trading Bot

Bot de paper trading en tiempo real basado en el **Backtester PRO**. Replica la lógica exacta de señales, DCA y gestión de posición, pero operando con datos reales de Binance sin usar dinero real.

---

## 📁 Archivos del proyecto

```
hype_bot/
├── bot.py           # Bucle principal, conexión a Binance, logging
├── strategy.py      # Lógica idéntica al backtester (EMAs, condiciones, DCA, TP/SL)
├── config.json      # Todos los parámetros configurables
├── requirements.txt # Dependencias (solo `requests`)
├── Procfile         # Para Railway / Render
├── railway.toml     # Config de deployment Railway
└── README.md        # Este archivo
```

---

## ⚙️ Configuración (`config.json`)

```json
{
  "symbol":     "HYPEUSDT",        // Par de trading
  "timeframe":  "15m",             // Temporalidad: 1m, 5m, 15m, 1h, 4h...
  "capital":    1000.0,            // Capital virtual inicial en USDT

  "leverage":   7,                 // Apalancamiento
  "risk_pct":   1.0,               // % del capital por operación
  "commission": 0.0005,            // Comisión maker/taker (0.05%)

  "tp_offset":  4.0,               // Fórmula TP: (LEV × nPos − offset) / div
  "tp_div":     2.0,
  "sl_pct":     0.0,               // Stop Loss % (0 = desactivado)

  "dca_pct":    20.0,              // Caída % para ejecutar DCA
  "dca_mode":   "npos",            // "npos" (agresivo) | "fixed" (conservador)
  "dca_ema_filter": true,          // DCA solo si EMA20 > EMA70

  "entry_dir":  "long",            // "long" | "short"

  "conditions": [
    { "type": "cross_below", "ema_a": 20, "ema_b": 70 }
  ],

  "telegram_token":   "",          // Token del bot de Telegram (opcional)
  "telegram_chat_id": ""           // Tu Chat ID de Telegram (opcional)
}
```

### Tipos de condición disponibles

| `type`             | Descripción                        |
|--------------------|------------------------------------|
| `cross_above`      | EMA A cruza por encima de EMA B    |
| `cross_below`      | EMA A cruza por debajo de EMA B    |
| `above`            | EMA A > EMA B (persistente)        |
| `below`            | EMA A < EMA B (persistente)        |
| `price_above_ema`  | Precio de cierre > EMA A           |
| `price_below_ema`  | Precio de cierre < EMA A           |

**Períodos EMA disponibles:** 8, 13, 20, 34, 50, 70, 100, 150, 200

---

## 🚀 Opción 1 — Deploy en Railway (RECOMENDADO, gratis)

### Paso 1: Crea una cuenta en Railway
1. Ve a **https://railway.app** y regístrate con tu cuenta de GitHub (gratis)

### Paso 2: Sube el código a GitHub
1. Crea un repositorio nuevo en GitHub (puede ser privado)
2. Sube todos los archivos de esta carpeta

```bash
git init
git add .
git commit -m "Paper trading bot inicial"
git remote add origin https://github.com/TU_USUARIO/hype-bot.git
git push -u origin main
```

### Paso 3: Crea el proyecto en Railway
1. En Railway → **New Project** → **Deploy from GitHub repo**
2. Selecciona tu repositorio
3. Railway detecta automáticamente el `Procfile` y `requirements.txt`

### Paso 4: Variables de entorno (opcional - para Telegram)
En Railway → tu proyecto → **Variables**:
```
TELEGRAM_TOKEN     = 123456:AABBccDD...
TELEGRAM_CHAT_ID   = -100123456789
```

### Paso 5: Deploy
- Haz clic en **Deploy** → el bot arranca automáticamente
- Ve a **Logs** para ver el output en tiempo real
- Si se cae, Railway lo reinicia solo

> ✅ **Plan gratuito**: 500 horas/mes (~21 días). Para uso continuo considera el plan $5/mes (ilimitado).

---

## 🚀 Opción 2 — Ejecutar localmente

```bash
# 1. Instalar Python 3.10+
# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar config.json según tus preferencias

# 4. Ejecutar
python bot.py
```

---

## 📲 Configurar Telegram (opcional pero muy recomendado)

1. Habla con **@BotFather** en Telegram → `/newbot` → guarda el token
2. Añade el bot a un grupo o envíale un mensaje
3. Abre: `https://api.telegram.org/bot<TOKEN>/getUpdates`
4. Copia el `chat_id` del resultado
5. Pega ambos en `config.json` o como variables de entorno en Railway

El bot te enviará mensajes con:
- 📥 Cada entrada nueva
- ✅/❌ Cada cierre (TP o SL) con PnL
- 🔁 Cada step de DCA
- 📊 Estado del capital

---

## 📊 Archivos generados

| Archivo        | Contenido                                    |
|----------------|----------------------------------------------|
| `bot.log`      | Log completo con timestamps                  |
| `trades.json`  | Historial de todas las operaciones cerradas  |
| `summary.json` | Resumen actualizado en cada vela             |

---

## 🔧 Ajustar la estrategia

Para cambiar parámetros, edita `config.json`. Por ejemplo:

**Estrategia con múltiples condiciones:**
```json
"conditions": [
  { "type": "cross_above", "ema_a": 20, "ema_b": 70 },
  { "type": "price_above_ema", "ema_a": 200 }
]
```

**Estrategia SHORT:**
```json
"entry_dir": "short",
"conditions": [
  { "type": "cross_below", "ema_a": 20, "ema_b": 70 }
]
```

---

## ⚠️ Notas importantes

- Este bot es **solo paper trading** — no ejecuta órdenes reales
- Usa únicamente endpoints públicos de Binance (no necesita API keys)
- La lógica es **idéntica** al Backtester PRO — los resultados son comparables
- HYPEUSDT podría estar en Binance Futures (`fapi`) en lugar de Spot — el bot prueba ambos automáticamente
