#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║     BOOKMAP TERMINAL  —  NSE Indian Market  L2 Depth        ║
╠══════════════════════════════════════════════════════════════╣
║  LEFT  : Persistent DOM — ALL accumulated price levels       ║
║          Fixed row positions (never shuffle), moving mid row ║
║          Rolling window keeps mid centred; manually scroll.  ║
║                                                              ║
║  RIGHT : TradingView-style mid-price chart                   ║
║          · Latest tick pinned near right edge (right margin) ║
║          · Y-axis synced with orderbook visible range        ║
║          · Dotted mid line + price badge on right axis       ║
║          · Crosshairs, tooltip, zoom/pan                     ║
╚══════════════════════════════════════════════════════════════╝

Install:
    pip install PyQt5 pyqtgraph numpy pandas

Run:
    python3 bookmap_terminal.py
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
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtCore import Qt, QThread, QTimer, QSize, QRect, QUrl, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPalette, QPainter, QPen

import pyqtgraph as pg
import os

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
    "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/NSE_TCS-EQ_tick_20260402_095152_ticks.csv"
)
ORDERBOOK_FILE = (
    "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/NSE_TCS-EQ_orderbook_20260402_095152.csv"
)

# ─────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────
ROW_H             = 19       # DOM row height (px)
OB_CHUNK_SIZE     = 10_000   # OB CSV read chunk
DOM_UPDATE_EVERY  = 2        # throttle DOM redraws (every N ticks)
CHART_MAX_POINTS  = 6_000    # max price points kept in RAM
CHART_VISIBLE     = 400      # visible ticks in chart at once
CHART_RIGHT_PAD   = 0.06     # fraction of window kept empty on right

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
                               f"Ready! {len(tick_df):,} ticks · "
                               f"{len(sorted_prices):,} unique prices")
            self.finished.emit(tick_df, ob_index, sorted_prices)
        except Exception as e:
            log.exception("DataLoader failed")
            self.progress.emit(-1, f"ERROR: {e}")


# ═════════════════════════════════════════════════════════════
#  PRICE LEVEL DATA  (persistent — never removed)
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
        if qty > self.cumulative_qty:
            self.cumulative_qty = qty
        if orders > self.cumulative_orders:
            self.cumulative_orders = orders

    def clear_current(self):
        self.current_qty    = 0
        self.current_orders = 0


