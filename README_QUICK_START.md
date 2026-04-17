# Bookmap Terminal - Quick Start Guide

## What Changed
Your bookmap terminal has been completely redesigned from a **volume bubble view** to a professional **dual-panel layout**:

### Before (Old)
- Scatter plot of volume bubbles (hard to track details)
- Simple 10-level orderbook
- No price history visualization

### After (Now)
- **LEFT**: Scrollable orderbook table (101 rows total)
  - 50 bid levels (green bars showing cumulative volume)
  - Mid separator line
  - 50 ask levels (red bars showing cumulative volume)
- **RIGHT**: Real-time price chart
  - Shows mid price history (last 300 ticks)
  - Time axis with HH:MM:SS:TICK format
  - Interactive crosshairs on hover
  - Tooltip showing exact price and time

## How to Run

### Option 1: Direct Execution
```bash
cd /Users/prashik/Aniket/SMP/FyersMap
python3 bookmap_terminal.py
```

### Option 2: With Python Virtual Environment (if needed)
```bash
python3 -m venv venv
source venv/bin/activate
pip install PyQt5 pyqtgraph pandas numpy
python3 bookmap_terminal.py
```

## Using the Terminal

### 1. Load Data
Terminal loads automatically from:
- `/Users/prashik/Aniket/SMP/DATA/MarketDepthData/2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/`
  - `NSE_TCS-EQ_tick_20260402_095152.csv` (tick data)
  - `NSE_TCS-EQ_orderbook_20260402_095152.csv` (orderbook snapshots)

### 2. Control Playback
| Button | Function |
|--------|----------|
| ▶ PLAY | Start playback from beginning |
| ⏸ PAUSE | Pause current playback |
| ⏹ STOP | Stop and reset to tick 0 |
| Speed ✓ | Select playback speed (0.5x / 1.0x / 1.5x / 2.0x) |

### 3. Read the Display

**Left Panel (Orderbook)**
```
  Price    Qty (Bar)          Orders (Bar)
────────────────────────────────────────────
  2406.90  12,034(████████...)  234(█████...)   ← Ask level
  2406.80   8,234(█████░░░░...)  156(███░░...)
  ...
  ││ MID: ₹2406.75 ││                         ← Mid separator
  ...
  2406.70   5,123(████░░░░...)   98(████░░...)   ← Bid level
  2406.65   3,456(███░░░░░...)   67(███░░░...)
```

- **Price**: Current price level
- **Qty Bar**: Visual intensity shows cumulative order quantity (darker = more volume)
- **Order Bar**: Visual intensity shows cumulative number of orders
- **Colors**: 
  - Green shades = Bid levels (buyers waiting)
  - Red shades = Ask levels (sellers waiting)

**Right Panel (Chart)**
- Y-axis (left): Price in Rupees (₹)
- X-axis (bottom): Time in HH:MM:SS:TICK format
- White line: Mid price movement over time
- Grid: Help align price/time readings
- Hover: Shows crosshairs + tooltip with exact values

## Key Features

### 1. Historical Volume Tracking
- Tracks ALL price levels ever seen (even if they disappear)
- Cumulative qty = sum of all **increases** (ignores decreases from fills)
- Example:
  - Tick 1: Price 100 has qty 50 → cumulative = 50
  - Tick 2: Price 100 has qty 20 → cumulative = 50 (no increase)
  - Tick 3: Price 100 has qty 65 → cumulative = 65 (+15 added)

### 2. Color Intensity
- Bars use RGB shading:
  - Darkest = ~30% brightness (minimum visible)
  - Brightest = 100% brightness (maximum volume)
- Automatically normalizes to max qty in current view
- Updates in real-time as data streams

### 3. Responsive Layout
- Splitter handle between panels (drag to resize)
- Orderbook auto-scrolls to show mid price
- Chart shows last 300 ticks
- Smooth 60 FPS on Mac Mini M4

### 4. Time Formatting
- Shows elapsed time from start of playback
- Format: `HH:MM:SS:TICK`
  - Hours (00-23)
  - Minutes (00-59)
  - Seconds (00-59)
  - Tick counter (relative to start)
- Example: "00:02:15:047" = 2 minutes, 15 seconds into playback, tick 47

## Troubleshooting

### Problem: "No data showing"
1. Check terminal window is focused
2. Click PLAY button
3. Check tick file size (should be ~0.7 MB)
4. Run test: `python3 test_bookmap.py`

### Problem: "Chart on wrong side"
- Confirm terminal is v926 lines
- Current: Orderbook LEFT, Chart RIGHT

### Problem: "Colors not visible"
- Make sure display is not set to brightness extremes
- Check resolution is 1920x1080 or higher
- Bars should show gradient from dark to bright

### Problem: "Playback very slow"
- Reduce Speed setting from 2.0x to 0.5x
- Close other applications (chart rendering intensive)
- Mac Mini M4 can handle full speed with throttled DOM updates

### Problem: "Memory usage high"
- Terminal uses fixed 300-tick buffers (prevents unbounded growth)
- Close other applications for Mac Mini M4
- Don't open multiple instances

## File Locations

| File | Location | Size |
|------|----------|------|
| Terminal Code | `/Users/prashik/Aniket/SMP/FyersMap/bookmap_terminal.py` | 39 KB |
| Tick Data | `/Users/prashik/Aniket/SMP/DATA/.../NSE_TCS-EQ_tick_20260402_095152.csv` | 0.7 MB |
| Orderbook Data | `/Users/prashik/Aniket/SMP/DATA/.../NSE_TCS-EQ_orderbook_20260402_095152.csv` | 49.3 MB |
| Test Script | `/Users/prashik/Aniket/SMP/FyersMap/test_bookmap.py` | 2.3 KB |
| Documentation | `/Users/prashik/Aniket/SMP/FyersMap/IMPLEMENTATION_COMPLETE.md` | 8.2 KB |

## System Requirements

- **OS**: macOS (tested on Mac Mini M4)
- **Python**: 3.8+ with pip
- **RAM**: 4 GB (recommend 8+ for smooth playback)
- **Dependencies**:
  - PyQt5 (GUI framework)
  - pyqtgraph (chart visualization)
  - pandas (data processing)
  - numpy (numerical computing)

## Advanced: Modify Configuration

Edit these constants in `bookmap_terminal.py` (lines 60-65):
```python
WINDOW_TICKS      = 300      # Ticks shown on chart (larger = more memory)
PRICE_LEVELS      = 50       # Max bid/ask levels (max 100 each)
CHART_DISPLAY     = 21       # Visible orderbook rows (max 101)
DOM_UPDATE_EVERY  = 3        # Update freq (3 = every 3rd tick, throttles)
```

## Support

For issues or modifications:
1. Check logs in terminal output
2. Run `python3 test_bookmap.py` to verify setup
3. Review `IMPLEMENTATION_COMPLETE.md` for technical details
4. Check file paths are correct (macOS uses `/` not `\`)

---

**Ready to use!** Run: `python3 /Users/prashik/Aniket/SMP/FyersMap/bookmap_terminal.py`
