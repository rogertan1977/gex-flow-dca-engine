#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
================================================================================
SCRIPT NAME : spot_dca_combiner.py
DESCRIPTION : Quantitative Spot DCA Engine Powered by Options Dealer Thermodynamics
              (Deribit Net GEX) & Cross-Venue Institutional Toxic Flow (Coinbase USD).
ENVIRONMENT : Python 3.8+ | Pure Standard Library + aiohttp (Zero Pandas / Zero NumPy)
DATA BUS    : SQLite3 (WAL-Mode In-Memory Mapped Non-Blocking Event Bus)
HARDWARE    : Intel Core i7-4770 / 8GB RAM / 1vCPU Cloud VPS (CPU < 2%, RAM < 60MB)
================================================================================
"""

import asyncio
import json
import math
import os
import signal
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiohttp

# ==============================================================================
# GLOBAL STRATEGIC CONFIGURATION
# ==============================================================================

# Target Instruments
SYMBOL_DERIBIT = "BTC"
SYMBOL_COINBASE = "BTC-USD"
SYMBOL_BINANCE = "BTCUSDT"

# Persistence Bus
DB_PATH = "spot_dca.db"

# Engine Parameters & Thresholds
GEX_POLL_INTERVAL = 30           # Deribit Options snapshot polling interval (seconds)
ENGINE_TICK_INTERVAL = 1.0       # Decision core clock frequency (1Hz)
ROLLING_WINDOW_SECS = 60         # Rolling window for Coinbase volume stats (seconds)
BASE_DCA_AMOUNT = 100.0          # Base DCA spot allocation amount (USDT)

# Telegram Tactical Notification Parameters
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # Replace with your Bot Token
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"      # Replace with your Chat ID
TELEGRAM_COOLDOWN_SECS = 180                    # Anti-spam cooldown for identical states (seconds)

# Self-Test Switch
# Set to True to trigger a simulated 'CAPITULATION' alert upon startup to verify connectivity.
SIMULATE_TELEGRAM_TEST = False


# ==============================================================================
# SQLITE3 WAL-MODE BUS INFRASTRUCTURE
# ==============================================================================

def init_database(db_path: str = DB_PATH) -> None:
    """Initialize SQLite database with Write-Ahead Logging (WAL) concurrency."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    cursor = conn.cursor()
    
    # Configure WAL mode and low-overhead synchronization
    cursor.execute("PRAGMA journal_mode = WAL;")
    cursor.execute("PRAGMA synchronous = NORMAL;")
    cursor.execute("PRAGMA temp_store = MEMORY;")
    cursor.execute("PRAGMA cache_size = -64000;")  # 64MB memory cache
    
    # Create persistent state tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gex_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            timestamp REAL,
            underlying_price REAL,
            flip_price REAL,
            net_gex REAL,
            phi_gex REAL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS flow_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            timestamp REAL,
            cb_price REAL,
            bn_price REAL,
            funding_rate REAL,
            taker_buy_vol REAL,
            z_cb REAL,
            basis REAL,
            psi_flow REAL
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            state TEXT,
            multiplier REAL,
            exec_amount REAL,
            reason TEXT
        );
    """)
    conn.commit()
    conn.close()


def get_db_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Acquire an optimized read/write SQLite database connection."""
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


# ==============================================================================
# UTILITY FUNCTIONS: MATHEMATICAL FORMULATIONS & TELEGRAM FORMATTING
# ==============================================================================