# ═════════════════════════════════════════════════════════════
#  ORDERBOOK TRACKER
# ═════════════════════════════════════════════════════════════
class OrderbookTracker:
    """
    Every unique price ever seen gets a PriceLevelData entry.
    Rows are fixed; only the mid-price row index changes.
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
                if lv is None:
                    continue
                bq, aq = int(bid_qtys[i]), int(ask_qtys[i])
                bo, ao = int(bid_ords[i]),  int(ask_ords[i])
                if bq > 0:
                    lv.update(bq, bo)
                elif aq > 0:
                    lv.update(aq, ao)

        mid_idx = bisect.bisect_right(self.sorted_prices, mid) - 1
        mid_idx = max(0, min(mid_idx, len(self.sorted_prices) - 1))
        return mid_idx

    def reset(self):
        for lv in self.levels.values():
            lv.clear_current()
            lv.cumulative_qty    = 0
            lv.cumulative_orders = 0


# ═════════════════════════════════════════════════════════════
#  DELEGATES
# ═════════════════════════════════════════════════════════════
class PriceDelegate(QStyledItemDelegate):
    """Column 0 — price text, coloured by zone."""
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
            p.drawText(opt.rect, Qt.AlignCenter, text)
        p.restore()

    def sizeHint(self, opt, idx):
        return QSize(0, ROW_H)


class BarDelegate(QStyledItemDelegate):
    """Columns 1 & 2 — quantity / orders with intensity bar."""
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

    def sizeHint(self, opt, idx):
        return QSize(0, ROW_H)


# ═════════════════════════════════════════════════════════════
#  ORDERBOOK WIDGET
# ═════════════════════════════════════════════════════════════
class OrderbookWidget(QFrame):
    """
    KEY DESIGN:
    ─ Every unique price ever seen gets a FIXED row — rows never move,
      no two prices share a row, no price ever changes its row.
    ─ The mid-price marker (spans all 3 cols) slides up/down through
      the fixed ladder as mid price changes.
    ─ Auto-scroll keeps mid row centred (rolling window).
      Any manual scroll touch pauses auto-scroll.
    ─ Scrolling always works — even during live playback.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background:{C_BG}; }}")
        self.setMinimumWidth(400)
        self.setMaximumWidth(700)

        self._sorted_prices: List[float] = []
        self._n              = 0
        self._mid_idx        = -1
        self._max_qty        = 1.0
        self._max_orders     = 1.0
        self._tick_ctr       = 0
        self._auto_scroll    = True
        self._scroll_locked  = False   # prevents feedback loop
        self._build()

    # ── build ────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._hdr(
            [("ASKs", 1), ("All Ask Orders", 2), ("All Ask Quantity", 2)], C_ASK
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
                background:{C_PANEL}; width:6px; border:none;
            }}
            QScrollBar::handle:vertical {{
                background:{C_GRID}; border-radius:3px; min-height:20px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height:0px; }}
        """)

        self.tbl.setColumnWidth(0, 80)
        for c in (1, 2):
            self.tbl.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.Stretch)

        self.tbl.setItemDelegateForColumn(0, PriceDelegate(self.tbl))
        self.tbl.setItemDelegateForColumn(1, BarDelegate(self.tbl))
        self.tbl.setItemDelegateForColumn(2, BarDelegate(self.tbl))

        # Detect manual scroll → pause auto-center
        self.tbl.verticalScrollBar().valueChanged.connect(self._on_user_scroll)

        root.addWidget(self.tbl, stretch=1)

        root.addWidget(self._hdr(
            [("BIDs", 1), ("All Bid Quantity", 2), ("All Bid Orders", 2)], C_BID
        ))

        self.stat = QLabel("")
        self.stat.setStyleSheet(
            f"color:{C_DIM}; font:7px 'Menlo'; background:{C_GRID}; padding:3px 6px;"
        )
        root.addWidget(self.stat)

    # ── initialize (once, after data loaded) ─────────────────
    def initialize(self, sorted_prices: List[float]):
        # Display highest price at the top so asks appear above mid and bids below.
        self._sorted_prices = list(reversed(sorted_prices))
        self._n = len(sorted_prices)
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

    # ── helpers ──────────────────────────────────────────────
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
                f"color:{color}; font:bold 7px 'Menlo';"
                f" letter-spacing:1px; padding:0;"
            )
            lay.addWidget(lb, stretch)
        return f

    def _on_user_scroll(self, _value):
        """Any scroll event NOT triggered by our own code → pause auto-centre."""
        if not self._scroll_locked:
            self._auto_scroll = False

    def _cell(self, r, c, rt, iv, txt):
        it = self.tbl.item(r, c)
        if it is not None:
            it.setData(Qt.UserRole,     rt)
            it.setData(Qt.UserRole + 1, float(iv))
            it.setData(Qt.DisplayRole,  txt)

    # ── main refresh (called every tick) ─────────────────────
    def refresh(self, tracker: 'OrderbookTracker', mid: float, mid_idx: int):
        if self._n == 0:
            return
        mid_idx = self._n - 1 - mid_idx

        self._tick_ctr += 1
        if self._tick_ctr > 1 and self._tick_ctr % DOM_UPDATE_EVERY != 0:
            return

        # ── 1. Move the mid-row span ──────────────────────────
        old_mid = self._mid_idx
        if old_mid != mid_idx:
            if 0 <= old_mid < self._n:
                # un-span old mid row so its cells are individually addressable
                self.tbl.setSpan(old_mid, 0, 1, 1)
            # span new mid row across all columns
            self.tbl.setSpan(mid_idx, 0, 1, 3)
        self._mid_idx = mid_idx

        # mid-row label
        mi = self.tbl.item(mid_idx, 0)
        if mi:
            mi.setData(Qt.UserRole,     'mid')
            mi.setData(Qt.UserRole + 1, 0.0)
            mi.setData(Qt.DisplayRole,  f'── MID  ₹{mid:.2f} ──')

        # ── 2. Intensity scaling ──────────────────────────────
        cur_mq = max(
            (lv.current_qty    for lv in tracker.levels.values()), default=0)
        cur_mo = max(
            (lv.current_orders for lv in tracker.levels.values()), default=0)
        self._max_qty    = max(self._max_qty    * 0.96, cur_mq, 1)
        self._max_orders = max(self._max_orders * 0.96, cur_mo, 1)
        mq, mo = self._max_qty, self._max_orders

        # ── 3. Update every non-mid row ───────────────────────
        prices = self._sorted_prices
        for i in range(self._n):
            if i == mid_idx:
                continue
            lv  = tracker.levels[prices[i]]
            rt  = 'ask' if i < mid_idx else 'bid'
            qty  = lv.current_qty
            ords = lv.current_orders
            self._cell(i, 0, rt, 0.0,  f'{prices[i]:.2f}')
            self._cell(i, 1, rt,
                       min(1.0, qty  / mq) if qty  else 0.0,
                       f'{qty:,}'  if qty  else '')
            self._cell(i, 2, rt,
                       min(1.0, ords / mo) if ords else 0.0,
                       f'{ords:,}' if ords else '')

        self.tbl.viewport().update()

        # ── 4. Auto-scroll to keep mid row centred ────────────
        if self._auto_scroll:
            QTimer.singleShot(0, self._scroll_to_mid)

        # ── 5. Footer stats ───────────────────────────────────
        tb, ta = 0, 0
        for i, price in enumerate(prices):
            q = tracker.levels[price].current_qty
            if   i < mid_idx: ta += q
            elif i > mid_idx: tb += q
        self.stat.setText(
            f"  BID Qty: {tb:>10,}  │  ASK Qty: {ta:>10,}  │  "
            f"LEVELS: {self._n:,}"
        )

    def _scroll_to_mid(self):
        """Centre the mid row in the viewport without triggering _on_user_scroll."""
        if self._mid_idx < 0 or self._n == 0:
            return
        self._scroll_locked = True
        vp_h   = self.tbl.viewport().height()
        target = self._mid_idx * ROW_H - vp_h // 2 + ROW_H // 2
        target = max(0, target)
        self.tbl.verticalScrollBar().setValue(target)
        self._scroll_locked = False

    # ── public API ────────────────────────────────────────────
    def get_visible_price_range(self) -> Tuple[float, float]:
        """
        Return (lowest_visible_price, highest_visible_price) so the chart
        Y-axis can be synced with exactly what the orderbook is showing.
        """
        if self._n == 0:
            return 0.0, 0.0

        vp_h = self.tbl.viewport().height()
        sb   = self.tbl.verticalScrollBar()

        if self._auto_scroll and self._mid_idx >= 0:
            # Calculate what _scroll_to_mid will set (deterministic)
            scroll_top = max(0, self._mid_idx * ROW_H - vp_h // 2 + ROW_H // 2)
        else:
            scroll_top = sb.value()

        top_row = max(0,          scroll_top // ROW_H)
        bot_row = min(self._n - 1, (scroll_top + vp_h - 1) // ROW_H)

        top_price = self._sorted_prices[top_row]
        bot_price = self._sorted_prices[bot_row]
        return min(top_price, bot_price), max(top_price, bot_price)

    def enable_auto_scroll(self):
        """Re-enable centring."""
        self._auto_scroll = True

    def reset(self):
        old = self._mid_idx
        self._tick_ctr    = 0
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

    def set_origin(self, s: int):        self._origin = s
    def register(self, x: int, s: int): self._x2s[x] = s
    def clear_all(self):
        self._x2s.clear(); self._origin = None

    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            s = self._x2s.get(int(round(v)))
            if s and self._origin:
                e = max(0, s - self._origin)
                out.append(
                    f"{e//3600:02d}:{(e%3600)//60:02d}:{e%60:02d}")
            else:
                out.append('')
        return out


# ═════════════════════════════════════════════════════════════
#  TRADING CHART  — TradingView-style using KLineChart
# ═════════════════════════════════════════════════════════════
class TradingChart(QWidget):
    """
    QWebEngineView wrapping the local KLineChart JS implementation.
    Receives ticks dynamically without requiring any external internet APIs.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QWidget {{ background:{C_BG}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        
        self.view = QWebEngineView(self)
        
        # Load local HTML
        html_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'web', 'chart.html'))
        self.view.load(QUrl.fromLocalFile(html_path))
        
        lay.addWidget(self.view)

    # ── Y-range sync ─────────────────────────────────────────
    def sync_y_range(self, low_price: float, high_price: float):
        """
        KLineChart auto-scales. We let its native zoom & pan do its job now.
        Just doing nothing here since we removed the forced manual sync.
        """
        pass

    # ── per-tick update ───────────────────────────────────────
    def on_tick(self, mid: float, tick_num: int, timestamp: int):
        js = f"updateTick({timestamp}, {mid});"
        self.view.page().runJavaScript(js)

    # ── public API ────────────────────────────────────────────
    def resume_auto_range(self):
        pass

    def reset(self):
        self.view.page().runJavaScript("resetChart();")


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
            t.setStyleSheet(
                f"QLabel {{ color:{C_DIM}; font:7px 'Menlo';"
                f" border:none; letter-spacing:1px; }}"
            )
            v = QLabel("—")
            v.setStyleSheet(
                f"QLabel {{ color:{color}; font:bold 12px 'Menlo'; border:none; }}"
            )
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
            try:
                self._vals[key].setText(fmt(row[key]))
            except Exception:
                pass
        try:
            dt = str(row.get('datetime', ''))
            self.ts.setText(dt[:19] if dt else '—')
        except Exception:
            pass


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

        spd = QLabel("SPEED")
        spd.setStyleSheet(
            f"color:{C_DIM}; font:7px 'Menlo'; letter-spacing:1px;")

        self.speed_cb = QComboBox()
        self.speed_cb.addItems(list(self.SPEED_MAP.keys()))
        self.speed_cb.setCurrentIndex(2)
        self.speed_cb.setFixedWidth(75)
        self.speed_cb.setStyleSheet(f"""
            QComboBox {{ background:{C_BG}; color:{C_TEXT};
                border:1px solid {C_GRID};
                padding:2px 6px; font:9px 'Menlo'; }}
            QComboBox::drop-down {{ border:none; width:16px; }}
            QComboBox QAbstractItemView {{
                background:{C_PANEL}; color:{C_TEXT};
                selection-background-color:{C_GRID}; }}
        """)

        for w in (spd, self.speed_cb):
            lay.addWidget(w)
        lay.addStretch()
        self.info = QLabel("Load data to begin")
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
        self.setWindowTitle(
            "📊  Bookmap Terminal  ·  NSE TCS-EQ  ·  L2 Depth")
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
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

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
            f" font:9px 'Menlo'; border-top:1px solid {C_GRID}; }}"
        )
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
        self.sb.showMessage(
            "⏳  Loading data…  (may take a moment for large files)")
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

        n = len(tick_df)
        p = len(sorted_prices)
        self.sb.showMessage(
            f"✅  {n:,} ticks · {p:,} unique prices · Auto-playing")
        self.ctrl.set_info(f"{n:,} ticks · {p:,} price levels")

        self.tracker = OrderbookTracker(sorted_prices)
        self.dom.initialize(sorted_prices)
        self._timer.start(max(1, int(500 / self.ctrl.speed)))
        self.sb.showMessage("▶  Playing…")

    def _speed_changed(self, _):
        if self._timer.isActive():
            self._timer.setInterval(max(1, int(500 / self.ctrl.speed)))

    def _step(self):
        if self.tick_df is None:
            return
        if self.tick_idx >= len(self.tick_df):
            self.tick_idx = 0
            if self.tracker:
                self.tracker.reset()
            self.dom.reset()
            self.chart.reset()

        row  = self.tick_df.iloc[self.tick_idx]
        ts   = int(row['timestamp'])
        snap = self.ob_index.get(ts, pd.DataFrame())
        mid  = float(row.get('mid_price', 0) or 0)

        # 1. Update persistent orderbook → returns mid-price row index
        mid_idx = self.tracker.update(snap, mid)

        # 2. Refresh DOM (fixes mid span, fills cells, auto-scrolls)
        self.dom.refresh(self.tracker, mid, mid_idx)

        # 3. Sync chart Y-range with the DOM's visible price window
        lo, hi = self.dom.get_visible_price_range()
        if lo < hi:
            self.chart.sync_y_range(lo, hi)

        # 4. Advance price chart (keeps latest at right edge)
        self.chart.on_tick(mid, self.tick_idx, ts)

        # 5. Top stats bar
        self.stats.refresh(row)

        pct = int(100 * self.tick_idx / len(self.tick_df))
        self.ctrl.set_info(
            f"Tick  {self.tick_idx:>6,} / {len(self.tick_df):,}  ({pct}%)"
        )
        self.tick_idx += 1

    def closeEvent(self, ev):
        self._timer.stop()
        if hasattr(self, '_loader') and self._loader.isRunning():
            self._loader.quit()
            self._loader.wait(3000)
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
            background:{C_PANEL}; width:6px; border:none;
        }}
        QScrollBar::handle:vertical {{
            background:{C_GRID}; border-radius:3px;
        }}
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
