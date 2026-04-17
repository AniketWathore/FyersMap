# Bookmap Terminal Redesign - Complete Overhaul

## Overview
The bookmap terminal has been completely redesigned from a volume bubble visualization to a professional dual-panel layout featuring:
- **Left Panel**: Interactive orderbook table with 101 price levels (50 bids + mid + 50 asks)
- **Right Panel**: TradingView-style price chart showing mid price history

---

## Architecture Changes

### Volume Bubbles → Price Level Tracking
**Old System**: Displayed bubbles only for top 10 bid/ask levels
**New System**: Maintains full price level history for all levels ever encountered

#### New Data Structure: `PriceLevelData`
```python
@dataclass
class PriceLevelData:
    price: float
    side: str = 'bid'
    total_qty_added: int = 0      # Cumulative qty increments
    total_orders_added: int = 0   # Cumulative order increments
    current_qty: int = 0          # Live qty at this tick
    current_orders: int = 0       # Live orders at this tick
```

#### Quantity Logic (Critical)
The tracker implements the exact delta-tracking logic you specified:

- **Tick 1**: Price 2406.6 has qty=19 → total_qty_added = 19
- **Tick 2**: Price 2406.6 has qty=20 → delta = (20-19) = 1 → total_qty_added = 20
- **Tick 3**: Price 2406.6 has qty=18 → delta = 0 (negative delta ignored) → total_qty_added = 20

This ensures cumulative volume tracking without double-counting.

---

## Left Panel: Orderbook Widget

### Layout (101 Rows)
- **Rows 0-49**: Ask orders (highest price first) - RED color gradient
- **Row 50**: Mid price separator - White highlight
- **Rows 51-100**: Bid orders (highest price first) - GREEN color gradient

### Display Format
```
PRICE      ↓ QTY ↑      ↓ ORDERS ↑
─────────────────────────────────
 2407.10   15,234(████████░░░░░░░░░░)   1,052(████████░░░░░░░░░░)  
 2407.05   8,456(█████░░░░░░░░░░░░░░░░░█)    456(████░░░░░░░░░░░░░░░)
 2407.00   23,100(███████████░░░░░░░░░░)   2,341(█████████░░░░░░░░░░)
││ MID: ₹2406.75 ││  (separator with grid background)
 2406.70   12,000(███████░░░░░░░░░░░░░░░)    987(█████░░░░░░░░░░░░░░)
 2406.65   5,600(████░░░░░░░░░░░░░░░░░░░░░░)  234(██░░░░░░░░░░░░░░░░░)
 ...
```

### Color Intensity Mapping
- **Green Shades (Bids)**:
  - Light green (30% brightness) = Low volume
  - Dark green (100% brightness) = High volume
  
- **Red Shades (Asks)**:
  - Light red (30% brightness) = Low volume
  - Dark red (100% brightness) = High volume

The intensity is calculated as: `brightness = 0.3 + (qty / max_qty) * 0.7`

### Dynamic Scrolling
- Automatically centers on mid price on first update
- Shows 21 visible rows (out of 101)
- User can scroll to view deep levels

### Status Bar
Shows real-time aggregated quantities:
```
BID Qty:        567,234  │  ASK Qty:        498,123
```

---

## Right Panel: Trading Chart

