#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     BOOKMAP TERMINAL  —  NSE Indian Market  L2 Depth        ║
╠══════════════════════════════════════════════════════════════╣
║  LEFT  : Scrollable DOM table  (50 bid + MID + 50 ask)      ║
║          Bids: highest (nearest mid) at bottom of bid zone  ║
║          Asks: lowest  (nearest mid) at top of ask zone     ║
║          = mid in the centre, both sides radiate outward    ║
║                                                              ║
║  RIGHT : TradingView-style mid-price chart                   ║
║          · Custom TimeAxisItem → HH:MM:SS:tick elapsed      ║
║          · RIGHT price Y-axis (dense labels, no left axis)  ║
║          · Dotted horizontal "last price" line               ║
║          · Blue highlighted current-price badge on right     ║
║          · Crosshairs (dotted H/V) following mouse          ║
║          · Tooltip: price / elapsed time / tick              ║
║          · Zoom/pan → auto-pauses playback                   ║
║          · Press PLAY → resumes auto-scroll                  ║
╚══════════════════════════════════════════════════════════════╝

Install:
    pip install PyQt5 pyqtgraph numpy pandas

Run:
    python3 bookmap_terminal.py
"""

import sys, gc, logging
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QSplitter, QComboBox, QStatusBar,
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
    "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/"
    "2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/"
    "NSE_TCS-EQ_tick_20260402_095152.csv"
)
ORDERBOOK_FILE = (
    "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/"
    "2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/"
    "NSE_TCS-EQ_orderbook_20260402_095152.csv"
)

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
PRICE_LEVELS     = 50        # rows per side
ROW_H            = 19        # DOM row height (px)
OB_CHUNK_SIZE    = 10_000    # OB CSV read chunk
DOM_UPDATE_EVERY = 2         # throttle DOM redraws
CHART_MAX_POINTS = 6_000     # max price points kept in RAM

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

BID_BAR_RGB = (0,   200, 100)
ASK_BAR_RGB = (230,  45,  65)

BID_BG   = QColor( 0,  42,  18)
ASK_BG   = QColor(42,   8,  14)
MID_BG   = QColor(22,  22,  55)
EMPTY_BG = QColor( 7,   7,  14)
SEP_CLR  = QColor(26,  26,  48)

pg.setConfigOptions(antialias=False, useOpenGL=False)

_F9  = QFont('Menlo', 9)
_F8  = QFont('Menlo', 8)
_F9B = QFont('Menlo', 9)
_F9B.setBold(True)


# ═════════════════════════════════════════════════════════════
#  DATA LOADER
# ═════════════════════════════════════════════════════════════
class DataLoader(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)

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
            total = 0
            for chunk in pd.read_csv(self.ob_path, dtype={
                'ticker': 'category', 'timestamp': np.int64,
                'price': np.float32, 'bid_qty': np.int32,
                'ask_qty': np.int32, 'bid_orders': np.int16,
                'ask_orders': np.int16, 'side': 'category',
            }, chunksize=OB_CHUNK_SIZE):
                for ts, grp in chunk.groupby('timestamp', sort=False):
                    k = int(ts)
                    ob_index[k] = (
                        pd.concat([ob_index[k], grp], ignore_index=True)
                        if k in ob_index else grp.reset_index(drop=True)
                    )
                total += len(chunk)
                self.progress.emit(
                    min(90, 15 + int(75 * total / max(total, 1))),
                    f"OB {total:,} rows…"
                )
            gc.collect()
            self.progress.emit(100, "Ready!")
            self.finished.emit(tick_df, ob_index)
        except Exception as e:
            log.exception("DataLoader failed")
            self.progress.emit(-1, f"ERROR: {e}")


# ═════════════════════════════════════════════════════════════
#  PRICE LEVEL DATA
# ═════════════════════════════════════════════════════════════
@dataclass
class PriceLevelData:
    price:             float
    side:              str
    cumulative_qty:    int = 0
    cumulative_orders: int = 0
    current_qty:       int = 0
    current_orders:    int = 0

    def update(self, qty: int, orders: int):
        if qty > self.current_qty:
            self.cumulative_qty    += qty - self.current_qty
        if orders > self.current_orders:
            self.cumulative_orders += orders - self.current_orders
        self.current_qty    = qty
        self.current_orders = orders


# ═════════════════════════════════════════════════════════════
#  ORDERBOOK TRACKER
# ═════════════════════════════════════════════════════════════
class OrderbookTracker:
    """
    Returns:
      bids → sorted HIGH→LOW  (index 0 = nearest mid)
      asks → sorted LOW→HIGH  (index 0 = nearest mid)
    """
    def __init__(self):
        self.bid_levels: Dict[float, PriceLevelData] = {}
        self.ask_levels: Dict[float, PriceLevelData] = {}

    def update(self, snap: pd.DataFrame, mid: float) -> Dict:
        if snap.empty:
            return {'bids': [], 'asks': [], 'mid': mid}

        prices   = snap['price'].values.astype(np.float64)
        bid_qtys = snap['bid_qty'].values.astype(np.int32)
        ask_qtys = snap['ask_qty'].values.astype(np.int32)
        bid_ords = (snap['bid_orders'].values.astype(np.int32)
                    if 'bid_orders' in snap.columns
                    else np.zeros(len(snap), np.int32))
        ask_ords = (snap['ask_orders'].values.astype(np.int32)
                    if 'ask_orders' in snap.columns
                    else np.zeros(len(snap), np.int32))

        cur_b: set = set()
        cur_a: set = set()
        for i in range(len(prices)):
            p = round(float(prices[i]), 2)
            bq, aq = int(bid_qtys[i]), int(ask_qtys[i])
            bo, ao = int(bid_ords[i]),  int(ask_ords[i])
            if bq > 0:
                cur_b.add(p)
                if p not in self.bid_levels:
                    self.bid_levels[p] = PriceLevelData(p, 'bid')
                self.bid_levels[p].update(bq, bo)
            if aq > 0:
                cur_a.add(p)
                if p not in self.ask_levels:
                    self.ask_levels[p] = PriceLevelData(p, 'ask')
                self.ask_levels[p].update(aq, ao)

        # bids: HIGH→LOW (nearest mid first)
        active_bids = sorted(
            (lv for lv in self.bid_levels.values() if lv.price in cur_b),
            key=lambda x: x.price, reverse=True,
        )[:PRICE_LEVELS]

        # asks: LOW→HIGH (nearest mid first)
        active_asks = sorted(
            (lv for lv in self.ask_levels.values() if lv.price in cur_a),
            key=lambda x: x.price, reverse=False,
        )[:PRICE_LEVELS]

        return {
            'bids': [(lv.price, lv.cumulative_qty, lv.cumulative_orders)
                     for lv in active_bids],
            'asks': [(lv.price, lv.cumulative_qty, lv.cumulative_orders)
                     for lv in active_asks],
            'mid': mid,
        }

    def reset(self):
        self.bid_levels.clear()
        self.ask_levels.clear()


# ═════════════════════════════════════════════════════════════
#  DELEGATES
# ═════════════════════════════════════════════════════════════
class PriceDelegate(QStyledItemDelegate):
    def paint(self, p: QPainter, opt, idx):
        p.save()
        rt   = idx.data(Qt.UserRole) or 'empty'
        text = str(idx.data(Qt.DisplayRole) or '')
        bg   = {'bid': BID_BG, 'ask': ASK_BG,
                'mid': MID_BG}.get(rt, EMPTY_BG)
        p.fillRect(opt.rect, bg)
        p.setPen(QPen(SEP_CLR, 1))
        p.drawLine(opt.rect.topRight(), opt.rect.bottomRight())
        if rt == 'mid':
            p.setFont(_F9B); p.setPen(QColor(C_WHITE))
            p.drawText(opt.rect, Qt.AlignCenter, text)
        elif rt in ('bid', 'ask'):
            p.setFont(_F9)
            p.setPen(QColor(C_BID if rt == 'bid' else C_ASK))
            p.drawText(opt.rect.adjusted(8, 0, -4, 0),
                       Qt.AlignVCenter | Qt.AlignLeft, text)
        elif text:
            p.setFont(_F8); p.setPen(QColor(C_DIM))
            p.drawText(opt.rect, Qt.AlignCenter, '·')
        p.restore()

    def sizeHint(self, opt, idx):
        return QSize(0, ROW_H)


class BarDelegate(QStyledItemDelegate):
    def paint(self, p: QPainter, opt, idx):
        p.save()
        rt  = idx.data(Qt.UserRole)     or 'empty'
        iv  = idx.data(Qt.UserRole + 1) or 0.0
        txt = str(idx.data(Qt.DisplayRole) or '')
        bg  = {'bid': BID_BG, 'ask': ASK_BG,
               'mid': MID_BG}.get(rt, EMPTY_BG)
        p.fillRect(opt.rect, bg)
        if rt == 'mid':
            p.restore(); return
        # bar
        if rt in ('bid', 'ask') and iv > 0.015:
            br = 0.25 + iv * 0.75
            r, g, b = BID_BAR_RGB if rt == 'bid' else ASK_BAR_RGB
            bw = max(3, int(opt.rect.width() * iv))
            p.fillRect(
                QRect(opt.rect.right() - bw, opt.rect.top() + 2,
                      bw, opt.rect.height() - 4),
                QColor(int(r*br), int(g*br), int(b*br))
            )
        # text
        if txt and rt in ('bid', 'ask'):
            p.setFont(_F8); p.setPen(QColor(C_TEXT))
            p.drawText(opt.rect.adjusted(5, 0, -4, 0),
                       Qt.AlignVCenter | Qt.AlignLeft, txt)
        elif rt == 'empty':
            p.setFont(_F8); p.setPen(QColor(C_DIM))
            p.drawText(opt.rect, Qt.AlignCenter, '·')
        p.restore()

    def sizeHint(self, opt, idx):
        return QSize(0, ROW_H)


# ═════════════════════════════════════════════════════════════
#  ORDERBOOK WIDGET
# ═════════════════════════════════════════════════════════════
class OrderbookWidget(QFrame):
    """
    Rolling window: shows 51 price levels (25 bids + mid + 25 asks) centered on mid price.
    Automatically adjusts as mid price moves through price levels.
    
    Display (top → bottom):
    ┌ BIDs header │ All Bid Quantity │ All Bid Orders ┐
    │ row  0: BID  25 levels up (farthest from mid)  │
    │ …                                               │
    │ row 24: HIGHEST BID (nearest mid)               │
    │ row 25: ─── MID PRICE ₹xxxx.xx ───  (3-col span)│
    │ row 26: LOWEST ASK (nearest mid)                │
    │ …                                               │
    │ row 50: ASK  25 levels down (farthest from mid) │
    └ ASKs footer │ All Ask Quantity │ All Ask Orders ┘
    
    Rolling behavior:
    - Shows nearest 25 bids (bids[0:25]) and nearest 25 asks (asks[0:25])
    - As orderbook updates with new prices, different levels enter/leave top 25
    - Mid price stays at row 25, with 25 levels above and below
    - Window automatically "rolls" as mid price moves and new orders flow
    """
    DISPLAY_LEVELS = 51       # Show 51 rows total (25 bids + mid + 25 asks)
    DISPLAY_BIDS = 25         # Show 25 bid levels
    DISPLAY_ASKS = 25         # Show 25 ask levels
    MID_ROW = DISPLAY_LEVELS // 2  # Row 25 (0-indexed)
    TOTAL_ROWS = DISPLAY_LEVELS

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background:{C_BG}; }}")
        self.setMinimumWidth(400)  # Increased for larger display
        self.setMaximumWidth(700)  # Increased for larger display
        self._max_qty    = 1
        self._max_orders = 1
        self._tick_ctr   = 0
        self._centred    = False
        self._last_mid_price = 0.0
        self._all_bids = []  # Top 50 bids from tracker, sorted HIGH→LOW
        self._all_asks = []  # Top 50 asks from tracker, sorted LOW→HIGH
        self._current_display_bids = []  # Currently displayed 25 bids
        self._current_display_asks = []  # Currently displayed 25 asks
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._hdr(
            [("BIDs", 1), ("All Bid Quantity", 2), ("All Bid Orders", 2)], C_BID
        ))

        self.tbl = QTableWidget(self.TOTAL_ROWS, 3)
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
            QScrollBar:vertical {{ background:{C_PANEL}; width:6px; border:none; }}
            QScrollBar::handle:vertical {{
                background:{C_GRID}; border-radius:3px; min-height:20px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height:0px; }}
        """)

        self.tbl.setColumnWidth(0, 75)
        for c in (1, 2):
            self.tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.Stretch)
        for r in range(self.TOTAL_ROWS):
            self.tbl.setRowHeight(r, ROW_H)

        self.tbl.setItemDelegateForColumn(0, PriceDelegate(self.tbl))
        self.tbl.setItemDelegateForColumn(1, BarDelegate(self.tbl))
        self.tbl.setItemDelegateForColumn(2, BarDelegate(self.tbl))

        for r in range(self.TOTAL_ROWS):
            for c in range(3):
                it = QTableWidgetItem('')
                it.setData(Qt.UserRole,     'empty')
                it.setData(Qt.UserRole + 1, 0.0)
                self.tbl.setItem(r, c, it)

        self.tbl.setSpan(self.MID_ROW, 0, 1, 3)
        mi = self.tbl.item(self.MID_ROW, 0)
        mi.setData(Qt.UserRole,    'mid')
        mi.setData(Qt.DisplayRole, '── MID PRICE ──')

        root.addWidget(self.tbl, stretch=1)
        root.addWidget(self._hdr(
            [("ASKs", 1), ("All Ask Quantity", 2), ("All Ask Orders", 2)], C_ASK
        ))

        self.stat = QLabel("")
        self.stat.setStyleSheet(
            f"color:{C_DIM}; font:7px 'Menlo'; background:{C_GRID}; padding:3px 6px;"
        )
        root.addWidget(self.stat)

    @staticmethod
    def _hdr(cols, color):
        f = QFrame()
        f.setStyleSheet(f"QFrame {{ background:{C_GRID}; border:none; }}")
        f.setFixedHeight(20)
        lay = QHBoxLayout(f)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)
        for text, stretch in cols:
            lb = QLabel(text)
            lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(
                f"color:{color}; font:bold 7px 'Menlo';"
                f" letter-spacing:1px; padding:0;"
            )
            lay.addWidget(lb, stretch)
        return f

    def _cell(self, r, c, rt, iv, txt):
        it = self.tbl.item(r, c)
        if it:
            it.setData(Qt.UserRole,     rt)
            it.setData(Qt.UserRole + 1, float(iv))
            it.setData(Qt.DisplayRole,  txt)

    def _row(self, r, rt, price_str, qty, qi, orders, oi):
        self._cell(r, 0, rt, 0.0, price_str)
        self._cell(r, 1, rt, qi,  f'{qty:,}'    if qty    else '')
        self._cell(r, 2, rt, oi,  f'{orders:,}' if orders else '')

    def _empty(self, r):
        for c in range(3):
            self._cell(r, c, 'empty', 0.0, '')

    # ── Refresh ─────────────────────────────────────────────────────────────
    def refresh(self, ob: Dict, bid: float, ask: float, mid: float):
        self._tick_ctr += 1
        if self._tick_ctr > 1 and self._tick_ctr % DOM_UPDATE_EVERY != 0:
            return

        bids: List = ob.get('bids', [])   # HIGH→LOW  (index 0 = nearest mid)
        asks: List = ob.get('asks', [])   # LOW→HIGH   (index 0 = nearest mid)
        
        # Store all bids/asks for rolling window calculation
        self._all_bids = bids  # Top 50 bids from tracker
        self._all_asks = asks  # Top 50 asks from tracker
        self._last_mid_price = mid

        # Calculate max quantities for intensity bars
        all_q = [q for _, q, _ in bids + asks]
        all_o = [o for _, _, o in bids + asks]
        if all_q: self._max_qty    = max(self._max_qty    * 0.96, max(all_q))
        if all_o: self._max_orders = max(self._max_orders * 0.96, max(all_o))
        mq, mo = max(self._max_qty, 1), max(self._max_orders, 1)

        # ─────────────────────────────────────────────────────────────────
        # ROLLING WINDOW: Show 51 levels centered on mid price (25+mid+25)
        # Rolling behavior: Display nearest 25 bids and 25 asks to current mid
        # As orderbook updates, different prices enter/leave the top 25
        # ─────────────────────────────────────────────────────────────────
        
        # Take 25 nearest bids (highest bids) and 25 nearest asks (lowest asks)
        # These are closest to mid price, creating a window around it
        display_bids = bids[:self.DISPLAY_BIDS] if len(bids) >= self.DISPLAY_BIDS else bids[:]
        display_asks = asks[:self.DISPLAY_ASKS] if len(asks) >= self.DISPLAY_ASKS else asks[:]
        
        # Pad with None if not enough levels available
        while len(display_bids) < self.DISPLAY_BIDS:
            display_bids = [None] + display_bids  # Add to the top (farthest from mid)
        while len(display_asks) < self.DISPLAY_ASKS:
            display_asks.append(None)  # Add to the bottom (farthest from mid)
        
        # Store current display for reference
        self._current_display_bids = display_bids[:]
        self._current_display_asks = display_asks[:]
        
        # Display rows 0-24: BIDs (reversed so farthest/oldest is at top)
        display_bids_rev = list(reversed(display_bids))
        for r in range(self.DISPLAY_BIDS):
            item = display_bids_rev[r]
            if item is None:
                self._empty(r)
            else:
                price, qty, orders = item
                self._row(r, 'bid', f'{price:.2f}',
                          qty,    min(1.0, qty    / mq),
                          orders, min(1.0, orders / mo))

        # MID ROW at row 25 - always shows current mid price
        mi = self.tbl.item(self.MID_ROW, 0)
        if mi:
            mi.setData(Qt.DisplayRole, f'── MID PRICE  ₹{mid:.2f} ──')

        # Display rows 26-50: ASKs (nearest to farthest)
        for i in range(self.DISPLAY_ASKS):
            r = self.MID_ROW + 1 + i
            item = display_asks[i]
            if item is None:
                self._empty(r)
            else:
                price, qty, orders = item
                self._row(r, 'ask', f'{price:.2f}',
                          qty,    min(1.0, qty    / mq),
                          orders, min(1.0, orders / mo))

        self.tbl.viewport().update()

        # Center the view on mid row on first display
        if not self._centred:
            self._centred = True
            QTimer.singleShot(120, self._centre)

        # Update footer statistics
        tb = int(sum(q for _, q, _ in bids))
        ta = int(sum(q for _, q, _ in asks))
        self.stat.setText(
            f"  BID Qty: {tb:>10,}  │  ASK Qty: {ta:>10,}  │  LEVELS: {len(bids)}/{len(asks)}"
        )

    def get_visible_price_range(self) -> Tuple[float, float]:
        """
        Get the highest visible bid and lowest visible ask prices.
        Used to align chart Y-axis with orderbook display.
        Returns: (highest_visible_bid, lowest_visible_ask)
        """
        if not self._current_display_bids or not self._current_display_asks:
            return 0.0, 0.0
        
        # Find highest bid (last in reversed list = first in original)
        highest_bid = 0.0
        for item in self._current_display_bids:
            if item is not None:
                price, _, _ = item
                highest_bid = max(highest_bid, price)
        
        # Find lowest ask (first in list since sorted ascending)
        lowest_ask = float('inf')
        for item in self._current_display_asks:
            if item is not None:
                price, _, _ = item
                lowest_ask = min(lowest_ask, price)
        
        # Return valid range or defaults
        if lowest_ask == float('inf'):
            lowest_ask = self._last_mid_price + 0.5
        if highest_bid == 0:
            highest_bid = self._last_mid_price - 0.5
        
        return highest_bid, lowest_ask

    def _centre(self):
        tgt = max(0, self.MID_ROW * ROW_H - self.tbl.viewport().height() // 2)
        self.tbl.verticalScrollBar().setValue(tgt)

    def reset(self):
        self._tick_ctr = 0
        self._centred  = False
        for r in range(self.TOTAL_ROWS):
            if r == self.MID_ROW:
                mi = self.tbl.item(r, 0)
                if mi: mi.setData(Qt.DisplayRole, '── MID PRICE ──')
                continue
            self._empty(r)
        self.stat.setText('')
        self.tbl.viewport().update()


# ═════════════════════════════════════════════════════════════
#  CUSTOM TIME AXIS
# ═════════════════════════════════════════════════════════════
class TimeAxisItem(pg.AxisItem):
    def __init__(self, **kw):
        super().__init__(orientation='bottom', **kw)
        self._x2s:   Dict[int, int]  = {}
        self._origin: Optional[int]  = None

    def set_origin(self, s: int):  self._origin = s
    def register(self, x: int, s: int): self._x2s[x] = s
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
#  TRADING CHART
# ═════════════════════════════════════════════════════════════
class TradingChart(pg.GraphicsLayoutWidget):
    """
    TradingView-style chart:
    · Dark grid, purple price line, coloured dots
    · Right-side price axis only
    · Dotted "last price" infinite line + blue badge at edge
    · Crosshairs + tooltip
    · Zoom → emits sigPauseRequested
    """
    sigPauseRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(C_BG)
        self._prices:    List[float] = []
        self._ts_sec:    List[int]   = []
        self._tick_nums: List[int]   = []
        self._origin_sec:  Optional[int] = None
        self._origin_tick: int = 0
        self._user_zoomed  = False
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

        # Style all axes
        for name in ('left', 'bottom', 'right', 'top'):
            ax = self.plt.getAxis(name)
            ax.setPen(pg.mkPen(C_GRID))
            ax.setTextPen(pg.mkPen(C_TEXT))
            ax.setStyle(tickFont=mono8)

        # Right axis visible (TradingView style), left hidden
        self.plt.showAxis('right')
        self.plt.hideAxis('left')
        ra = self.plt.getAxis('right')
        ra.setWidth(72)
        ra.setStyle(tickFont=mono9)

        self.plt.getAxis('bottom').setLabel(
            'Elapsed Time  HH:MM:SS  :  tick offset',
            color=C_DIM, size='8pt'
        )
        self.plt.showGrid(x=True, y=True, alpha=0.08)

        # Price curve
        self.curve = pg.PlotCurveItem(
            pen=pg.mkPen(color='#7070ee', width=2),
            skipFiniteCheck=True,
        )
        self.plt.addItem(self.curve)

        # Scatter dots
        self.dots = pg.ScatterPlotItem(
            symbol='o', size=5, pxMode=True, pen=pg.mkPen(None)
        )
        self.plt.addItem(self.dots)

        # Last-price dotted line
        self._lp_line = pg.InfiniteLine(
            angle=0,
            pen=pg.mkPen(color='#9999dd', width=1, style=Qt.DotLine),
        )
        self.plt.addItem(self._lp_line, ignoreBounds=True)

        # Price badge (TextItem anchored at right edge)
        self._badge = pg.TextItem(
            text='', color='#ffffff',
            fill=pg.mkBrush(35, 55, 200, 245),
            border=pg.mkPen('#8888ff', width=1),
            anchor=(0.0, 0.5),
        )
        self._badge.setFont(QFont('Menlo', 9))
        self._badge.setZValue(60)
        self.plt.addItem(self._badge, ignoreBounds=True)

        # Right-edge "now" vertical dashed line
        self._now_line = pg.InfiniteLine(
            angle=90,
            pen=pg.mkPen(color='#3a3a6a', width=1, style=Qt.DashLine),
        )
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
            border=pg.mkPen(C_GRID, width=1),
            anchor=(0, 1),
        )
        self.tip.setFont(QFont('Menlo', 9))
        self.tip.setZValue(100)
        self.tip.setVisible(False)
        self.plt.addItem(self.tip)

        # Signals
        self.setMouseTracking(True)
        self._mprx = pg.SignalProxy(
            self.plt.scene().sigMouseMoved,
            rateLimit=30, slot=self._on_mouse,
        )
        self.plt.vb.sigRangeChangedManually.connect(self._on_manual)

    # ── Per-tick ────────────────────────────────────────────────────────────
    def on_tick(self, mid: float, tick_num: int, timestamp: int):
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

        # Memory cap
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

        # Colour dots by price direction
        if n > 1:
            dirs   = np.diff(y, prepend=y[0])
            colours = [
                pg.mkBrush(0, 200, 90, 200)  if d >= 0
                else pg.mkBrush(210, 40, 55, 200)
                for d in dirs
            ]
        else:
            colours = [pg.mkBrush(130, 130, 240, 180)]
        self.dots.setData(x, y, brush=colours)

        # Last-price line
        self._lp_line.setValue(mid)
        self._now_line.setValue(n - 1)

        # Badge at right edge of visible view
        vr = self.plt.vb.viewRange()
        bx = vr[0][1] - (vr[0][1] - vr[0][0]) * 0.003
        self._badge.setPos(bx, mid)
        self._badge.setText(f' ₹{mid:.2f} ')

        if not self._user_zoomed:
            self._auto_range(x, y, n)

    def _auto_range(self, x, y, n):
        vs = max(0, n - 600)
        self.plt.setXRange(vs, n, padding=0.02)
        vy = y[vs:]
        if len(vy):
            lo, hi = vy.min(), vy.max()
            pad = max(hi - lo, 0.5) * 0.12
            self.plt.setYRange(lo - pad, hi + pad, padding=0)

    def sync_y_range_with_orderbook(self, visible_bid_high: float, visible_ask_low: float):
        """
        Align chart Y-axis range with orderbook's visible price range.
        This ensures the mid-price dotted line appears at the same visual height
        as the orderbook's mid-price row.
        
        Args:
            visible_bid_high: Highest visible bid price in orderbook
            visible_ask_low:  Lowest visible ask price in orderbook
        """
        if visible_bid_high > 0 and visible_ask_low > 0:
            # Set Y-range to cover the visible orderbook price range with padding
            price_span = visible_ask_low - visible_bid_high
            if price_span < 0.1:
                price_span = 1.0  # Minimum span for stability
            
            pad = price_span * 0.05  # 5% padding
            y_low = visible_bid_high - pad
            y_high = visible_ask_low + pad
            
            # Only update if not manually zoomed
            if not self._user_zoomed:
                self.plt.setYRange(y_low, y_high, padding=0)

    def _on_manual(self, *_):
        self._user_zoomed = True
        # Don't pause - just skip auto-range while zoomed

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
            self.tip.setText(
                f"  ₹{price:.2f}\n  {ts}\n  tick #{tick}  "
            )
            self.tip.setPos(mx + 1, my)
            self.tip.setVisible(True)
        else:
            self.tip.setVisible(False)

    def resume_auto_range(self):
        self._user_zoomed = False

    def reset(self):
        self._prices.clear(); self._ts_sec.clear(); self._tick_nums.clear()
        self._origin_sec = None; self._origin_tick = 0
        self._user_zoomed = False
        self._taxis.clear_all()
        self.curve.setData([], []); self.dots.setData([], [])
        self._badge.setText('')
        self.tip.setVisible(False)


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
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)
        self._vals: Dict[str, QLabel] = {}
        for key, label, color, _ in self._FIELDS:
            box = QFrame()
            box.setStyleSheet(
                f"QFrame {{ background:{C_BG}; border:1px solid {C_GRID};"
                f" border-radius:3px; }}"
            )
            vl = QVBoxLayout(box)
            vl.setContentsMargins(10, 3, 10, 3); vl.setSpacing(0)
            t = QLabel(label)
            t.setStyleSheet(f"QLabel {{ color:{C_DIM}; font:7px 'Menlo'; border:none; letter-spacing:1px; }}")
            v = QLabel("—")
            v.setStyleSheet(f"QLabel {{ color:{color}; font:bold 12px 'Menlo'; border:none; }}")
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
        '0.25×': 0.25, '0.5×': 0.5,  '1×':   1.0,
        '2×':    2.0,  '5×':   5.0,  '10×':  10.0,
        '25×':  25.0,  'MAX': 9999.0,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{C_PANEL}; border-bottom:1px solid {C_GRID}; }}"
        )
        self.setFixedHeight(40)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4); lay.setSpacing(8)

        self.btn_play  = self._btn("▶  PLAY",  '#174a17')
        self.btn_pause = self._btn("⏸  PAUSE", '#4a3a10')
        self.btn_stop  = self._btn("■  RESET", '#4a1414')

        sep = QFrame(); sep.setFixedSize(1, 20)
        sep.setStyleSheet(f"background:{C_GRID};")

        spd = QLabel("SPEED")
        spd.setStyleSheet(f"color:{C_DIM}; font:7px 'Menlo'; letter-spacing:1px;")

        self.speed_cb = QComboBox()
        self.speed_cb.addItems(list(self.SPEED_MAP.keys()))
        self.speed_cb.setCurrentIndex(2); self.speed_cb.setFixedWidth(75)
        self.speed_cb.setStyleSheet(f"""
            QComboBox {{ background:{C_BG}; color:{C_TEXT}; border:1px solid {C_GRID};
                         padding:2px 6px; font:9px 'Menlo'; }}
            QComboBox::drop-down {{ border:none; width:16px; }}
            QComboBox QAbstractItemView {{ background:{C_PANEL}; color:{C_TEXT};
                selection-background-color:{C_GRID}; }}
        """)

        for w in (self.btn_play, self.btn_pause, self.btn_stop, sep, spd, self.speed_cb):
            lay.addWidget(w)
        lay.addStretch()
        self.info = QLabel("Load data to begin")
        self.info.setStyleSheet(f"color:{C_DIM}; font:9px 'Menlo';")
        lay.addWidget(self.info)

    @staticmethod
    def _btn(text, bg):
        b = QPushButton(text)
        b.setStyleSheet(f"""
            QPushButton {{ background:{bg}; color:#e0e0e0; border:none;
                padding:4px 14px; border-radius:3px; font:bold 9px 'Menlo'; }}
            QPushButton:hover   {{ background:#2a7a2a; }}
            QPushButton:pressed {{ background:#0f3a0f; }}
            QPushButton:disabled {{ background:#1a1a2a; color:#404050; }}
        """)
        return b

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
        root = QWidget(); self.setCentralWidget(root)
        lay  = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        self.stats = StatsBar(); lay.addWidget(self.stats)

        self.ctrl = ControlBar()
        self.ctrl.btn_play.clicked.connect(self._play)
        self.ctrl.btn_pause.clicked.connect(self._pause)
        self.ctrl.btn_stop.clicked.connect(self._reset)
        self.ctrl.speed_cb.currentTextChanged.connect(self._speed)
        lay.addWidget(self.ctrl)

        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(3)
        spl.setStyleSheet(f"QSplitter::handle {{ background:{C_GRID}; }}")

        self.dom   = OrderbookWidget()
        self.chart = TradingChart()
        self.chart.sigPauseRequested.connect(self._pause)

        spl.addWidget(self.dom); spl.addWidget(self.chart)
        spl.setSizes([430, 1170])
        spl.setCollapsible(0, False); spl.setCollapsible(1, False)
        lay.addWidget(spl, stretch=1)

        sb = QStatusBar()
        sb.setStyleSheet(
            f"QStatusBar {{ background:{C_PANEL}; color:{C_DIM};"
            f" font:9px 'Menlo'; border-top:1px solid {C_GRID}; }}"
        )
        self.setStatusBar(sb); self.sb = sb

    def _init_state(self):
        self.tick_df  = None
        self.ob_index = None
        self.tick_idx = 0
        self._timer   = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.tracker  = OrderbookTracker()
        for b in (self.ctrl.btn_play, self.ctrl.btn_pause, self.ctrl.btn_stop):
            b.setEnabled(False)

    def _start_loading(self):
        self.sb.showMessage("⏳  Loading data…  (may take a moment for large files)")
        self._loader = DataLoader(TICK_FILE, ORDERBOOK_FILE)
        self._loader.progress.connect(self._on_prog)
        self._loader.finished.connect(self._on_loaded)
        self._loader.start()

    @pg.QtCore.pyqtSlot(int, str)
    def _on_prog(self, pct, msg):
        self.sb.showMessage(f"{'❌' if pct<0 else '⏳'}  {abs(pct)}%  ·  {msg}")

    @pg.QtCore.pyqtSlot(object, object)
    def _on_loaded(self, tick_df, ob_index):
        self.tick_df = tick_df; self.ob_index = ob_index
        n = len(tick_df)
        self.sb.showMessage(
            f"✅  Loaded · {n:,} ticks · {len(ob_index):,} snapshots · Press ▶ PLAY"
        )
        self.ctrl.set_info(f"{n:,} ticks · {len(ob_index):,} snapshots")
        for b in (self.ctrl.btn_play, self.ctrl.btn_pause, self.ctrl.btn_stop):
            b.setEnabled(True)

    def _play(self):
        if not self.tick_df is not None: return
        if self.tick_df is None: return
        self.chart.resume_auto_range()
        self._timer.start(max(1, int(500 / self.ctrl.speed)))
        self.ctrl.btn_play.setText("▶  PLAYING")
        self.sb.showMessage("▶  Playing…  —  zoom/pan chart to auto-pause")

    def _pause(self):
        self._timer.stop()
        self.ctrl.btn_play.setText("▶  PLAY")
        self.sb.showMessage("⏸  Paused  ·  Press PLAY to continue")

    def _reset(self):
        self._timer.stop(); self.tick_idx = 0
        self.tracker.reset(); self.dom.reset(); self.chart.reset()
        self.ctrl.btn_play.setText("▶  PLAY")
        self.sb.showMessage("■  Reset  ·  Press PLAY to replay from start")

    def _speed(self, _):
        if self._timer.isActive():
            self._timer.setInterval(max(1, int(500 / self.ctrl.speed)))

    def _step(self):
        if self.tick_df is None or self.tick_idx >= len(self.tick_df):
            self._timer.stop()
            self.ctrl.btn_play.setText("▶  PLAY")
            self.sb.showMessage("⏹  Playback complete  ·  Press RESET to replay")
            return

        row  = self.tick_df.iloc[self.tick_idx]
        ts   = int(row['timestamp'])
        snap = self.ob_index.get(ts, pd.DataFrame())
        mid  = float(row.get('mid_price', 0) or 0)

        ob = self.tracker.update(snap, mid)
        self.dom.refresh(ob, float(row.get('best_bid', 0)),
                         float(row.get('best_ask', 0)), mid)
        
        # Sync chart Y-axis with orderbook visible price range for alignment
        highest_bid, lowest_ask = self.dom.get_visible_price_range()
        self.chart.sync_y_range_with_orderbook(highest_bid, lowest_ask)
        
        self.chart.on_tick(mid, self.tick_idx, ts)
        self.stats.refresh(row)

        pct = int(100 * self.tick_idx / len(self.tick_df))
        self.ctrl.set_info(
            f"Tick  {self.tick_idx:>6,} / {len(self.tick_df):,}  ({pct}%)"
        )
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
        QScrollBar:vertical {{ background:{C_PANEL}; width:6px; border:none; }}
        QScrollBar::handle:vertical {{ background:{C_GRID}; border-radius:3px; }}
        QToolTip {{
            background:{C_PANEL}; color:{C_TEXT};
            border:1px solid {C_GRID}; font:9px 'Menlo';
        }}
    """)
    win = BookmapTerminal()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()