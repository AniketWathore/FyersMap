# Bookmap Terminal — Architecture & Usage Guide

## Quick Start
```bash
pip install -r requirements.txt
python bookmap_terminal.py
```

---

## Architecture Overview

```
BookmapTerminal (QMainWindow)
│
├── StatsBar              ← Top strip: BBO, spread, mid, qty
├── ControlBar            ← Play / Pause / Reset / Speed selector
│
├── HeatmapView  (GraphicsLayoutWidget)
│   ├── HeatmapMatrix     ← Circular buffer (N_PRICE × WINDOW_TICKS)
│   ├── ImageItem         ← Renders heatmap with log-normalised colormap
│   ├── ScatterPlotItem   ← Buy bubbles (green)
│   ├── ScatterPlotItem   ← Sell bubbles (red)
│   ├── InfiniteLine ×3   ← BID / ASK / MID price lines
│   └── TradeDetector     ← Infers trades from consecutive tick diffs
│
├── VolumeProfileBar      ← Vertical cumulative qty bars (right of heatmap)
│
└── DOMWidget             ← Live bid/ask ladder with ASCII bar indicators

DataLoader (QThread)
    Streams orderbook CSV in chunks → Dict[timestamp, DataFrame]
    Emits finished(tick_df, ob_index) when done
```

---

## Class Responsibilities

### `DataLoader(QThread)`
- Reads tick CSV once (small file, direct load)
- Reads orderbook CSV in configurable chunks (`OB_CHUNK_SIZE = 10,000` rows)
- Groups orderbook rows by timestamp into `ob_index: Dict[int, DataFrame]`
- Uses compact dtypes (`float32`, `int32`, `category`) to minimize RAM

### `HeatmapMatrix`
- Maintains a `(N_PRICE × WINDOW_TICKS)` float32 numpy array as a circular buffer
- Each `push()` call places one tick's orderbook snapshot into the next column
- `np.roll()` returns the display matrix with oldest data on the left
- Price window re-centres on the current mid-price every tick
- Uses `np.add.at()` for vectorised, collision-safe accumulation

### `TradeDetector`
- Compares consecutive tick rows' `total_bid_qty` and `total_ask_qty`
- Drop in `total_ask_qty` → aggressive buy at `best_ask` (green bubble)
- Drop in `total_bid_qty` → aggressive sell at `best_bid` (red bubble)
- Bubble size ∝ `log(qty)` for perceptual linearity

### `HeatmapView`
- Heatmap: `pg.ImageItem` with custom black→navy→cyan→yellow→red colormap
- Log-normalised intensity for high dynamic range visibility
- Bubbles stored in a `deque(maxlen=MAX_BUBBLES)` with absolute tick birth time
  → No per-tick deque reconstruction, O(1) aging via `current_tick - born_tick`
- Separate `ScatterPlotItem` for buy/sell (faster `setData` on smaller lists)

---

## Configuration (`bookmap_terminal.py` top section)

| Constant         | Default | Description                              |
|------------------|---------|------------------------------------------|
| `WINDOW_TICKS`   | 300     | Ticks visible horizontally               |
| `N_PRICE`        | 160     | Price rows in heatmap                    |
| `PRICE_STEP`     | 0.1     | NSE minimum price increment (₹)          |
| `OB_CHUNK_SIZE`  | 10,000  | Orderbook rows per CSV read chunk        |
| `BUBBLE_MIN_PX`  | 5       | Minimum bubble diameter (pixels)         |
| `BUBBLE_MAX_PX`  | 42      | Maximum bubble diameter (pixels)         |
| `MAX_BUBBLES`    | 900     | Bubble deque capacity                    |

---

## Memory Efficiency Notes

- **Compact dtypes**: `float32` for prices, `int32` for quantities, `category` for
  string columns — roughly 2× savings vs default `float64`/`object`
- **Circular buffer**: The heatmap matrix never grows — fixed `(160 × 300)` array
- **Bubble deque**: `maxlen=900` bounds memory; old bubbles auto-evict
- **Chunk loading**: Orderbook is never loaded row-by-row; 10k-row chunks keep
  peak RAM low during startup
- **No per-frame pandas**: The hot path (timer callback) does pure numpy operations
  on pre-indexed data

For extremely large files (>500MB orderbook):
- Increase `OB_CHUNK_SIZE` to 50,000 for faster loading
- Or reduce `OB_CHUNK_SIZE` to 2,000 for lower peak RAM during load

---

## Extending the Terminal

### Add Cumulative Volume Delta (CVD)
Track running `buy_qty - sell_qty` in `TradeDetector` and render as a
`pg.PlotItem` below the heatmap using `pg.GraphicsLayoutWidget.addPlot(row=1)`.

### Real-time streaming
Replace `DataLoader`/`QTimer` replay with a WebSocket/socket feed that
calls `heatmap.on_tick()` and `dom.refresh()` directly.

### Bid/Ask split heatmap
Store bid qty and ask qty in separate matrices; blend them at render time
with separate colormaps (blue for bid depth, red for ask depth).

### Alerts
Add `pg.InfiniteLine` annotations or `pg.TextItem` labels when bubble
size exceeds a threshold (large trade alert).