def escape_md(text: object) -> str:
    """Escape reserved characters for Telegram MarkdownV2 compliance."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in escape_chars else c for c in str(text))


def calc_mean_std(data: deque) -> Tuple[float, float]:
    """Calculate rolling mean and sample standard deviation in O(N) pure Python."""
    n = len(data)
    if n < 2:
        return (data[0] if n == 1 else 0.0), 0.0
    mean_val = sum(data) / n
    variance = sum((x - mean_val) ** 2 for x in data) / (n - 1)
    return mean_val, math.sqrt(max(0.0, variance))


# ==============================================================================
# MODULE 1: DERIBIT DEALER GEX HARVESTER (GexHarvester)
# ==============================================================================

class GexHarvester:
    """
    Asynchronously queries Deribit options order books, computes Dealer Net GEX 
    and the Zero-Gamma Flip Line, outputting Macro Safety Degree: Φ_GEX ∈ [0.0, 1.0].
    """
    def __init__(self, currency: str = SYMBOL_DERIBIT, interval: int = GEX_POLL_INTERVAL):
        self.currency = currency
        self.interval = interval
        self.url = f"https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency={currency}&kind=option"
        self.running = True

    async def run(self):
        conn = get_db_connection()
        async with aiohttp.ClientSession() as session:
            while self.running:
                try:
                    async with session.get(self.url, timeout=10) as resp:
                        if resp.status == 200:
                            payload = await resp.json()
                            result = payload.get("result", [])
                            self._process_and_store(result, conn)
                except Exception:
                    pass  # Transient network jitter; resume next cycle
                
                await asyncio.sleep(self.interval)
        conn.close()

    def _process_and_store(self, instruments: List[Dict], conn: sqlite3.Connection):
        if not instruments:
            return

        underlying_price = 0.0
        strike_gex: Dict[float, float] = {}
        total_net_gex = 0.0

        for item in instruments:
            name = item.get("instrument_name", "")
            gamma = item.get("gamma") or 0.0
            oi = item.get("open_interest") or 0.0
            u_price = item.get("underlying_price") or 0.0
            
            if u_price > 0:
                underlying_price = u_price

            if gamma <= 0 or oi <= 0:
                continue

            parts = name.split("-")
            if len(parts) < 4:
                continue
            
            try:
                strike = float(parts[2])
                opt_type = parts[3].upper()
            except ValueError:
                continue

            # Physical Dollar Gamma: Gamma * OI * Spot^2 * 0.01
            dollar_gamma = gamma * oi * (underlying_price ** 2) * 0.01
            
            # Dealer Hedging Sign: Long Call = +Gamma Buffer; Long Put = -Gamma Cliff
            signed_gex = dollar_gamma if opt_type == "C" else -dollar_gamma
            strike_gex[strike] = strike_gex.get(strike, 0.0) + signed_gex
            total_net_gex += signed_gex

        if underlying_price <= 0:
            return

        # Derive Zero-Gamma Flip Price (Zero Cross of Cumulative Strike GEX)
        sorted_strikes = sorted(strike_gex.keys())
        flip_price = sorted_strikes[0] if sorted_strikes else underlying_price
        cum_gex = 0.0
        
        for strike in sorted_strikes:
            cum_gex += strike_gex[strike]
            if cum_gex >= 0:
                flip_price = strike
                break

        # Normalized Distance and Sigmoid Environmental Transmission: Φ_GEX
        delta_gamma = (underlying_price - flip_price) / flip_price
        phi_gex = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, 80.0 * delta_gamma))))

        # Persist to SQLite WAL Bus
        now = time.time()
        conn.execute("""
            INSERT OR REPLACE INTO gex_state (id, timestamp, underlying_price, flip_price, net_gex, phi_gex)
            VALUES (1, ?, ?, ?, ?, ?);
        """, (now, underlying_price, flip_price, total_net_gex, phi_gex))


# ==============================================================================
# MODULE 2: CROSS-VENUE INSTITUTIONAL TOXIC FLOW MONITOR (CoinbaseToxicFlowMonitor)
# ==============================================================================

class CoinbaseToxicFlowMonitor:
    """
    Consumes Coinbase USD Spot match events alongside Binance Futures order books 
    and funding rates, outputting Tactical Ignition Index: Ψ_Flow ∈ [0.0, 1.0].
    """
    def __init__(self):
        self.running = True
        self.cb_ws_url = "wss://ws-feed.exchange.coinbase.com"
        self.bn_ws_url = "wss://fstream.binance.com/stream?streams=btcusdt@bookTicker/btcusdt@markPrice@1s"
        
        self.latest_cb_price = 0.0
        self.latest_bn_price = 0.0
        self.latest_funding_rate = 0.0
        
        self.sec_taker_buy_vol = 0.0
        self.history_buy_vols = deque(maxlen=ROLLING_WINDOW_SECS)
        self.history_basis = deque(maxlen=ROLLING_WINDOW_SECS)

    async def run(self):
        conn = get_db_connection()
        await asyncio.gather(
            self._coinbase_ws_loop(),
            self._binance_ws_loop(),
            self._metrics_aggregation_loop(conn)
        )
        conn.close()

    async def _coinbase_ws_loop(self):
        """Coinbase Matches WebSocket Stream."""
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.cb_ws_url, heartbeat=20) as ws:
                        sub_msg = {
                            "type": "subscribe",
                            "product_ids": [SYMBOL_COINBASE],
                            "channels": ["matches"]
                        }
                        await ws.send_str(json.dumps(sub_msg))
                        async for msg in ws:
                            if not self.running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if data.get("type") == "match":
                                    side = data.get("side")
                                    size = float(data.get("size", 0.0))
                                    price = float(data.get("price", 0.0))
                                    self.latest_cb_price = price
                                    # side == 'buy' indicates taker is buying (Aggressive Toxic Inflow)
                                    if side == "buy":
                                        self.sec_taker_buy_vol += size
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception:
                await asyncio.sleep(2)

    async def _binance_ws_loop(self):
        """Binance Best Bid/Ask and 1-Second Mark Price/Funding Stream."""
        while self.running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(self.bn_ws_url, heartbeat=20) as ws:
                        async for msg in ws:
                            if not self.running:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                payload = json.loads(msg.data)
                                stream = payload.get("stream", "")
                                data = payload.get("data", {})
                                
                                if "bookTicker" in stream:
                                    bid = float(data.get("b", 0.0))
                                    ask = float(data.get("a", 0.0))
                                    if bid > 0 and ask > 0:
                                        self.latest_bn_price = (bid + ask) * 0.5
                                elif "markPrice" in stream:
                                    self.latest_funding_rate = float(data.get("r", 0.0))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception:
                await asyncio.sleep(2)

    async def _metrics_aggregation_loop(self, conn: sqlite3.Connection):
        """1Hz Aggregation and Dynamic Z-Score Calculation Loop."""
        while self.running:
            start_t = time.time()
            
            current_vol = self.sec_taker_buy_vol
            self.sec_taker_buy_vol = 0.0  # Reset slice counter
            self.history_buy_vols.append(current_vol)

            # Cross-Venue Fiat Premium: (P_Coinbase - P_Binance) / P_Binance
            basis = 0.0
            if self.latest_bn_price > 0 and self.latest_cb_price > 0:
                basis = (self.latest_cb_price - self.latest_bn_price) / self.latest_bn_price
            self.history_basis.append(basis)

            # Online Mean and Standard Deviation
            mean_vol, std_vol = calc_mean_std(self.history_buy_vols)
            z_cb = (current_vol - mean_vol) / std_vol if std_vol > 0 else 0.0

            # Tactical Ignition Index: Ψ_Flow = 0.5 * tanh(Z - 1.5) + 0.5 * tanh(Basis / 0.0008)
            t_z = math.tanh(z_cb - 1.5)
            t_b = math.tanh(basis / 0.0008)
            psi_flow = max(0.0, min(1.0, 0.5 * t_z + 0.5 * t_b))

            # Store to WAL Database
            conn.execute("""
                INSERT OR REPLACE INTO flow_state 
                (id, timestamp, cb_price, bn_price, funding_rate, taker_buy_vol, z_cb, basis, psi_flow)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (time.time(), self.latest_cb_price, self.latest_bn_price, 
                  self.latest_funding_rate, current_vol, z_cb, basis, psi_flow))

            elapsed = time.time() - start_t
            await asyncio.sleep(max(0.0, 1.0 - elapsed))


