# Spot DCA Combiner Engine

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Dependencies](https://img.shields.io/badge/Dependencies-aiohttp%20only-brightgreen.svg)]()
[![Hardware](https://img.shields.io/badge/Hardware-Democratized%20(i7--4770%20%2F%201vCPU)-orange.svg)]()

> **A zero-bloat, high-performance quantitative spot buying system powered by Deribit Net GEX (Options Gravity) & Coinbase USD Toxic Flow.**

---

## ⚡ Core Philosophy: Rejecting Retail Traps

Traditional spot accumulators rely on lagging, publicized indicators (RSI, Moving Averages, MACD) or static order book snapshots. In modern adversarial crypto markets, these static metrics act as **liquidity magnets for market makers (MMs)** to engineer fakeouts and harvest retail stop-losses.

The **Spot DCA Combiner Engine** ignores charts and formulates capital allocation strictly upon **microstructural mechanics and physical constraints**:

1. **Options Market Gravity (Deribit Net GEX)**: Options dealers must remain Delta-neutral. In a Net Negative Gamma ($-\Gamma$) regime, dealer hedging is pro-cyclical—every downward price tick *forces* market makers to sell underlying spot/perpetuals into the bid, causing cascading waterfalls. Buying during $-\Gamma$ without absorption proof is mathematically equivalent to catching falling knives.
2. **Institutional Fiat Flow (Coinbase USD)**: Coinbase represents genuine non-leveraged USD fiat custody (the underlying custody rail for major US spot ETFs). When institutional algorithms (TWAP/VWAP) sweep Coinbase books, this represents non-liquidatable, informed "toxic flow" that leads offshore derivatives markets by hundreds of milliseconds.
3. **Counter-Intuitive Exhaustion**: In moments of extreme panic, when mechanical dealer hedging ($-\Gamma$) collides with aggressive Wall Street institutional accumulation, the downward momentum is absorbed. Identifying this collision produces god-tier entry points (*The Ultimate Bottom*).

---

## 🛠 Hardware Democratization & Zero-Bloat Engineering

Most modern quant repositories force heavy installations of Pandas, NumPy, SciPy, and complex ORMs that consume gigabytes of memory and choke older CPUs.

**This engine is built on the principle of extreme hardware democratization:**
- **Zero Heavy Frameworks**: **Strictly NO Pandas. Strictly NO NumPy.** All vector math, moving averages, and sample standard deviations are implemented via pure Python standard library routines and constant-size ring buffers (`collections.deque(maxlen=N)`).
- **Ultra-Low Resource Footprint**: Validated to run seamlessly on legacy hardware such as an **Intel Core i7-4770 with 8GB RAM**, or cheap cloud instances (**1vCPU / 1GB RAM VPS**).
  - **CPU Utilization**: $< 2.0\%$ (single core).
  - **Memory Footprint (RSS)**: $< 60\text{ MB}$.
- **Storage Bus**: Utilizes **SQLite3 in WAL (Write-Ahead Logging) mode** as an in-memory mapped, non-blocking asynchronous event bus. Reading the latest state takes less than **50 microseconds**.
- **100% Free Public APIs**: Completely independent of paid market data tiers. Powered entirely by Deribit Public REST, Coinbase Public WebSocket, and Binance Public WebSocket feeds.

---

## 🧠 The 4-Phase State Machine Architecture

The engine runs a discrete decision cycle at **1Hz** based on two continuous dimensionless factors:
- **Macro Environmental Safety Degree**: $\Phi_{\text{GEX}} \in [0.0, 1.0]$ derived from distance to the Zero-Gamma Flip Price.
- **Tactical Kinetic Ignition Index**: $\Psi_{\text{Flow}} \in [0.0, 1.0]$ derived from Coinbase Taker Buy volume $Z$-Score and Cross-Venue Basis.

```
                    ┌─────────────────────────┐
                    │     STATE 0: FREEZE     │
                    │ (Avalanche Lock: -GEX)  │
                    └───────────┬─────────────┘
                                │
        ┌───────────────────────┴───────────────────────┐
        │ [Standard]: Environmental Unlock              │ [Override]: Capitulation Event
        │ (Φ_GEX >= 0.40)                               │ (Wall St Inflow vs. -Gamma Dump)
        ▼                                               ▼
┌─────────────────────────┐                   ┌─────────────────────────┐
│     STATE 1: WATCH      │                   │   STATE 3: CAPITULATION │
│ (Safe Standby: Awaiting)│                   │  (Ultimate Bottom Reversal)│
└───────────┬─────────────┘                   └───────────┬─────────────┘
            │                                             │
            │ Institutional Sweep Detected                │ Peak Allocation Multiplier (3.0x)
            │ (Ψ_Flow >= 0.70)                            │
            ▼                                             │
┌─────────────────────────┐                               │
│     STATE 2: FIRE       │                               │
│(Resonant Tactical Buy)  │ ◄─────────────────────────────┘
└─────────────────────────┘
```

### 1. `FREEZE` (Avalanche Lock)
- **Condition**: $\Phi_{\text{GEX}} < 0.40$ (Trapped in Negative Gamma zone).
- **Action**: Multiplier = `0.0x`. All buying is blocked to prevent catching falling knives during mechanical dealer sell-offs.

### 2. `WATCH` (Safe Standby)
- **Condition**: $\Phi_{\text{GEX}} \ge 0.40$ and $\Psi_{\text{Flow}} < 0.70$.
- **Action**: Multiplier = `0.0x`. Macro environment is mathematically safe, but institutional fiat buying velocity has not ignited.

### 3. `FIRE` (Resonant Tactical Buy)
- **Condition**: $\Phi_{\text{GEX}} \ge 0.40$ and $\Psi_{\text{Flow}} \ge 0.70$.
- **Action**: Multiplier = `1.0x - 2.0x`. High-confidence spot execution triggered by macro safety plus genuine Coinbase taker sweep.

### 4. `CAPITULATION` (The Ultimate Bottom Override)
- **Condition**: Even if trapped in deep Negative Gamma ($\Phi_{\text{GEX}} < 0.40$), the engine overrides the lock if:
  1. Cross-venue Coinbase premium: $\Delta_{\text{Basis}} > \mu_{\text{Basis}} + 2.5\sigma_{\text{Basis}}$
  2. Coinbase taker buy pulse: $Z_{\text{CB}} > 2.2$
  3. Price curvature decelerates: $\frac{d^2P}{dt^2} \ge 0$
- **Action**: Multiplier = `3.0x` (Maximum Allocation). Captures the physical point where Wall Street fiat absorption halts the mechanical dealer cascade, preceding a violent short squeeze.

---

## 🚀 Quickstart & Installation

### Prerequisites
- Python 3.8 or higher.
- Install the sole dependency (`aiohttp`):
  ```bash
  pip install aiohttp
  ```

### Configuration
Open `spot_dca_combiner.py` and set your preferences in the **GLOBAL STRATEGIC CONFIGURATION** section:
```python
# Base DCA allocation in USDT
BASE_DCA_AMOUNT = 100.0

# Telegram Bot Credentials
TELEGRAM_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
TELEGRAM_CHAT_ID = "-1001234567890"

# Set to True to send a test alert on boot to verify Telegram setup
SIMULATE_TELEGRAM_TEST = False
```

### Execution
Run directly in your terminal:
```bash
python spot_dca_combiner.py
```

To run continuously as a background process on a Linux VPS:
```bash
nohup python spot_dca_combiner.py > /dev/null 2>&1 &
```

---

## 🖥 Terminal Dashboard Preview

When running, the engine provides an interactive ANSI dashboard refreshing at 1Hz without screen flickering:

```
================================================================================
       SPOT DCA COMBINER MATRIX: DERIBIT GEX x COINBASE TOXIC FLOW              
================================================================================
System Time : 2026-11-11 21:20:00  |  Arch: Asyncio Event Loop  |  Bus: SQLite WAL
--------------------------------------------------------------------------------
[1] MACRO GRAVITY (Deribit Options GEX)
• Underlying Spot : $90,450.20            • Zero-Gamma Flip : $91,200.00
• Total Net GEX   : -145.20M              • Macro Safety (Φ): 0.1245 [AVALANCHE LOCK]

[2] MICRO TACTICAL FLOW (Coinbase Spot vs Binance Perp)
• Coinbase Spot   : $90,620.00            • Binance Perp    : $90,410.50
• Cross-Venue Basis: +0.2317%             • Taker Buy Z-Score: +2.85σ
• Binance Funding : -0.0150%              • Ignition Index (Ψ): 0.8120
--------------------------------------------------------------------------------
[3] STATE MACHINE EXECUTION & ALLOCATION
• Decision State  : CAPITULATION
• DCA Multiplier  : 3.00x (Allocation Target: $300.00)
================================================================================
```

---

## ⚠️ Disclaimer

This software is for educational, research, and algorithmic prototyping purposes only. Cryptocurrency trading and spot accumulation involve substantial risk of financial loss. Past statistical edges in market microstructure do not guarantee future performance. Ensure appropriate risk parameters before executing real capital.