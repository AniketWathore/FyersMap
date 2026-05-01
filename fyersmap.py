#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     BOOKMAP TERMINAL  —  NSE Indian Market  L2 Depth        ║
╠══════════════════════════════════════════════════════════════╣
║  LEFT  : Persistent DOM — ALL accumulated price levels       ║
║          Fixed row positions, highest price at top           ║
║          Mid-price row moves every tick (zero lag)           ║
║          Rolling window keeps mid centred; scroll freely     ║
║                                                              ║
║  RIGHT : TradingView-style mid-price chart                   ║
║          · Latest tick always pinned at right edge           ║
║          · Y-axis synced with DOM visible price window       ║
║          · Dotted mid line visually aligns with DOM mid row  ║
║          · Crosshairs, tooltip, zoom/pan                     ║
╚══════════════════════════════════════════════════════════════╝

Install:  pip install PyQt5 pyqtgraph numpy pandas
Run:      python3 bookmap_terminal.py
"""

import sys, gc, logging, bisect
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QSplitter, QComboBox, QStatusBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStyledItemDelegate,
)
from PyQt5.QtCore import Qt, QThread, QTimer, QSize, QRect, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPalette, QPainter, QPen

import pyqtgraph as pg

# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('bookmap')

# ─────────────────────────────────────────────────────────────
#  FILE PATHS  ← adjust if moved
# ─────────────────────────────────────────────────────────────
TICK_FILE = (
    "/home/aniket/SMP DATA/MarketDepthData/"
    "2026-02-27_12-15-23_NSE_TCS-EQ/NSE_TCS-EQ/"
    "NSE_TCS-EQ_tick_20260227_121523.csv"
)
ORDERBOOK_FILE = (
    "/home/aniket/SMP DATA/MarketDepthData/"
    "2026-02-27_12-15-23_NSE_TCS-EQ/NSE_TCS-EQ/"
    "NSE_TCS-EQ_orderbook_20260227_121523.csv"
)

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
ROW_H            = 19        # DOM row height (px)
OB_CHUNK_SIZE    = 10_000    # orderbook CSV read chunk size
CELL_UPDATE_EVERY = 2        # throttle full cell redraws (mid row: always)
CHART_MAX_POINTS  = 8_000    # max price history in RAM
CHART_VISIBLE     = 400      # default visible tick window in chart
CHART_RIGHT_PAD   = 0.08     # fraction of visible window kept empty on right

# ─────────────────────────────────────────────────────────────
#  COLOURS
# ─────────────────────────────────────────────────────────────
C_BG    = '#07070e'
C_PANEL = '#0c0c1c'
C_GRID  = '#1a1a30'
C_TEXT  = '#b0b0cc'
C_DIM   = '#505068'
C_WHITE = '#f0f0f0'
C_BID   = '#00c0e8'
C_ASK   = '#e83050'
C_YELL  = '#f0b020'
C_MID   = '#ffffff'

BID_BAR_RGB = (0,   200, 100)
ASK_BAR_RGB = (230,  45,  65)

BID_BG   = QColor( 0,  42,  18)
ASK_BG   = QColor(42,   8,  14)
MID_BG   = QColor(30,  30,  80)
EMPTY_BG = QColor( 7,   7,  14)
SEP_CLR  = QColor(26,  26,  48)

pg.setConfigOptions(antialias=False, useOpenGL=False)

_F9  = QFont('Menlo', 9)
_F8  = QFont('Menlo', 8)
_F9B = QFont('Menlo', 9); _F9B.setBold(True)
_F10B = QFont('Menlo', 10); _F10B.setBold(True)


# ═════════════════════════════════════════════════════════════
#  DATA LOADER
# ═════════════════════════════════════════════════════════════
class DataLoader(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object, object)   # tick_df, ob_index, sorted_prices

    def __init__(self, tick_path, ob_path, parent=None):
        super().__init__(parent)
        self.tick_path, self.ob_path = tick_path, ob_path

    def run(self):
        try:
            self.progress.emit(3, "Reading tick file…")
            tick_df = pd.read_csv(self.tick_path, dtype={
                'ticker': 'category', 'timestamp': np.int64,
                'best_bid': np.float32, 'best_ask': np.float32,
                'mid_price': np.float32, 'spread': np.float32,
                'total_bid_qty': np.int32, 'total_ask_qty': np.int32,
            })
            self.progress.emit(15, f"Tick ({len(tick_df):,} rows) · reading OB…")

            ob_index: Dict[int, pd.DataFrame] = {}
            all_prices: set = set()
            total = 0
            for chunk in pd.read_csv(self.ob_path, dtype={
                'ticker': 'category', 'timestamp': np.int64,
                'price': np.float32, 'bid_qty': np.int32,
                'ask_qty': np.int32, 'bid_orders': np.int16,
                'ask_orders': np.int16, 'side': 'category',
            }, chunksize=OB_CHUNK_SIZE):
                for raw_p in chunk['price'].values:
                    all_prices.add(round(float(raw_p), 2))
                for ts, grp in chunk.groupby('timestamp', sort=False):
                    k = int(ts)
                    ob_index[k] = (
                        pd.concat([ob_index[k], grp], ignore_index=True)
                        if k in ob_index else grp.reset_index(drop=True)
                    )
                total += len(chunk)
                self.progress.emit(
                    min(90, 15 + int(75 * total / max(total, 1))),
                    f"OB {total:,} rows · {len(all_prices):,} prices…"
                )

            sorted_prices = sorted(all_prices)
            gc.collect()
            self.progress.emit(100,
                f"Ready! {len(tick_df):,} ticks · {len(sorted_prices):,} unique prices")
            self.finished.emit(tick_df, ob_index, sorted_prices)
        except Exception as e:
            log.exception("DataLoader failed")
            self.progress.emit(-1, f"ERROR: {e}")


# ═════════════════════════════════════════════════════════════
#  PRICE LEVEL DATA  (persistent — never removed, never shuffled)
# ═════════════════════════════════════════════════════════════
@dataclass
class PriceLevelData:
    price:             float
    current_qty:       int = 0
    current_orders:    int = 0
    cumulative_qty:    int = 0
    cumulative_orders: int = 0

    def update(self, qty: int, orders: int):
        self.current_qty    = qty
        self.current_orders = orders
        if qty    > self.cumulative_qty:    self.cumulative_qty    = qty
        if orders > self.cumulative_orders: self.cumulative_orders = orders

    def clear_current(self):
        self.current_qty = 0; self.current_orders = 0


# ═════════════════════════════════════════════════════════════
#  ORDERBOOK TRACKER
# ═════════════════════════════════════════════════════════════
class OrderbookTracker:
    """
    Maintains every unique price ever seen in fixed sorted_prices list.
    On each snapshot, clears then refills current quantities.
    Returns mid_idx = index in ascending sorted_prices of the mid price row.
    """
    def __init__(self, sorted_prices: List[float]):
        self.sorted_prices = sorted_prices
        self.levels: Dict[float, PriceLevelData] = {
            p: PriceLevelData(p) for p in sorted_prices
        }

    def update(self, snap: pd.DataFrame, mid: float) -> int:
        for lv in self.levels.values():
            lv.clear_current()

        if not snap.empty:
            prices   = snap['price'].values.astype(np.float64)
            bid_qtys = snap['bid_qty'].values.astype(np.int32)
            ask_qtys = snap['ask_qty'].values.astype(np.int32)
            bid_ords = (snap['bid_orders'].values.astype(np.int32)
                        if 'bid_orders' in snap.columns
                        else np.zeros(len(snap), np.int32))
            ask_ords = (snap['ask_orders'].values.astype(np.int32)
                        if 'ask_orders' in snap.columns
                        else np.zeros(len(snap), np.int32))
            for i in range(len(prices)):
                p  = round(float(prices[i]), 2)
                lv = self.levels.get(p)
                if lv is None: continue
                bq, aq = int(bid_qtys[i]), int(ask_qtys[i])
                bo, ao = int(bid_ords[i]),  int(ask_ords[i])
                if bq > 0: lv.update(bq, bo)
                elif aq > 0: lv.update(aq, ao)

        # mid_idx: highest index where price <= mid  (ascending order)
        mid_idx = bisect.bisect_right(self.sorted_prices, mid) - 1
        return max(0, min(mid_idx, len(self.sorted_prices) - 1))

    def reset(self):
        for lv in self.levels.values():
            lv.clear_current()
            lv.cumulative_qty = 0; lv.cumulative_orders = 0


# ═════════════════════════════════════════════════════════════
#  DELEGATES
# ═════════════════════════════════════════════════════════════
class PriceDelegate(QStyledItemDelegate):
    def paint(self, p: QPainter, opt, idx):
        p.save()
        rt   = idx.data(Qt.UserRole) or 'empty'
        text = str(idx.data(Qt.DisplayRole) or '')
        bg   = {'bid': BID_BG, 'ask': ASK_BG, 'mid': MID_BG}.get(rt, EMPTY_BG)
        p.fillRect(opt.rect, bg)
        p.setPen(QPen(SEP_CLR, 1))
        p.drawLine(opt.rect.topRight(), opt.rect.bottomRight())
        if rt == 'mid':
            p.setFont(_F10B); p.setPen(QColor(C_MID))
            p.drawText(opt.rect, Qt.AlignCenter, text)
        elif rt in ('bid', 'ask'):
            p.setFont(_F9)
            p.setPen(QColor(C_BID if rt == 'bid' else C_ASK))
            p.drawText(opt.rect.adjusted(8, 0, -4, 0),
                       Qt.AlignVCenter | Qt.AlignLeft, text)
        elif text:
            p.setFont(_F8); p.setPen(QColor(C_DIM))
            p.drawText(opt.rect, Qt.AlignCenter, text)
        p.restore()

    def sizeHint(self, opt, idx): return QSize(0, ROW_H)


class BarDelegate(QStyledItemDelegate):
    def paint(self, p: QPainter, opt, idx):
        p.save()
        rt  = idx.data(Qt.UserRole)     or 'empty'
        iv  = idx.data(Qt.UserRole + 1) or 0.0
        txt = str(idx.data(Qt.DisplayRole) or '')
        bg  = {'bid': BID_BG, 'ask': ASK_BG, 'mid': MID_BG}.get(rt, EMPTY_BG)
        p.fillRect(opt.rect, bg)
        if rt == 'mid': p.restore(); return
        if rt in ('bid', 'ask') and iv > 0.015:
            br = 0.25 + iv * 0.75
            r, g, b = BID_BAR_RGB if rt == 'bid' else ASK_BAR_RGB
            bw = max(3, int(opt.rect.width() * iv))
            p.fillRect(
                QRect(opt.rect.right() - bw, opt.rect.top() + 2,
                      bw, opt.rect.height() - 4),
                QColor(int(r*br), int(g*br), int(b*br))
            )
        if txt and rt in ('bid', 'ask'):
            p.setFont(_F8); p.setPen(QColor(C_TEXT))
            p.drawText(opt.rect.adjusted(5, 0, -4, 0),
                       Qt.AlignVCenter | Qt.AlignLeft, txt)
        p.restore()

    def sizeHint(self, opt, idx): return QSize(0, ROW_H)


# ═════════════════════════════════════════════════════════════
#  ORDERBOOK WIDGET
# ═════════════════════════════════════════════════════════════
class OrderbookWidget(QFrame):
    """
    Architecture
    ────────────
    • sorted_prices is reversed so row 0 = highest price (asks at top, bids below).
    • Every price gets a FIXED row forever — rows never shuffle, no two prices share a row.
    • The mid-price row (span across all 3 cols) slides up and down through the fixed
      ladder every single tick — there is NO throttle on mid-row movement.
    • Full cell redraws are throttled to CELL_UPDATE_EVERY ticks for performance.
    • Auto-scroll centres the mid row at exactly 50 % of viewport height.
    • Any manual scroll touch pauses auto-centre until PLAY is pressed again.
    • get_visible_price_range() computes the price range deterministically from the
      mid_idx math — it does NOT read the scroll bar, avoiding Qt event lag.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background:{C_BG}; }}")
        self.setMinimumWidth(380)
        self.setMaximumWidth(680)

        # Internal state
        self._sorted_prices: List[float] = []   # HIGH → LOW order
        self._n             = 0
        self._mid_idx       = -1                 # display row index (HIGH→LOW)
        self._max_qty       = 1.0
        self._max_orders    = 1.0
        self._cell_ctr      = 0
        self._auto_scroll   = True
        self._scroll_locked = False              # prevents feedback loop

        self._build()

    # ── Build UI ─────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ASK header at the top (high prices = asks)
        root.addWidget(self._hdr(
            [("PRICE", 1), ("ASK QTY", 2), ("ASK ORDERS", 2)], C_ASK
        ))

        self.tbl = QTableWidget(0, 3)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.setSelectionMode(QAbstractItemView.NoSelection)
        self.tbl.setFocusPolicy(Qt.NoFocus)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setVisible(False)
        self.tbl.setShowGrid(False)
        self.tbl.setFrameShape(QFrame.NoFrame)
        self.tbl.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tbl.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tbl.setStyleSheet(f"""
            QTableWidget {{ background:{C_BG}; border:none; outline:none; }}
            QScrollBar:vertical {{
                background:{C_PANEL}; width:6px; border:none; }}
            QScrollBar::handle:vertical {{
                background:{C_GRID}; border-radius:3px; min-height:20px; }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height:0px; }}
        """)

        self.tbl.setColumnWidth(0, 80)
        for c in (1, 2):
            self.tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.Stretch)

        self.tbl.setItemDelegateForColumn(0, PriceDelegate(self.tbl))
        self.tbl.setItemDelegateForColumn(1, BarDelegate(self.tbl))
        self.tbl.setItemDelegateForColumn(2, BarDelegate(self.tbl))

        # Manual scroll → pause auto-centre
        self.tbl.verticalScrollBar().valueChanged.connect(self._on_user_scroll)

        root.addWidget(self.tbl, stretch=1)

        # BID header at the bottom (low prices = bids)
        root.addWidget(self._hdr(
            [("PRICE", 1), ("BID QTY", 2), ("BID ORDERS", 2)], C_BID
        ))

        self.stat = QLabel("")
        self.stat.setStyleSheet(
            f"color:{C_DIM}; font:7px 'Menlo'; background:{C_GRID}; padding:3px 6px;"
        )
        root.addWidget(self.stat)

    # ── One-time initialization after data is loaded ──────────
    def initialize(self, sorted_prices: List[float]):
        """
        sorted_prices comes in ASCENDING order from the tracker.
        We reverse it so row 0 = highest price (asks at top).
        """
        self._sorted_prices = list(reversed(sorted_prices))
        self._n = len(self._sorted_prices)
        self.tbl.setRowCount(self._n)
        for r in range(self._n):
            self.tbl.setRowHeight(r, ROW_H)
            price = self._sorted_prices[r]
            for c in range(3):
                it = QTableWidgetItem('')
                it.setData(Qt.UserRole,     'empty')
                it.setData(Qt.UserRole + 1, 0.0)
                self.tbl.setItem(r, c, it)
            self.tbl.item(r, 0).setData(Qt.DisplayRole, f'{price:.2f}')

    # ── Helpers ──────────────────────────────────────────────
    @staticmethod
    def _hdr(cols, color):
        f = QFrame()
        f.setStyleSheet(f"QFrame {{ background:{C_GRID}; border:none; }}")
        f.setFixedHeight(20)
        lay = QHBoxLayout(f)
        lay.setContentsMargins(4, 0, 4, 0); lay.setSpacing(0)
        for text, stretch in cols:
            lb = QLabel(text)
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(
                f"color:{color}; font:bold 7px 'Menlo'; letter-spacing:1px; padding:0;")
            lay.addWidget(lb, stretch)
        return f

    def _on_user_scroll(self, _value):
        if not self._scroll_locked:
            self._auto_scroll = False

    def _cell(self, r, c, rt, iv, txt):
        it = self.tbl.item(r, c)
        if it is not None:
            it.setData(Qt.UserRole,     rt)
            it.setData(Qt.UserRole + 1, float(iv))
            it.setData(Qt.DisplayRole,  txt)

    def _asc_to_display(self, asc_idx: int) -> int:
        """Convert ascending-order index → display row (high→low)."""
        return self._n - 1 - asc_idx

    # ── Main refresh — called every single tick ───────────────
    def refresh(self, tracker: 'OrderbookTracker', mid: float, asc_mid_idx: int):
        """
        Split into two phases:
        Phase 1 (every tick):   move mid-row span + label + auto-scroll
        Phase 2 (throttled):    redraw all non-mid cells

        This ensures the mid row and the chart dotted line move in perfect sync
        even at high playback speeds.
        """
        if self._n == 0:
            return

        display_mid = self._asc_to_display(asc_mid_idx)

        # ── PHASE 1: move mid row (runs every tick, no throttle) ──
        old_mid = self._mid_idx
        if old_mid != display_mid:
            if 0 <= old_mid < self._n:
                self.tbl.setSpan(old_mid, 0, 1, 1)   # un-span old
            self.tbl.setSpan(display_mid, 0, 1, 3)   # span new
        self._mid_idx = display_mid

        mi = self.tbl.item(display_mid, 0)
        if mi:
            mi.setData(Qt.UserRole,     'mid')
            mi.setData(Qt.UserRole + 1, 0.0)
            mi.setData(Qt.DisplayRole,  f'──── MID  ₹{mid:.2f} ────')

        # Always auto-scroll to keep mid centered
        if self._auto_scroll:
            self._scroll_to_mid_now()

        # ── PHASE 2: redraw cells (throttled) ────────────────
        self._cell_ctr += 1
        if self._cell_ctr % CELL_UPDATE_EVERY != 0:
            # Still refresh viewport so mid-row repaint is visible
            self.tbl.viewport().update()
            return

        # Intensity scaling
        cur_mq = max((lv.current_qty    for lv in tracker.levels.values()), default=0)
        cur_mo = max((lv.current_orders for lv in tracker.levels.values()), default=0)
        self._max_qty    = max(self._max_qty    * 0.96, cur_mq, 1)
        self._max_orders = max(self._max_orders * 0.96, cur_mo, 1)
        mq, mo = self._max_qty, self._max_orders

        prices = self._sorted_prices
        for i in range(self._n):
            if i == display_mid:
                continue
            lv   = tracker.levels[prices[i]]
            # Rows ABOVE mid (lower row index) are asks (higher price)
            # Rows BELOW mid (higher row index) are bids (lower price)
            rt   = 'ask' if i < display_mid else 'bid'
            qty  = lv.current_qty
            ords = lv.current_orders
            self._cell(i, 0, rt, 0.0, f'{prices[i]:.2f}')
            self._cell(i, 1, rt,
                       min(1.0, qty  / mq) if qty  else 0.0,
                       f'{qty:,}'  if qty  else '')
            self._cell(i, 2, rt,
                       min(1.0, ords / mo) if ords else 0.0,
                       f'{ords:,}' if ords else '')

        self.tbl.viewport().update()

        # Footer stats
        tb, ta = 0, 0
        for i, price in enumerate(prices):
            q = tracker.levels[price].current_qty
            if   i < display_mid: ta += q   # above mid = ask
            elif i > display_mid: tb += q   # below mid = bid
        self.stat.setText(
            f"  BID Qty: {tb:>10,}  │  ASK Qty: {ta:>10,}  │  "
            f"LEVELS: {self._n:,}"
        )

    def _scroll_to_mid_now(self):
        """
        Centre the mid row at exactly 50 % of the viewport height.
        Uses _scroll_locked to prevent triggering _on_user_scroll.
        """
        if self._mid_idx < 0 or self._n == 0:
            return
        self._scroll_locked = True
        vp_h   = self.tbl.viewport().height()
        target = self._mid_idx * ROW_H - vp_h // 2 + ROW_H // 2
        target = max(0, target)
        self.tbl.verticalScrollBar().setValue(target)
        self._scroll_locked = False

    # ── Public API ────────────────────────────────────────────
    def get_visible_price_range(self) -> Tuple[float, float]:
        """
        Return (lo_price, hi_price) of the currently visible DOM window.

        When auto-scrolling, we compute this DETERMINISTICALLY from the
        mid_idx math (not from the scroll bar, which can lag by one event
        loop cycle). This makes the chart Y-axis sync perfectly.
        """
        if self._n == 0 or self._mid_idx < 0:
            return 0.0, 0.0

        vp_h = self.tbl.viewport().height()

        if self._auto_scroll:
            # Exact scroll position that _scroll_to_mid_now() sets
            scroll_top = max(0, self._mid_idx * ROW_H - vp_h // 2 + ROW_H // 2)
        else:
            scroll_top = self.tbl.verticalScrollBar().value()

        top_row = max(0,          scroll_top // ROW_H)
        bot_row = min(self._n - 1, (scroll_top + vp_h - 1) // ROW_H)

        # _sorted_prices is high→low, so top_row = highest price
        hi_price = self._sorted_prices[top_row]
        lo_price = self._sorted_prices[bot_row]
        return lo_price, hi_price

    def enable_auto_scroll(self):
        self._auto_scroll = True

    def reset(self):
        old = self._mid_idx
        self._cell_ctr    = 0
        self._mid_idx     = -1
        self._auto_scroll = True
        if 0 <= old < self._n:
            self.tbl.setSpan(old, 0, 1, 1)
        for r in range(self._n):
            price = self._sorted_prices[r]
            self._cell(r, 0, 'empty', 0.0, f'{price:.2f}')
            self._cell(r, 1, 'empty', 0.0, '')
            self._cell(r, 2, 'empty', 0.0, '')
        self.stat.setText('')
        self.tbl.viewport().update()


# ═════════════════════════════════════════════════════════════
#  CUSTOM TIME AXIS
# ═════════════════════════════════════════════════════════════
class TimeAxisItem(pg.AxisItem):
    def __init__(self, **kw):
        super().__init__(orientation='bottom', **kw)
        self._x2s:    Dict[int, int] = {}
        self._origin: Optional[int]  = None

    def set_origin(self, s: int):         self._origin = s
    def register(self, x: int, s: int):  self._x2s[x] = s
    def clear_all(self):
        self._x2s.clear(); self._origin = None

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            s = self._x2s.get(int(round(v)))
            if s and self._origin:
                e = max(0, s - self._origin)
                out.append(f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}")
            else:
                out.append('')
        return out


# ═════════════════════════════════════════════════════════════
#  TRADING CHART  — TradingView-style
# ═════════════════════════════════════════════════════════════
class TradingChart(pg.GraphicsLayoutWidget):
    """
    TradingView-style chart.

    X-axis behaviour
    ────────────────
    Auto-follow (default): latest tick is always placed at
        x = right_wall - CHART_RIGHT_PAD * CHART_VISIBLE  ticks
    so there is always empty space on the right, exactly like TradingView.
    The right wall is computed as  latest + CHART_RIGHT_PAD * CHART_VISIBLE,
    and the left wall is  right_wall - CHART_VISIBLE.

    When the user zooms or pans manually, auto-follow is suspended.
    Pressing PLAY re-enables it.

    Y-axis behaviour
    ────────────────
    Driven entirely by the orderbook's visible price window via sync_y_range().
    This makes the horizontal dotted mid-price line sit at the same fractional
    height in the chart as the mid-price row in the DOM panel.

    Badge
    ─────
    The price badge is right-aligned to the current right wall of the view,
    at the mid-price Y coordinate.  anchor=(1,0.5) so the badge never floats
    off the right edge.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(C_BG)
        self._prices:     List[float] = []
        self._ts_sec:     List[int]   = []
        self._tick_nums:  List[int]   = []
        self._origin_sec:  Optional[int] = None
        self._origin_tick: int = 0
        self._user_zoomed  = False
        self._last_mid     = 0.0
        self._build()

    @staticmethod
    def _to_sec(ts: int) -> int:
        if ts > 1_000_000_000_000_000: return int(ts // 1_000_000_000)
        if ts > 1_000_000_000_000:     return int(ts // 1_000_000)
        if ts > 10_000_000_000:        return int(ts // 1_000)
        return int(ts)

    def _build(self):
        mono8 = QFont('Menlo', 8)
        mono9 = QFont('Menlo', 9)

        self._taxis = TimeAxisItem()
        self.plt = self.addPlot(axisItems={'bottom': self._taxis})
        self.plt.setMenuEnabled(False)
        self.plt.setClipToView(True)
        self.plt.setDefaultPadding(0.0)

        for name in ('left', 'bottom', 'right', 'top'):
            ax = self.plt.getAxis(name)
            ax.setPen(pg.mkPen(C_GRID))
            ax.setTextPen(pg.mkPen(C_TEXT))
            ax.setStyle(tickFont=mono8)

        self.plt.showAxis('right')
        self.plt.hideAxis('left')
        ra = self.plt.getAxis('right')
        ra.setWidth(80)
        ra.setStyle(tickFont=mono9)

        self.plt.getAxis('bottom').setLabel(
            'Elapsed Time  HH:MM:SS', color=C_DIM, size='8pt')
        self.plt.showGrid(x=True, y=True, alpha=0.08)

        # Price curve
        self.curve = pg.PlotCurveItem(
            pen=pg.mkPen(color='#7070ee', width=2), skipFiniteCheck=True)
        self.plt.addItem(self.curve)

        # Coloured dots
        self.dots = pg.ScatterPlotItem(
            symbol='o', size=5, pxMode=True, pen=pg.mkPen(None))
        self.plt.addItem(self.dots)

        # Horizontal dotted last-price line (the "connected" mid line)
        self._lp_line = pg.InfiniteLine(
            angle=0,
            pen=pg.mkPen(color='#aaaaff', width=1, style=Qt.DotLine))
        self.plt.addItem(self._lp_line, ignoreBounds=True)

        # Price badge — anchor=(1,0.5): right edge of badge at (x, mid)
        self._badge = pg.TextItem(
            text='', color='#ffffff',
            fill=pg.mkBrush(35, 55, 200, 245),
            border=pg.mkPen('#8888ff', width=1),
            anchor=(1.0, 0.5))
        self._badge.setFont(QFont('Menlo', 9))
        self._badge.setZValue(60)
        self.plt.addItem(self._badge, ignoreBounds=True)

        # Vertical "now" dashed line
        self._now_line = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(color='#3a3a6a', width=1, style=Qt.DashLine))
        self.plt.addItem(self._now_line, ignoreBounds=True)

        # Crosshairs
        chp = pg.mkPen(C_DIM, width=1, style=Qt.DotLine)
        self.vl = pg.InfiniteLine(angle=90, pen=chp)
        self.hl = pg.InfiniteLine(angle=0,  pen=chp)
        self.vl.setZValue(10); self.hl.setZValue(10)
        self.plt.addItem(self.vl, ignoreBounds=True)
        self.plt.addItem(self.hl, ignoreBounds=True)

        # Tooltip
        self.tip = pg.TextItem(
            text='', color=C_TEXT,
            fill=pg.mkBrush(10, 10, 26, 220),
            border=pg.mkPen(C_GRID, width=1), anchor=(0, 1))
        self.tip.setFont(QFont('Menlo', 9))
        self.tip.setZValue(100)
        self.tip.setVisible(False)
        self.plt.addItem(self.tip)

        self.setMouseTracking(True)
        self._mprx = pg.SignalProxy(
            self.plt.scene().sigMouseMoved, rateLimit=30, slot=self._on_mouse)
        self.plt.vb.sigRangeChangedManually.connect(self._on_manual)

    # ── Y-range sync ──────────────────────────────────────────
    def sync_y_range(self, lo: float, hi: float):
        """
        Called every tick with the DOM's visible price window.
        Setting Y range here makes the dotted line at mid_price appear
        at the same fractional height as the mid row in the DOM.
        """
        if not self._user_zoomed and lo < hi:
            self.plt.setYRange(lo, hi, padding=0)

    # ── TradingView-style X range ─────────────────────────────
    def _compute_auto_x(self, n: int) -> Tuple[float, float, float]:
        """
        Returns (left_edge, right_edge, latest_x) for auto-follow.

        latest_x is placed at:
            right_edge - CHART_RIGHT_PAD * CHART_VISIBLE
        so there is always right-side empty space.
        """
        latest      = float(n - 1)
        right_pad   = CHART_VISIBLE * CHART_RIGHT_PAD
        right_edge  = latest + right_pad
        left_edge   = max(0.0, right_edge - CHART_VISIBLE)
        return left_edge, right_edge, latest

    # ── Per-tick update ───────────────────────────────────────
    def on_tick(self, mid: float, tick_num: int, timestamp: int):
        self._last_mid = mid
        sec = self._to_sec(timestamp)
        if self._origin_sec is None:
            self._origin_sec  = sec
            self._origin_tick = tick_num
            self._taxis.set_origin(sec)

        x_idx = len(self._prices)
        self._prices.append(mid)
        self._ts_sec.append(sec)
        self._tick_nums.append(tick_num)
        self._taxis.register(x_idx, sec)

        # Trim oldest points to cap memory
        if len(self._prices) > CHART_MAX_POINTS:
            trim = len(self._prices) - CHART_MAX_POINTS
            self._prices    = self._prices[trim:]
            self._ts_sec    = self._ts_sec[trim:]
            self._tick_nums = self._tick_nums[trim:]
            self._taxis._x2s = {i: s for i, s in enumerate(self._ts_sec)}

        n = len(self._prices)
        x = np.arange(n, dtype=np.float32)
        y = np.array(self._prices, dtype=np.float32)

        self.curve.setData(x, y)

        # Coloured dots
        if n > 1:
            dirs = np.diff(y, prepend=y[0])
            colours = [pg.mkBrush(0, 200, 90, 200) if d >= 0
                       else pg.mkBrush(210, 40, 55, 200) for d in dirs]
        else:
            colours = [pg.mkBrush(130, 130, 240, 180)]
        self.dots.setData(x, y, brush=colours)

        # Dotted horizontal mid-price line
        self._lp_line.setValue(mid)
        # Vertical "now" marker
        self._now_line.setValue(float(n - 1))

        # ── X-range: keep latest tick near right edge ─────────
        if not self._user_zoomed:
            left_x, right_x, _ = self._compute_auto_x(n)
            self.plt.setXRange(left_x, right_x, padding=0)
        else:
            right_x = self.plt.vb.viewRange()[0][1]

        # Badge pinned to the current right edge of the view
        self._badge.setPos(right_x, mid)
        self._badge.setText(f' ₹{mid:.2f} ')

    # ── Event handlers ────────────────────────────────────────
    def _on_manual(self, *_):
        self._user_zoomed = True

    def _on_mouse(self, evt):
        pos = evt[0]
        if not self.plt.sceneBoundingRect().contains(pos):
            self.tip.setVisible(False); return
        pt = self.plt.vb.mapSceneToView(pos)
        mx, my = pt.x(), pt.y()
        self.vl.setPos(mx); self.hl.setPos(my)
        if self._prices:
            idx   = int(max(0, min(round(mx), len(self._prices) - 1)))
            price = self._prices[idx]
            sec   = self._ts_sec[idx]
            tick  = self._tick_nums[idx]
            if self._origin_sec:
                e    = max(0, sec - self._origin_sec)
                toff = tick - self._origin_tick
                ts   = (f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}"
                        f":{toff:04d}")
            else:
                ts = str(sec)
            self.tip.setText(f"  ₹{price:.2f}\n  {ts}\n  tick #{tick}  ")
            self.tip.setPos(mx + 1, my)
            self.tip.setVisible(True)
        else:
            self.tip.setVisible(False)

    # ── Public API ────────────────────────────────────────────
    def resume_auto_range(self):
        self._user_zoomed = False

    def reset(self):
        self._prices.clear(); self._ts_sec.clear(); self._tick_nums.clear()
        self._origin_sec = None; self._origin_tick = 0
        self._user_zoomed = False; self._last_mid = 0.0
        self._taxis.clear_all()
        self.curve.setData([], []); self.dots.setData([], [])
        self._badge.setText(''); self.tip.setVisible(False)


# ═════════════════════════════════════════════════════════════
#  STATS BAR
# ═════════════════════════════════════════════════════════════
class StatsBar(QFrame):
    _FIELDS = [
        ('ticker',        'SYMBOL',  C_TEXT,  lambda v: str(v)),
        ('best_bid',      'BID',     C_BID,   lambda v: f'₹{float(v):,.2f}'),
        ('best_ask',      'ASK',     C_ASK,   lambda v: f'₹{float(v):,.2f}'),
        ('spread',        'SPREAD',  C_YELL,  lambda v: f'{float(v):.3f}'),
        ('mid_price',     'MID',     C_WHITE, lambda v: f'₹{float(v):,.2f}'),
        ('total_bid_qty', 'BID QTY', C_BID,   lambda v: f'{int(v):,}'),
        ('total_ask_qty', 'ASK QTY', C_ASK,   lambda v: f'{int(v):,}'),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background:{C_PANEL}; }}")
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6); lay.setSpacing(6)
        self._vals: Dict[str, QLabel] = {}
        for key, label, color, _ in self._FIELDS:
            box = QFrame()
            box.setStyleSheet(
                f"QFrame {{ background:{C_BG}; border:1px solid {C_GRID};"
                f" border-radius:3px; }}")
            vl = QVBoxLayout(box)
            vl.setContentsMargins(10, 3, 10, 3); vl.setSpacing(0)
            t = QLabel(label)
            t.setStyleSheet(
                f"QLabel {{ color:{C_DIM}; font:7px 'Menlo';"
                f" border:none; letter-spacing:1px; }}")
            v = QLabel("—")
            v.setStyleSheet(
                f"QLabel {{ color:{color}; font:bold 12px 'Menlo'; border:none; }}")
            v.setAlignment(Qt.AlignCenter)
            vl.addWidget(t); vl.addWidget(v)
            self._vals[key] = v
            lay.addWidget(box)
        lay.addStretch()
        self.ts = QLabel("—")
        self.ts.setStyleSheet(f"color:{C_DIM}; font:bold 10px 'Menlo';")
        lay.addWidget(self.ts)

    def refresh(self, row: pd.Series):
        for key, _, _, fmt in self._FIELDS:
            try: self._vals[key].setText(fmt(row[key]))
            except Exception: pass
        try:
            dt = str(row.get('datetime', ''))
            self.ts.setText(dt[:19] if dt else '—')
        except Exception: pass


# ═════════════════════════════════════════════════════════════
#  CONTROL BAR
# ═════════════════════════════════════════════════════════════
class ControlBar(QFrame):
    SPEED_MAP = {
        '0.25×': 0.25, '0.5×': 0.5, '1×': 1.0,
        '2×': 2.0, '5×': 5.0, '10×': 10.0,
        '25×': 25.0, 'MAX': 9999.0,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{C_PANEL}; border-bottom:1px solid {C_GRID}; }}")
        self.setFixedHeight(40)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4); lay.setSpacing(8)

        spd = QLabel("SPEED")
        spd.setStyleSheet(f"color:{C_DIM}; font:7px 'Menlo'; letter-spacing:1px;")
        self.speed_cb = QComboBox()
        self.speed_cb.addItems(list(self.SPEED_MAP.keys()))
        self.speed_cb.setCurrentIndex(2)
        self.speed_cb.setFixedWidth(75)
        self.speed_cb.setStyleSheet(f"""
            QComboBox {{ background:{C_BG}; color:{C_TEXT};
                border:1px solid {C_GRID}; padding:2px 6px; font:9px 'Menlo'; }}
            QComboBox::drop-down {{ border:none; width:16px; }}
            QComboBox QAbstractItemView {{
                background:{C_PANEL}; color:{C_TEXT};
                selection-background-color:{C_GRID}; }}
        """)
        for w in (spd, self.speed_cb): lay.addWidget(w)
        lay.addStretch()
        self.info = QLabel("Loading…")
        self.info.setStyleSheet(f"color:{C_DIM}; font:9px 'Menlo';")
        lay.addWidget(self.info)

    def set_info(self, m): self.info.setText(m)

    @property
    def speed(self):
        return self.SPEED_MAP.get(self.speed_cb.currentText(), 1.0)


# ═════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═════════════════════════════════════════════════════════════
class BookmapTerminal(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("📊  Bookmap Terminal  ·  NSE TCS-EQ  ·  L2 Depth")
        self.resize(1600, 900)
        self._apply_palette()
        self._build_ui()
        self._init_state()
        self._start_loading()

    def _apply_palette(self):
        pal = QPalette()
        pal.setColor(QPalette.Window,     QColor(C_BG))
        pal.setColor(QPalette.WindowText, QColor(C_TEXT))
        pal.setColor(QPalette.Base,       QColor(C_PANEL))
        pal.setColor(QPalette.Text,       QColor(C_TEXT))
        pal.setColor(QPalette.Button,     QColor(C_PANEL))
        pal.setColor(QPalette.ButtonText, QColor(C_TEXT))
        self.setPalette(pal)
        self.setStyleSheet(f"QMainWindow, QWidget {{ background:{C_BG}; }}")

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self.stats = StatsBar()
        lay.addWidget(self.stats)

        self.ctrl = ControlBar()
        self.ctrl.speed_cb.currentTextChanged.connect(self._speed_changed)
        lay.addWidget(self.ctrl)

        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(3)
        spl.setStyleSheet(f"QSplitter::handle {{ background:{C_GRID}; }}")

        self.dom   = OrderbookWidget()
        self.chart = TradingChart()

        spl.addWidget(self.dom)
        spl.addWidget(self.chart)
        spl.setSizes([430, 1170])
        spl.setCollapsible(0, False)
        spl.setCollapsible(1, False)
        lay.addWidget(spl, stretch=1)

        sb = QStatusBar()
        sb.setStyleSheet(
            f"QStatusBar {{ background:{C_PANEL}; color:{C_DIM};"
            f" font:9px 'Menlo'; border-top:1px solid {C_GRID}; }}")
        self.setStatusBar(sb)
        self.sb = sb

    def _init_state(self):
        self.tick_df       = None
        self.ob_index      = None
        self.sorted_prices = None
        self.tick_idx      = 0
        self._timer        = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.tracker       = None

    def _start_loading(self):
        self.sb.showMessage("⏳  Loading data…")
        self._loader = DataLoader(TICK_FILE, ORDERBOOK_FILE)
        self._loader.progress.connect(self._on_prog)
        self._loader.finished.connect(self._on_loaded)
        self._loader.start()

    @pg.QtCore.pyqtSlot(int, str)
    def _on_prog(self, pct, msg):
        icon = '❌' if pct < 0 else '⏳'
        self.sb.showMessage(f"{icon}  {abs(pct)}%  ·  {msg}")

    @pg.QtCore.pyqtSlot(object, object, object)
    def _on_loaded(self, tick_df, ob_index, sorted_prices):
        self.tick_df       = tick_df
        self.ob_index      = ob_index
        self.sorted_prices = sorted_prices

        n, p = len(tick_df), len(sorted_prices)
        self.sb.showMessage(
            f"✅  {n:,} ticks · {p:,} unique prices · Auto-playing…")
        self.ctrl.set_info(f"{n:,} ticks · {p:,} price levels")

        self.tracker = OrderbookTracker(sorted_prices)
        self.dom.initialize(sorted_prices)

        # Auto-start playback at 1×
        self._timer.start(max(1, int(500 / self.ctrl.speed)))
        self.sb.showMessage("▶  Playing…  ·  Scroll DOM freely  ·  "
                            "Pan/zoom chart (auto-follow pauses until loop restarts)")

    def _speed_changed(self, _):
        if self._timer.isActive():
            self._timer.setInterval(max(1, int(500 / self.ctrl.speed)))

    def _step(self):
        if self.tick_df is None:
            return

        # Loop back to start when exhausted
        if self.tick_idx >= len(self.tick_df):
            self.tick_idx = 0
            if self.tracker: self.tracker.reset()
            self.dom.reset()
            self.chart.reset()
            self.dom.enable_auto_scroll()
            self.chart.resume_auto_range()

        row  = self.tick_df.iloc[self.tick_idx]
        ts   = int(row['timestamp'])
        snap = self.ob_index.get(ts, pd.DataFrame())
        mid  = float(row.get('mid_price', 0) or 0)

        # 1. Update tracker — get ascending mid index
        asc_mid_idx = self.tracker.update(snap, mid)

        # 2. Refresh DOM — phase 1 (mid row) runs always; phase 2 throttled
        self.dom.refresh(self.tracker, mid, asc_mid_idx)

        # 3. Get DOM visible price range AFTER mid row + scroll update
        lo, hi = self.dom.get_visible_price_range()

        # 4. Sync chart Y range with DOM window
        if lo < hi:
            self.chart.sync_y_range(lo, hi)

        # 5. Advance chart (X auto-follow keeps latest at right edge)
        self.chart.on_tick(mid, self.tick_idx, ts)

        # 6. Stats
        self.stats.refresh(row)
        pct = int(100 * self.tick_idx / len(self.tick_df))
        self.ctrl.set_info(
            f"Tick  {self.tick_idx:>6,} / {len(self.tick_df):,}  ({pct}%)")
        self.tick_idx += 1

    def closeEvent(self, ev):
        self._timer.stop()
        if hasattr(self, '_loader') and self._loader.isRunning():
            self._loader.quit(); self._loader.wait(3000)
        super().closeEvent(ev)


# ═════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════
def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,    True)
    app = QApplication(sys.argv)
    app.setApplicationName("Bookmap Terminal")
    app.setStyle("Fusion")
    app.setStyleSheet(f"""
        QWidget {{ background:{C_BG}; color:{C_TEXT}; }}
        QScrollBar:vertical {{
            background:{C_PANEL}; width:6px; border:none; }}
        QScrollBar::handle:vertical {{
            background:{C_GRID}; border-radius:3px; }}
        QToolTip {{
            background:{C_PANEL}; color:{C_TEXT};
            border:1px solid {C_GRID}; font:9px 'Menlo'; }}
    """)
    win = BookmapTerminal()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()