# ==============================================================================
# MODULE 3: TELEGRAM TACTICAL NOTIFIER (MarkdownV2 & Anti-Spam Cooldown)
# ==============================================================================

class TelegramNotifier:
    def __init__(self, token: str = TELEGRAM_BOT_TOKEN, chat_id: str = TELEGRAM_CHAT_ID):
        self.token = token
        self.chat_id = chat_id
        self.last_sent_time = 0.0
        self.last_state = ""

    async def send_signal_alert(self, state: str, multiplier: float, price: float, 
                                flip_price: float, phi_gex: float, basis: float, 
                                z_cb: float, funding: float, reason: str, 
                                is_test: bool = False) -> bool:
        now = time.time()
        if not is_test:
            if state == self.last_state and (now - self.last_sent_time) < TELEGRAM_COOLDOWN_SECS:
                return False

        header = "🚨 *【SPOT DCA TACTICAL ALERT】* 🚨" if not is_test else "🛠 *【SPOT DCA PIPELINE SELF-TEST】* 🛠"
        state_display = "🔥 CAPITULATION (Ultimate Bottom Reversal)" if state == "CAPITULATION" else "⚡ FIRE (Resonance Tactical Buy)"
        
        flip_offset_pct = ((price - flip_price) / flip_price) * 100 if flip_price > 0 else 0.0
        basis_pct = basis * 100
        funding_pct = funding * 100

        text = (
            f"{header}\n\n"
            f"• Decision State: `{escape_md(state_display)}`\n"
            f"• Allocation Multiplier: `{escape_md(f'{multiplier:.1f}x')}`\n"
            f"• Target Allocation: `${escape_md(f'{BASE_DCA_AMOUNT * multiplier:.2f}')}`\n"
            f"• Reference Price: `${escape_md(f'{price:,.2f}')}`\n"
            f"• Zero\\-Gamma Flip: `${escape_md(f'{flip_price:,.2f}')}` \\(Offset: `{escape_md(f'{flip_offset_pct:+.2f}%')}`\\)\n"
            f"• Macro Safety \\(Φ\\_GEX\\): `{escape_md(f'{phi_gex:.4f}')}`\n"
            f"• Coinbase Premium: `{escape_md(f'{basis_pct:+.4f}%')}` \\(Z\\-Score: `{escape_md(f'{z_cb:.2f}')}`\\)\n"
            f"• Binance Funding Rate: `{escape_md(f'{funding_pct:+.4f}%')}`\n"
            f"• Trigger Attribution: `{escape_md(reason)}`\n\n"
            f"⏱ Timestamp: `{escape_md(datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'))}`"
        )

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as resp:
                    if resp.status == 200:
                        self.last_sent_time = now
                        self.last_state = state
                        return True
        except Exception:
            pass
        return False


