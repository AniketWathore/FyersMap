#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║      BOOKMAP TERMINAL - ORDERBOOK + CHART LAYOUT         ║
║    Interactive Dual-Panel Market Depth Visualization    ║
╠══════════════════════════════════════════════════════════╣
║  Left Panel:  Scrollable Orderbook Table (101 rows)      ║
║  Right Panel: TradingView-Style Price Chart             ║
║  Features:   Historical Volume Tracking                  ║
║             Color-Coded Intensity Bars                   ║
║             Real-time Big-Move Alerts                    ║
╚══════════════════════════════════════════════════════════╝
"""

import sys
import gc
import logging
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QSplitter, QComboBox, QStatusBar,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt5.QtCore import Qt, QThread, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPalette, QBrush

import pyqtgraph as pg

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s  %(levelname)-8s  %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('bookmap')

# ─────────────────────────────────────────────────────────────────────────────
# FILE PATHS
# ─────────────────────────────────────────────────────────────────────────────
TICK_FILE = "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/NSE_TCS-EQ_tick_20260402_095152.csv"
ORDERBOOK_FILE = "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/NSE_TCS-EQ_orderbook_20260402_095152.csv"

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
WINDOW_TICKS      = 300
PRICE_STEP        = 0.1
OB_CHUNK_SIZE     = 10_000
PRICE_LEVELS      = 50         # 50 bids + 50 asks
CHART_DISPLAY     = 21         # Show 21 rows on screen
DOM_UPDATE_EVERY  = 3          # Update DOM every N steps

# ─────────────────────────────────────────────────────────────────────────────
# COLOR PALETTE
# ─────────────────────────────────────────────────────────────────────────────
C_BG              = '#07070e'
C_PANEL           = '#0c0c1c'
C_GRID            = '#1a1a30'
C_TEXT            = '#b0b0cc'
C_TEXT_DIM        = '#606080'
C_MID             = '#f0f0f0'
C_YELLOW          = '#f0b020'
C_ALERT           = '#ff8800'

# Bid colors (green shades)
C_BID_BASE        = (0, 200, 100)      # Base green
C_ASK_BASE        = (220, 40, 60)      # Base red

pg.setConfigOptions(antialias=False, useOpenGL=False)

# ═════════════════════════════════════════════════════════════════════════════
class DataLoader(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)

    def __init__(self, tick_path: str, ob_path: str, parent=None):
        super().__init__(parent)
        self.tick_path = tick_path
        self.ob_path   = ob_path

    def run(self):
        try:
            self.progress.emit(3, "Reading tick file…")
            tick_dtypes = {
                'ticker': 'category',
                'timestamp': np.int64,
                'best_bid': np.float32,
                'best_ask': np.float32,
                'mid_price': np.float32,
                'spread': np.float32,
                'total_bid_qty': np.int32,
                'total_ask_qty': np.int32
            }
            tick_df = pd.read_csv(self.tick_path, dtype=tick_dtypes)
            self.progress.emit(18, "Tick loaded… Reading orderbook…")

            ob_dtypes = {
                'ticker': 'category',
                'timestamp': np.int64,
                'price': np.float32,
                'bid_qty': np.int32,
                'ask_qty': np.int32,
                'bid_orders': np.int16,
                'ask_orders': np.int16,
                'side': 'category'
            }
            ob_index: Dict[int, pd.DataFrame] = {}
            total_ob = 0

            for chunk in pd.read_csv(self.ob_path, dtype=ob_dtypes, chunksize=OB_CHUNK_SIZE):
                for ts, grp in chunk.groupby('timestamp', sort=False):
                    ts_i = int(ts)
                    if ts_i in ob_index:
                        ob_index[ts_i] = pd.concat([ob_index[ts_i], grp], ignore_index=True)
                    else:
                        ob_index[ts_i] = grp.reset_index(drop=True)
                total_ob += len(chunk)
                pct = min(18 + int(72 * total_ob / max(total_ob, 1)), 90)
                self.progress.emit(pct, f"Orderbook: {total_ob:,} rows indexed…")

            gc.collect()
            self.progress.emit(100, "Ready!")
            self.finished.emit(tick_df, ob_index)
        except Exception as exc:
            self.progress.emit(-1, f"ERROR: {exc}")
            log.exception("Data loading failed")

# ═════════════════════════════════════════════════════════════════════════════
@dataclass
class PriceLevelData:
    """Tracks volume history for a single price level."""
    price: float
    side: str = 'bid'  # 'bid' or 'ask'
    
    # Cumulative tracking across all ticks
    total_qty_added: int = 0          # Sum of all qty increments
    total_orders_added: int = 0       # Sum of all order increments
    
    # Current state
    current_qty: int = 0
    current_orders: int = 0
    
    # Historical tracking
    previous_qty: int = 0
    previous_orders: int = 0

    def update(self, new_qty: int, new_orders: int) -> Tuple[int, int]:
        """
        Update level with new qty/orders. Returns (qty_delta, orders_delta).
        """
        qty_delta = max(0, new_qty - self.current_qty)  # Only positive increments count
        orders_delta = max(0, new_orders - self.current_orders)
        
        self.total_qty_added += qty_delta
        self.total_orders_added += orders_delta
        
        self.previous_qty = self.current_qty
        self.previous_orders = self.current_orders
        
        self.current_qty = new_qty
        self.current_orders = new_orders
        
        return qty_delta, orders_delta

    def get_display_qty(self) -> int:
        """Return the cumulative quantity to display."""
        return self.total_qty_added

    def get_display_orders(self) -> int:
        """Return the cumulative orders to display."""
        return self.total_orders_added


class OrderbookTracker:
    """Maintains historical orderbook data for up to 101 price levels (50 bid, 1 mid, 50 ask)."""
    
    def __init__(self, max_levels: int = PRICE_LEVELS):
        self.max_levels = max_levels
        self.all_levels: Dict[float, PriceLevelData] = {}  # Historical data for ALL levels ever seen
        self.active_levels: Dict[float, PriceLevelData] = {}  # Current visible levels
        self.mid_price = 0.0
        self.tick_count = 0

    def update(self, snap: pd.DataFrame, mid: float) -> Dict[str, List[Tuple[float, int, int]]]:
        """
        Update tracker with new snapshot.
        Returns dict with 'bids' and 'asks' containing (price, qty, orders) tuples.
        """
        self.tick_count += 1
        self.mid_price = mid
        
        if snap.empty:
            return {'bids': [], 'asks': [], 'mid': mid}

        # Process all current data
        prices = snap['price'].values
        bid_qtys = snap['bid_qty'].values
        ask_qtys = snap['ask_qty'].values
        bid_orders = snap.get('bid_orders', pd.Series([0] * len(snap))).values if 'bid_orders' in snap else np.zeros(len(snap), dtype=np.int16)
        ask_orders = snap.get('ask_orders', pd.Series([0] * len(snap))).values if 'ask_orders' in snap else np.zeros(len(snap), dtype=np.int16)

        current_prices: Set[float] = set()

        # Update bids and asks
        for i in range(len(prices)):
            price = round(round(float(prices[i]) / PRICE_STEP) * PRICE_STEP, 2)
            bid_qty = int(bid_qtys[i])
            ask_qty = int(ask_qtys[i])
            bid_ord = int(bid_orders[i]) if i < len(bid_orders) else 0
            ask_ord = int(ask_orders[i]) if i < len(ask_orders) else 0

            if bid_qty > 0:
                current_prices.add(price)
                if price not in self.all_levels:
                    self.all_levels[price] = PriceLevelData(price=price, side='bid')
                self.all_levels[price].update(bid_qty, bid_ord)

            if ask_qty > 0:
                current_prices.add(price)
                if price not in self.all_levels:
                    self.all_levels[price] = PriceLevelData(price=price, side='ask')
                self.all_levels[price].update(ask_qty, ask_ord)

        # Mark missing levels as having 0 qty (important for filled/cancelled tracking)
        for price, level in self.all_levels.items():
            if price not in current_prices and level.current_qty > 0:
                level.update(0, 0)

        # Build active levels list (50 bids, 50 asks around mid)
        bids = [lv for lv in self.all_levels.values() if lv.side == 'bid' and lv.current_qty > 0]
        asks = [lv for lv in self.all_levels.values() if lv.side == 'ask' and lv.current_qty > 0]
        
        bids.sort(key=lambda x: x.price, reverse=True)
        asks.sort(key=lambda x: x.price)
        
        # Keep only recent levels (up to max_levels each)
        active_bids = bids[:self.max_levels]
        active_asks = asks[:self.max_levels]
        
        self.active_levels = {lv.price: lv for lv in active_bids + active_asks}

        return {
            'bids': [(lv.price, lv.get_display_qty(), lv.get_display_orders()) for lv in active_bids],
            'asks': [(lv.price, lv.get_display_qty(), lv.get_display_orders()) for lv in active_asks],
            'mid': mid
        }

    def get_historical_data(self, price: float) -> Optional[PriceLevelData]:
        """Retrieve historical data for a price level, even if no longer in current snapshot."""
        return self.all_levels.get(round(round(price / PRICE_STEP) * PRICE_STEP, 2))

    def reset(self):
        self.all_levels.clear()
        self.active_levels.clear()
        self.mid_price = 0.0
        self.tick_count = 0

# ═════════════════════════════════════════════════════════════════════════════
class TradingChart(pg.GraphicsLayoutWidget):
    """TradingView-style chart showing mid price history."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(C_BG)
        self._build_plot()
        
        self.mid_prices = deque(maxlen=WINDOW_TICKS)
        self.tick_times = deque(maxlen=WINDOW_TICKS)
        self.tick_count = 0
        self.start_time = None
        self.start_tick = 0

    def _build_plot(self):
        self.plt = self.addPlot()
        self.plt.setMenuEnabled(False)
        self.plt.setClipToView(True)
        self.plt.setDefaultPadding(0)
        self.plt.setLabel('left', 'Price (₹)', color=C_TEXT)
        self.plt.setLabel('bottom', 'Time (HH:MM:SS:TICK)', color=C_TEXT)
        
        mono = QFont('Menlo', 8)
        for name in ('left', 'bottom'):
            ax = self.plt.getAxis(name)
            ax.setPen(pg.mkPen(C_GRID))
            ax.setTextPen(pg.mkPen(C_TEXT))
            ax.setStyle(tickFont=mono)
            # Set reasonable tick intervals
            if name == 'bottom':
                ax.setTicks([[(i, '') for i in range(0, WINDOW_TICKS + 1, max(1, WINDOW_TICKS // 10))]])

        self.plt.showGrid(x=True, y=True, alpha=0.10)

        # Mid price line - make it more visible
        self.line_mid = pg.PlotCurveItem(pen=pg.mkPen(color=C_MID, width=2.5), symbol='o', symbolSize=3, symbolBrush=pg.mkBrush(C_MID), symbolPen=pg.mkPen(color=C_MID, width=1))
        self.plt.addItem(self.line_mid)

        # Crosshairs
        self.vline = pg.InfiniteLine(angle=90, pen=pg.mkPen(C_GRID, width=1, style=Qt.DotLine))
        self.hline = pg.InfiniteLine(angle=0, pen=pg.mkPen(C_GRID, width=1, style=Qt.DotLine))
        self.vline.setZValue(0)
        self.hline.setZValue(0)
        self.plt.addItem(self.vline, ignoreBounds=True)
        self.plt.addItem(self.hline, ignoreBounds=True)

        # Tooltip
        self.tooltip = pg.TextItem(text='', color=C_TEXT, fill=pg.mkBrush(12, 12, 30, 230), border=pg.mkPen(C_GRID, width=1), anchor=(0, 1))
        self.tooltip.setFont(QFont('Menlo', 9))
        self.tooltip.setZValue(100)
        self.tooltip.setVisible(False)
        self.plt.addItem(self.tooltip)

        self.setMouseTracking(True)
        proxy = pg.SignalProxy(self.plt.scene().sigMouseMoved, rateLimit=30, slot=self._on_mouse_moved)
        self._mouse_proxy = proxy

    def on_tick(self, mid: float, tick_num: int, timestamp: int):
        """Add new mid price point."""
        if self.start_time is None:
            self.start_time = timestamp
            self.start_tick = tick_num
        
        self.tick_count = tick_num
        self.mid_prices.append(mid)
        
        # Format: HH:MM:SS:TICK
        # Try different timestamp formats
        elapsed_ms = abs(timestamp - self.start_time)
        if elapsed_ms > 100_000_000:  # Likely nanoseconds
            elapsed_seconds = elapsed_ms // 1_000_000_000
        elif elapsed_ms > 100_000:  # Likely microseconds
            elapsed_seconds = elapsed_ms // 1_000_000
        else:  # Likely milliseconds or seconds
            elapsed_seconds = elapsed_ms // 1_000 if elapsed_ms > 1_000 else elapsed_ms
        
        hours = max(0, elapsed_seconds // 3600)
        minutes = max(0, (elapsed_seconds % 3600) // 60)
        seconds = max(0, elapsed_seconds % 60)
        
        time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}:{tick_num - self.start_tick}"
        self.tick_times.append(time_str)
        
        self._update_plot()

    def _update_plot(self):
        """Redraw the price curve."""
        if not self.mid_prices:
            return
        
        x = np.arange(len(self.mid_prices))
        y = np.array(self.mid_prices)
        
        self.line_mid.setData(x, y)
        
        if len(self.mid_prices) > 1:
            self.plt.setXRange(max(0, len(self.mid_prices) - WINDOW_TICKS), len(self.mid_prices), padding=0)
            y_min, y_max = y.min(), y.max()
            y_range = max(y_max - y_min, 0.5)
            self.plt.setYRange(y_min - y_range * 0.1, y_max + y_range * 0.1, padding=0)

    def _on_mouse_moved(self, evt):
        """Handle mouse hover for crosshairs and tooltip."""
        pos = evt[0]
        if not self.plt.sceneBoundingRect().contains(pos):
            self.tooltip.setVisible(False)
            return

        mouse_pt = self.plt.vb.mapSceneToView(pos)
        mx, my = mouse_pt.x(), mouse_pt.y()
        
        self.vline.setPos(mx)
        self.hline.setPos(my)

        # Find nearest tick
        if self.mid_prices:
            idx = int(max(0, min(mx, len(self.mid_prices) - 1)))
            if 0 <= idx < len(self.mid_prices):
                time_str = self.tick_times[idx] if idx < len(self.tick_times) else "—"
                price = self.mid_prices[idx]
                txt = f"  ₹{price:.2f}\n  {time_str}  "
                self.tooltip.setText(txt)
                self.tooltip.setPos(mx + 2, my)
                self.tooltip.setVisible(True)

    def reset(self):
        """Clear all data."""
        self.mid_prices.clear()
        self.tick_times.clear()
        self.tick_count = 0
        self.start_time = None
        self.start_tick = 0
        self.line_mid.setData([], [])
        self.tooltip.setVisible(False)

# ═════════════════════════════════════════════════════════════════════════════
class OrderbookWidget(QFrame):
    """Left panel: Scrollable orderbook table showing all 101 price levels."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: {C_PANEL}; }}")
        self.setMinimumWidth(320)
        self.setMaximumWidth(420)
        self._build()
        self._max_qty = 1
        self._max_orders = 1
        self._tick_counter = 0
        self._scroll_initialized = False

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Header
        hdr = QLabel("  ORDERBOOK  (L1 → L50)")
        hdr.setStyleSheet(f"color: {C_TEXT}; font: bold 9px 'Menlo'; background: {C_GRID}; padding: 5px 6px; letter-spacing: 1px;")
        root.addWidget(hdr)

        # Column headers
        col_hdr = QLabel("  PRICE      ↓ QTY ↑      ↓ ORDERS ↑")
        col_hdr.setStyleSheet(f"color: {C_TEXT_DIM}; font: 7px 'Menlo'; background: {C_BG}; padding: 3px 6px;")
        root.addWidget(col_hdr)

        # Scroll area with price level rows
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setMaximumHeight((CHART_DISPLAY * 16) + 4)  # Show exactly 21 rows on screen
        self.scroll.setStyleSheet(f"""
            QScrollArea {{ background: {C_BG}; border: none; }}
            QScrollBar:vertical {{ background: {C_PANEL}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C_GRID}; border-radius: 4px; min-height: 20px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

        scroll_widget = QWidget()
        scroll_widget.setStyleSheet(f"background: {C_BG};")
        self.scroll_layout = QVBoxLayout(scroll_widget)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(1)

        # Create row widgets for 101 levels
        self.row_labels: List[QLabel] = []
        ROW_HEIGHT = 16
        
        for i in range(101):  # 50 asks + 1 mid + 50 bids
            row = QLabel("")
            row.setFixedHeight(ROW_HEIGHT)
            row.setStyleSheet(f"color: {C_TEXT}; font: 8px 'Menlo'; padding: 0px 6px;")
            row.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.row_labels.append(row)
            self.scroll_layout.addWidget(row)

        self.scroll.setWidget(scroll_widget)
        root.addWidget(self.scroll, stretch=1)

        # Status bar
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font: 7px 'Menlo'; background: {C_GRID}; padding: 4px 6px;")
        root.addWidget(self.status_lbl)

    def _shade_color(self, intensity: float, is_bid: bool) -> str:
        """
        Generate hex color based on intensity [0.0 to 1.0].
        Lighter = lower intensity, Darker = higher intensity.
        """
        if intensity == 0.0:
            return f"rgb({C_BG})"  # No color
        
        if is_bid:
            # Green shades: light green (low) to dark green (high)
            base_r, base_g, base_b = 0, 200, 100
        else:
            # Red shades: light red (low) to dark red (high)
            base_r, base_g, base_b = 220, 40, 60
        
        # Scale intensity: 0.0 = 30% brightness, 1.0 = 100% brightness
        brightness = 0.3 + (intensity * 0.7)
        
        r = int(base_r * brightness)
        g = int(base_g * brightness)
        b = int(base_b * brightness)
        
        return f"rgb({r}, {g}, {b})"

    def _render_bar(self, value: int, max_val: int, width_chars: int = 20) -> str:
        """Render a text-based bar with color intensity."""
        if max_val <= 0 or value <= 0:
            return "░" * width_chars
        
        intensity = min(1.0, value / max_val)
        filled = max(1, int(width_chars * intensity))
        empty = width_chars - filled
        
        return "█" * filled + "░" * empty

    def refresh(self, orderbook_data: Dict, bid: float, ask: float, mid: float):
        """Update orderbook display."""
        self._tick_counter += 1
        # Allow first update immediately, then throttle
        if self._tick_counter > 1 and self._tick_counter % DOM_UPDATE_EVERY != 0:
            return

        bids = orderbook_data.get('bids', [])
        asks = orderbook_data.get('asks', [])

        # Compute max values for scaling
        all_qtys = [item[1] for item in bids + asks]
        all_orders = [item[2] for item in bids + asks]
        
        if all_qtys:
            self._max_qty = max(self._max_qty * 0.95, max(all_qtys))
        if all_orders:
            self._max_orders = max(self._max_orders * 0.95, max(all_orders))

        # Build display: asks (descending) + mid + bids (ascending)
        display_lines = []

        # Asks (top to bottom, highest price first)
        for price, qty, orders in asks:
            intensity_qty = min(1.0, qty / max(self._max_qty, 1))
            intensity_ord = min(1.0, orders / max(self._max_orders, 1))
            
            bar_qty = self._render_bar(qty, self._max_qty, 18)
            bar_ord = self._render_bar(orders, self._max_orders, 18)
            
            color_qty = self._shade_color(intensity_qty, False)  # Red for ask
            color_ord = self._shade_color(intensity_ord, False)
            
            line = f"  {price:>8.1f}  {qty:>7,}({bar_qty})  {orders:>6,}({bar_ord})"
            display_lines.append(('ask', line, color_qty, color_ord))

        # Mid price
        display_lines.append(('mid', f"  ││ MID: ₹{mid:.2f} ││", C_TEXT, C_TEXT))

        # Bids (top to bottom, highest price first)
        for price, qty, orders in bids:
            intensity_qty = min(1.0, qty / max(self._max_qty, 1))
            intensity_ord = min(1.0, orders / max(self._max_orders, 1))
            
            bar_qty = self._render_bar(qty, self._max_qty, 18)
            bar_ord = self._render_bar(orders, self._max_orders, 18)
            
            color_qty = self._shade_color(intensity_qty, True)  # Green for bid
            color_ord = self._shade_color(intensity_ord, True)
            
            line = f"  {price:>8.1f}  {qty:>7,}({bar_qty})  {orders:>6,}({bar_ord})"
            display_lines.append(('bid', line, color_qty, color_ord))

        # Pad with empty rows to reach 101
        while len(display_lines) < 101:
            display_lines.append(('empty', '', C_TEXT_DIM, C_TEXT_DIM))

        # Update row labels
        for i, (row_type, text, color_qty, color_ord) in enumerate(display_lines):
            if row_type == 'mid':
                self.row_labels[i].setStyleSheet(f"color: {color_qty}; font: bold 8px 'Menlo'; padding: 0px 6px; background: {C_GRID};")
                self.row_labels[i].setText(text)
            elif row_type == 'empty':
                self.row_labels[i].setText("")
                self.row_labels[i].setStyleSheet(f"color: {C_TEXT_DIM}; font: 8px 'Menlo'; padding: 0px 6px;")
            else:
                self.row_labels[i].setStyleSheet(f"color: {C_TEXT}; font: 8px 'Menlo'; padding: 0px 6px;")
                self.row_labels[i].setText(text)

        # Center scroll to mid
        if not self._scroll_initialized:
            QTimer.singleShot(100, self._center_scroll)
            self._scroll_initialized = True

        # Update status
        total_bid_qty = sum(item[1] for item in bids)
        total_ask_qty = sum(item[1] for item in asks)
        self.status_lbl.setText(f"  BID Qty: {int(total_bid_qty):>8,}  │  ASK Qty: {int(total_ask_qty):>8,}")

    def _center_scroll(self):
        """Center scroll at mid price."""
        mid_row = 50  # Mid is at row 50 (50 asks before it)
        row_height = 16
        target_y = mid_row * row_height
        visible_h = self.scroll.viewport().height()
        target = max(0, target_y - visible_h // 2)
        self.scroll.verticalScrollBar().setValue(target)

    def reset(self):
        """Clear display."""
        for row in self.row_labels:
            row.setText("")
        self._tick_counter = 0
        self._scroll_initialized = False

# ═════════════════════════════════════════════════════════════════════════════
class StatsBar(QFrame):
    """Top status bar showing key metrics."""
    
    _FIELDS = [
        ('ticker', 'SYMBOL', C_TEXT, lambda v: str(v)),
        ('best_bid', 'BID', '#00c0e8', lambda v: f'₹{float(v):,.2f}'),
        ('best_ask', 'ASK', '#e83050', lambda v: f'₹{float(v):,.2f}'),
        ('spread', 'SPREAD', C_YELLOW, lambda v: f'{float(v):.3f}'),
        ('mid_price', 'MID', C_MID, lambda v: f'₹{float(v):,.2f}'),
        ('total_bid_qty', 'BID QTY', '#00c0e8', lambda v: f'{int(v):,}'),
        ('total_ask_qty', 'ASK QTY', '#e83050', lambda v: f'{int(v):,}'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: {C_PANEL}; }}")
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)
        self._vals: Dict[str, QLabel] = {}

        for key, label, color, _ in self._FIELDS:
            box = QFrame()
            box.setStyleSheet(f"QFrame {{ background: {C_BG}; border: 1px solid {C_GRID}; border-radius: 3px; }}")
            vl = QVBoxLayout(box)
            vl.setContentsMargins(10, 3, 10, 3)
            vl.setSpacing(0)
            
            t = QLabel(label)
            t.setStyleSheet(f"QLabel {{ color: {C_TEXT_DIM}; font: 7px 'Menlo'; border: none; letter-spacing: 1px; }}")
            
            v = QLabel("—")
            v.setStyleSheet(f"QLabel {{ color: {color}; font: bold 12px 'Menlo'; border: none; }}")
            v.setAlignment(Qt.AlignCenter)
            
            vl.addWidget(t)
            vl.addWidget(v)
            self._vals[key] = v
            lay.addWidget(box)

        lay.addStretch()
        
        self.ts_lbl = QLabel("—")
        self.ts_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font: bold 10px 'Menlo';")
        lay.addWidget(self.ts_lbl)

    def refresh(self, row: pd.Series):
        """Update with tick data."""
        for key, _, _, fmt in self._FIELDS:
            try:
                self._vals[key].setText(fmt(row[key]))
            except:
                pass
        try:
            dt = str(row.get('datetime', ''))
            self.ts_lbl.setText(dt[:19] if dt else '—')
        except:
            pass

# ═════════════════════════════════════════════════════════════════════════════
class ControlBar(QFrame):
    """Control bar with play/pause/reset and speed controls."""
    
    SPEED_MAP = {
        '0.25×': 0.25,
        '0.5×': 0.5,
        '1×': 1.0,
        '2×': 2.0,
        '5×': 5.0,
        '10×': 10.0,
        '25×': 25.0,
        'MAX': 9999.0
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: {C_PANEL}; border-bottom: 1px solid {C_GRID}; }}")
        self.setFixedHeight(40)
        self._build()

    def _build(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)

        self.btn_play = self._btn("▶  PLAY", '#174a17')
        self.btn_pause = self._btn("⏸  PAUSE", '#4a3a10')
        self.btn_stop = self._btn("■  RESET", '#4a1414')

        sep = QFrame()
        sep.setFixedSize(1, 20)
        sep.setStyleSheet(f"background: {C_GRID};")

        spd_lbl = QLabel("SPEED")
        spd_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font: 7px 'Menlo'; letter-spacing: 1px;")

        self.speed_cb = QComboBox()
        self.speed_cb.addItems(list(self.SPEED_MAP.keys()))
        self.speed_cb.setCurrentIndex(2)
        self.speed_cb.setFixedWidth(75)
        self.speed_cb.setStyleSheet(f"""
            QComboBox {{ background: {C_BG}; color: {C_TEXT}; border: 1px solid {C_GRID}; padding: 2px 6px; font: 9px 'Menlo'; }}
            QComboBox::drop-down {{ border: none; width: 16px; }}
            QComboBox QAbstractItemView {{ background: {C_PANEL}; color: {C_TEXT}; selection-background-color: {C_GRID}; }}
        """)

        for w in [self.btn_play, self.btn_pause, self.btn_stop, sep, spd_lbl, self.speed_cb]:
            lay.addWidget(w)

        lay.addStretch()

        self.info_lbl = QLabel("Load data to begin")
        self.info_lbl.setStyleSheet(f"color: {C_TEXT_DIM}; font: 9px 'Menlo';")
        lay.addWidget(self.info_lbl)

    def _btn(self, text: str, bg: str) -> QPushButton:
        b = QPushButton(text)
        b.setStyleSheet(f"""
            QPushButton {{ background: {bg}; color: #e0e0e0; border: none; padding: 4px 14px; border-radius: 3px; font: bold 9px 'Menlo'; }}
            QPushButton:hover {{ background: #2a7a2a; }}
            QPushButton:pressed {{ background: #0f3a0f; }}
            QPushButton:disabled {{ background: #1a1a2a; color: #404050; }}
        """)
        return b

    def set_info(self, msg: str):
        self.info_lbl.setText(msg)

    @property
    def speed(self) -> float:
        return self.SPEED_MAP.get(self.speed_cb.currentText(), 1.0)

# ═════════════════════════════════════════════════════════════════════════════
class BookmapTerminal(QMainWindow):
    """Main terminal window with dual-panel layout."""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("📊  Bookmap Terminal  ·  NSE TCS-EQ  ·  L2 Market Depth")
        self.resize(1600, 900)
        self._apply_palette()
        self._build_ui()
        self._init_state()
        self._start_loading()

    def _apply_palette(self):
        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(C_BG))
        pal.setColor(QPalette.WindowText, QColor(C_TEXT))
        pal.setColor(QPalette.Base, QColor(C_PANEL))
        pal.setColor(QPalette.Text, QColor(C_TEXT))
        pal.setColor(QPalette.Button, QColor(C_PANEL))
        pal.setColor(QPalette.ButtonText, QColor(C_TEXT))
        self.setPalette(pal)
        self.setStyleSheet(f"QMainWindow, QWidget {{ background: {C_BG}; }}")

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_lay = QVBoxLayout(root)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # Stats bar
        self.stats = StatsBar()
        main_lay.addWidget(self.stats)

        # Control bar
        self.ctrl = ControlBar()
        self.ctrl.btn_play.clicked.connect(self._play)
        self.ctrl.btn_pause.clicked.connect(self._pause)
        self.ctrl.btn_stop.clicked.connect(self._stop_reset)
        self.ctrl.speed_cb.currentTextChanged.connect(self._on_speed_change)
        main_lay.addWidget(self.ctrl)

        # Alert banner
        self.alert_banner = QLabel("")
        self.alert_banner.setAlignment(Qt.AlignCenter)
        self.alert_banner.setStyleSheet(f"QLabel {{ background: #3a1800; color: {C_ALERT}; font: bold 11px 'Menlo'; padding: 6px; border: 2px solid {C_ALERT}; }}")
        self.alert_banner.setFixedHeight(32)
        self.alert_banner.setVisible(False)
        main_lay.addWidget(self.alert_banner)

        # Main content: Orderbook (left) + Chart (right)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(3)
        splitter.setStyleSheet(f"QSplitter::handle {{ background: {C_GRID}; }}")

        self.orderbook = OrderbookWidget()
        self.chart = TradingChart()

        splitter.addWidget(self.orderbook)
        splitter.addWidget(self.chart)
        splitter.setSizes([400, 1200])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        main_lay.addWidget(splitter, stretch=1)

        # Status bar
        sb = QStatusBar()
        sb.setStyleSheet(f"QStatusBar {{ background: {C_PANEL}; color: {C_TEXT_DIM}; font: 9px 'Menlo'; border-top: 1px solid {C_GRID}; }}")
        self.setStatusBar(sb)
        self.sb = sb

    def _init_state(self):
        self.tick_df: Optional[pd.DataFrame] = None
        self.ob_index: Optional[Dict[int, pd.DataFrame]] = None
        self.tick_idx = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.tracker = OrderbookTracker()
        
        for b in (self.ctrl.btn_play, self.ctrl.btn_pause, self.ctrl.btn_stop):
            b.setEnabled(False)

    def _start_loading(self):
        self.sb.showMessage("⏳  Loading data files…")
        self._loader = DataLoader(TICK_FILE, ORDERBOOK_FILE)
        self._loader.progress.connect(self._on_load_progress)
        self._loader.finished.connect(self._on_loaded)
        self._loader.start()

    @pg.QtCore.pyqtSlot(int, str)
    def _on_load_progress(self, pct: int, msg: str):
        if pct < 0:
            self.sb.showMessage(f"❌  {msg}")
        else:
            self.sb.showMessage(f"⏳  {pct}%  ·  {msg}")

    @pg.QtCore.pyqtSlot(object, object)
    def _on_loaded(self, tick_df: pd.DataFrame, ob_index: dict):
        self.tick_df = tick_df
        self.ob_index = ob_index
        self.sb.showMessage(f"✅  Loaded  ·  Press  ▶ PLAY  to begin")
        self.ctrl.set_info(f"{len(tick_df):,} ticks")
        
        for b in (self.ctrl.btn_play, self.ctrl.btn_pause, self.ctrl.btn_stop):
            b.setEnabled(True)

    def _play(self):
        if self.tick_df is None:
            return
        self._timer.start(max(1, int(500.0 / self.ctrl.speed)))
        self.ctrl.btn_play.setText("▶  PLAYING")
        self.sb.showMessage("▶  Playing…")

    def _pause(self):
        self._timer.stop()
        self.ctrl.btn_play.setText("▶  PLAY")
        self.sb.showMessage("⏸  Paused")

    def _stop_reset(self):
        self._timer.stop()
        self.tick_idx = 0
        self.orderbook.reset()
        self.chart.reset()
        self.tracker.reset()
        self.alert_banner.setVisible(False)
        self.ctrl.btn_play.setText("▶  PLAY")
        self.sb.showMessage("■  Reset")

    def _on_speed_change(self, _text: str):
        if self._timer.isActive():
            self._timer.setInterval(max(1, int(500.0 / self.ctrl.speed)))

    def _step(self):
        """Step through one tick."""
        if self.tick_df is None or self.tick_idx >= len(self.tick_df):
            self._timer.stop()
            self.ctrl.btn_play.setText("▶  PLAY")
            self.sb.showMessage("⏹  Playback complete")
            return

        row = self.tick_df.iloc[self.tick_idx]
        ts = int(row['timestamp'])
        snap = self.ob_index.get(ts, pd.DataFrame())

        mid = float(row.get('mid_price', 0))
        
        # Update tracker and get orderbook data
        ob_data = self.tracker.update(snap, mid)

        # Refresh UI components
        self.orderbook.refresh(ob_data, float(row.get('best_bid', 0)), float(row.get('best_ask', 0)), mid)
        self.chart.on_tick(mid, self.tick_idx, ts)
        self.stats.refresh(row)

        # Check for big move alerts (mid hits a high-activity level)
        hist_mid = self.tracker.get_historical_data(mid)
        if hist_mid and self.orderbook._max_qty > 0 and self.tick_idx > 10:
            if hist_mid.total_qty_added > self.orderbook._max_qty * 0.8:
                self.alert_banner.setText(f"⚠ BIG MOVE ABOUT TO HAPPEN · Mid price crossing high-volume level (₹{mid}) ⚠")
                self.alert_banner.setVisible(True)
                QTimer.singleShot(2000, lambda: self.alert_banner.setVisible(False))

        # Update progress
        pct = int(100 * self.tick_idx / len(self.tick_df))
        self.ctrl.set_info(f"Tick  {self.tick_idx:>6,} / {len(self.tick_df):,}   ({pct}%)")
        self.tick_idx += 1

    def closeEvent(self, event):
        self._timer.stop()
        if hasattr(self, '_loader') and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(3000)
        super().closeEvent(event)

# ═════════════════════════════════════════════════════════════════════════════
def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Bookmap Terminal")
    app.setStyle("Fusion")
    app.setStyleSheet(f"""
        QWidget      {{ background: {C_BG}; color: {C_TEXT}; }}
        QScrollBar:vertical {{ background: {C_PANEL}; width: 8px; border: none; }}
        QScrollBar::handle:vertical {{ background: {C_GRID}; border-radius: 4px; }}
        QToolTip {{ background: {C_PANEL}; color: {C_TEXT}; border: 1px solid {C_GRID}; font: 9px 'Menlo'; }}
    """)
    win = BookmapTerminal()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