### Features
1. **Mid Price History Line**
   - Continuous plot of mid price over time
   - Updates every tick
   - Line color: White (#f0f0f0)
   - Width: 2px

2. **Interactive Crosshairs**
   - Vertical line follows X (time) axis
   - Horizontal line follows Y (price) axis
   - Dotted style for clarity

3. **Tooltip on Hover**
   - Shows: Price (₹), Time (HH:MM:SS:TICK)
   - Follows cursor with 2px offset
   - Auto-hides when not near data

4. **Grid Background**
   - Dark grid for easy price/time reference
   - 10% opacity for subtle appearance
   - Price axis labeled on right
   - Time axis labeled on bottom

5. **Zoom & Pan**
   - Scroll to zoom in/out
   - Drag to pan horizontally/vertically
   - Maintains smooth updates during interaction

### Time Format
- **Format**: `HH:MM:SS:TICK`
- **Example**: `14:35:42:1234` (14:35:42 UTC, tick #1234)
- Calculated from first timestamp's relative time

### Chart Dynamics
- Rolling window: Shows last 300 ticks
- Auto-scales Y-axis with 10% padding for price range
- Auto-adjusts X-axis to show recent ticks with 2-tick padding

---

## Data Flow Pipeline

### Tick Processing (Per Frame)
1. **Load Data**: Read tick row + corresponding orderbook snapshot
2. **Update Tracker**: 
   - `OrderbookTracker.update(snap, mid)` processes all price levels
   - Returns dict with `{'bids': [...], 'asks': [...], 'mid': float}`
3. **Refresh UI**:
   - OrderbookWidget displays color-coded bars
   - TradingChart adds new mid price point
   - StatsBar updates metrics
   - Alert banner triggers on high activity

### Historical Data Preservation
The `OrderbookTracker` maintains:
- `all_levels`: Dict of ALL price levels ever seen
- `active_levels`: Current 50 bid + 50 ask levels with qty > 0

When a price level disappears from the snapshot, its historical data is preserved and will resume tracking if that price reappears.

---

## Performance Optimizations (Mac Mini M4 Compatible)

### Memory Management
1. **Fixed-Size Buffers**:
   - `deque(maxlen=WINDOW_TICKS)` for price history (300 max)
   - Automatic old data removal prevents unbounded growth

2. **Lazy Updates**:
   - DOM updates every N ticks (`DOM_UPDATE_EVERY = 3`)
   - Prevents excessive GUI redraws
   - Maintains responsiveness without lag

3. **Efficient Data Structures**:
   - Sets for O(1) price lookups
   - Dict-based level storage avoids O(n) searches
   - NumPy arrays for bulk operations only when needed

### Rendering Optimization
1. **PyQtGraph Configuration**:
   - `antialias=False` - Faster rendering
   - `useOpenGL=False` - More stable on all systems
   
2. **Batch Updates**:
   - Single GUI update per tick
   - No per-row updates in orderbook

3. **Garbage Collection**:
   - `gc.collect()` after data loading
   - Prevents memory accumulation

---

## Big-Move Alerts

### Alert Trigger Logic
```python
total_activity = len(snap_dataframe)
if total_activity > 20 and tick_idx > 10:
    # Show alert
```

### Alert Format
```
⚠ HIGH MARKET ACTIVITY · 47 active price levels ⚠
```

### Display Behavior
- Orange color (#ff8800) with red border
- Auto-hides after 2 seconds
- Can be manually dismissed

---

## Configuration Constants

```python
WINDOW_TICKS      = 300         # Chart shows last 300 ticks
PRICE_STEP        = 0.1         # Round prices to 0.1
OB_CHUNK_SIZE     = 10_000      # Data loading batch size
PRICE_LEVELS      = 50          # Max bid/ask levels shown
CHART_DISPLAY     = 21          # Visible orderbook rows
DOM_UPDATE_EVERY  = 3           # Update DOM every N ticks
```

---

## UI Controls

### Top Control Bar
- **▶ PLAY**: Start playback at current speed
- **⏸ PAUSE**: Pause without resetting
- **■ RESET**: Stop and return to tick #0
- **SPEED**: 0.25×, 0.5×, 1×, 2×, 5×, 10×, 25×, or MAX

### Status Bar
- Left: Progress indicator (tick number, percentage)
- Right: Timestamp of current tick

### Alert Banner
- Appears on high market activity
- Shows active price level count
- Auto-dismisses after 2 seconds

---

## File Paths (Configurable)

```python
TICK_FILE = "/Users/prashik/Aniket/SMP/DATA/...NSE_TCS-EQ_tick_20260402_095152.csv"
ORDERBOOK_FILE = "/Users/prashik/Aniket/SMP/DATA/...NSE_TCS-EQ_orderbook_20260402_095152.csv"
```

---

## Color Palette

| Element | Color | Usage |
|---------|-------|-------|
| Background | #07070e | Main window bg |
| Panel | #0c0c1c | Widget backgrounds |
| Grid | #1a1a30 | Grid lines, dividers |
| Text | #b0b0cc | Primary text |
| Text Dim | #606080 | Secondary text |
| Mid Price | #f0f0f0 | Chart line, separator |
| Alert | #ff8800 | Alert banner |
| Bid Base | rgb(0, 200, 100) | Green for bids |
| Ask Base | rgb(220, 40, 60) | Red for asks |

---

## Known Limitations & Future Enhancements

### Current Limitations
1. Big-move alerts based on price level count (not historical volume)
2. Time format assumes microsecond timestamps
3. No persistent configuration save/load

### Future Enhancements
1. Alert based on specific tracked levels reaching activity thresholds
2. Volume profile histogram on right side of chart
3. Order flow delta (buy vs sell volume)
4. Session statistics and VWAP
5. Custom alert rules and price level filters

---

## Running the Terminal

```bash
cd /Users/prashik/Aniket/SMP/FyersMap
python3 bookmap_terminal.py
```

The terminal will:
1. Load data (progress bar shows %)
2. Enable controls when ready
3. Display empty orderbook + blank chart
4. Click ▶ PLAY to begin
5. Adjust speed as needed
6. Use scroll bars to explore deep levels

---

## Summary of Changes

| Aspect | Before | After |
|--------|--------|-------|
| **Layout** | Volume bubbles only | Dual-panel (orderbook + chart) |
| **Price Levels** | Top 10 bids/asks | 50 bids + 50 asks (101 total) |
| **Volume Tracking** | Peak only | Cumulative delta tracking |
| **Visualization** | Bubble size | Color intensity bars |
| **Chart**| N/A | Full TradingView-style history |
| **Scrolling** | 10 DOM levels | 21 visible out of 101 |
| **Responsiveness** | Updates every tick | Updates every 3 ticks |

Enjoy your enhanced bookmap terminal! 🚀