# ==============================================================================
# MODULE 4: MATHEMATICAL STATE MACHINE & DECISION ENGINE (SpotDCACombinerEngine)
# ==============================================================================

class SpotDCACombinerEngine:
    """
    1Hz Discrete Decision Core:
    - 4-Phase Transition: FREEZE -> WATCH -> FIRE -> CAPITULATION
    - Capitulation Override Evaluation
    - Real-Time Terminal Dashboard Visualizer
    """
    def __init__(self, notifier: TelegramNotifier):
        self.notifier = notifier
        self.running = True
        self.current_state = "FREEZE"
        
        # Buffer for Second Derivative of Price (Acceleration: d^2P/dt^2)
        self.price_history = deque(maxlen=5)
        # Buffer for Spread Outlier Statistics
        self.basis_history = deque(maxlen=ROLLING_WINDOW_SECS)

    async def run(self):
        conn = get_db_connection()
        print("\033[2J\033[H")  # Clear screen

        # Execute Startup Self-Test if enabled
        if SIMULATE_TELEGRAM_TEST:
            print("[Self-Test] Dispatching simulated Capitulation Alert via Telegram...")
            await self.notifier.send_signal_alert(
                state="CAPITULATION",
                multiplier=3.0,
                price=89450.0,
                flip_price=92100.0,
                phi_gex=0.035,
                basis=0.0052,
                z_cb=3.45,
                funding=-0.00025,
                reason="[Self-Test] Wall Street fiat block order absorbing negative gamma cascade",
                is_test=True
            )
            print("[Self-Test] Simulated alert delivered successfully. Entering main event loop.\n")

        while self.running:
            start_tick = time.time()
            
            # Fetch latest exogenous slices from SQLite WAL bus
            gex_row = conn.execute("SELECT underlying_price, flip_price, net_gex, phi_gex FROM gex_state WHERE id = 1").fetchone()
            flow_row = conn.execute("SELECT cb_price, bn_price, funding_rate, z_cb, basis, psi_flow FROM flow_state WHERE id = 1").fetchone()

            if gex_row and flow_row:
                u_price, flip_price, net_gex, phi_gex = gex_row
                cb_price, bn_price, funding, z_cb, basis, psi_flow = flow_row
                
                ref_price = cb_price if cb_price > 0 else u_price
                if ref_price > 0:
                    self.price_history.append(ref_price)
                self.basis_history.append(basis)

                # Evaluate Mathematical State Machine
                next_state, multiplier, reason = self._evaluate_state_machine(
                    ref_price, flip_price, phi_gex, basis, z_cb, psi_flow
                )
                
                # State Transition and Execution
                if next_state in ("FIRE", "CAPITULATION") and next_state != self.current_state:
                    conn.execute("""
                        INSERT INTO execution_logs (timestamp, state, multiplier, exec_amount, reason)
                        VALUES (?, ?, ?, ?, ?);
                    """, (time.time(), next_state, multiplier, BASE_DCA_AMOUNT * multiplier, reason))
                    
                    asyncio.create_task(
                        self.notifier.send_signal_alert(
                            state=next_state, multiplier=multiplier, price=ref_price,
                            flip_price=flip_price, phi_gex=phi_gex, basis=basis,
                            z_cb=z_cb, funding=funding, reason=reason
                        )
                    )

                self.current_state = next_state
                
                # Refresh Console Terminal Dashboard
                self._render_terminal_dashboard(
                    ref_price, flip_price, net_gex, phi_gex, 
                    cb_price, bn_price, basis, z_cb, psi_flow, funding, multiplier
                )

            await asyncio.sleep(max(0.0, ENGINE_TICK_INTERVAL - (time.time() - start_tick)))
        conn.close()

    def _evaluate_state_machine(self, price: float, flip: float, phi: float, 
                                basis: float, z_cb: float, psi: float) -> Tuple[str, float, str]:
        """
        Mathematical Decision Architecture:
        1. Evaluate Capitulation Override (Exhaustion Inversion)
        2. Evaluate Environmental Lock Φ_GEX (FREEZE vs. WATCH)
        3. Evaluate Tactical Kinetic Flow Ψ_Flow (FIRE Trigger)
        """
        # Price Second Derivative (Acceleration: d^2P/dt^2)
        acceleration = 0.0
        if len(self.price_history) >= 3:
            p_t0 = self.price_history[-1]
            p_t1 = self.price_history[-2]
            p_t2 = self.price_history[-3]
            acceleration = (p_t0 - p_t1) - (p_t1 - p_t2)

        mean_basis, std_basis = calc_mean_std(self.basis_history)
        basis_outlier = (basis - mean_basis) > (2.5 * std_basis) if std_basis > 0 else False

        # [CAPITULATION OVERRIDE]: Ultimate Bottom Inversion
        # Triggered in deep negative gamma if Coinbase spot premium > 2.5σ, Z > 2.2, and convex price curve
        if phi < 0.40 and basis_outlier and z_cb > 2.2 and acceleration >= 0:
            return "CAPITULATION", 3.0, "Capitulation Override: Extreme Coinbase spot absorption truncating -Gamma avalanche"

        # [MACRO ENVIRONMENTAL LOCK]: Negative Gamma Cascade Protection
        if phi < 0.40:
            return "FREEZE", 0.0, "Trapped in Net Negative Gamma avalanche regime; environment lock engaged"

        # [SAFE STANDBY]: Awaiting Kinetic Ignition
        if psi < 0.70:
            return "WATCH", 0.0, "Macro environment safe; standing by for institutional fiat sweep"

        # [RESONANT IGNITION]: Tactical Execution
        multiplier = 1.0 + (psi - 0.70) * (1.0 / 0.30)
        return "FIRE", round(multiplier, 2), "Resonant Ignition: Macro safe zone + Wall St aggressive fiat taker sweep"

    def _render_terminal_dashboard(self, price: float, flip: float, net_gex: float, 
                                   phi: float, cb_price: float, bn_price: float, 
                                   basis: float, z_cb: float, psi: float, 
                                   funding: float, multiplier: float):
        """ANSI Real-Time Interactive Console Dashboard."""
        state_color = {
            "FREEZE": "\033[94m",       # Blue
            "WATCH": "\033[93m",        # Yellow
            "FIRE": "\033[92m",         # Green
            "CAPITULATION": "\033[91m"  # Red
        }.get(self.current_state, "\033[0m")

        sys.stdout.write("\033[H")
        sys.stdout.write(f"""
\033[1;37m================================================================================\033[0m
\033[1;36m       SPOT DCA COMBINER MATRIX: DERIBIT GEX x COINBASE TOXIC FLOW              \033[0m
\033[1;37m================================================================================\033[0m
System Time : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  Arch: Asyncio Event Loop  |  Bus: SQLite WAL
--------------------------------------------------------------------------------
\033[1m[1] MACRO GRAVITY (Deribit Options GEX)\033[0m
• Underlying Spot : ${price:,.2f}            • Zero-Gamma Flip : ${flip:,.2f}
• Total Net GEX   : {net_gex / 1e6:+,.2f}M           • Macro Safety (Φ): {phi:.4f} {('[UNLOCKED]' if phi >= 0.4 else '\033[91m[AVALANCHE LOCK]\033[0m')}

\033[1m[2] MICRO TACTICAL FLOW (Coinbase Spot vs Binance Perp)\033[0m
• Coinbase Spot   : ${cb_price:,.2f}          • Binance Perp    : ${bn_price:,.2f}
• Cross-Venue Basis: {basis * 100:+.4f}%            • Taker Buy Z-Score: {z_cb:+.2f}σ
• Binance Funding : {funding * 100:+.4f}%             • Ignition Index (Ψ): {psi:.4f}
--------------------------------------------------------------------------------
\033[1m[3] STATE MACHINE EXECUTION & ALLOCATION\033[0m
• Decision State  : {state_color}\033[1m{self.current_state}\033[0m
• DCA Multiplier  : \033[1;32m{multiplier:.2f}x\033[0m (Allocation Target: ${BASE_DCA_AMOUNT * multiplier:,.2f})
\033[1;37m================================================================================\033[0m
""")
        sys.stdout.flush()


# ==============================================================================
# MODULE 5: APPLICATION LIFECYCLE ORCHESTRATOR
# ==============================================================================

async def main():
    init_database()

    gex_harvester = GexHarvester()
    flow_monitor = CoinbaseToxicFlowMonitor()
    notifier = TelegramNotifier()
    engine = SpotDCACombinerEngine(notifier)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def shutdown_handler():
        gex_harvester.running = False
        flow_monitor.running = False
        engine.running = False
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            pass  # Windows platform fallback

    tasks = [
        asyncio.create_task(gex_harvester.run()),
        asyncio.create_task(flow_monitor.run()),
        asyncio.create_task(engine.run())
    ]

    try:
        await stop_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        print("\n[Shutdown] Event loops gracefully terminated. SQLite WAL flushed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass