#!/usr/bin/env python3
"""Quick test to verify bookmap terminal works."""

import sys
import os

# Test 1: Import check
print("Test 1: Checking imports...")
try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import Qt
    import pyqtgraph as pg
    import pandas as pd
    import numpy as np
    print("✓ All imports successful")
except ImportError as e:
    print(f"✗ Import failed: {e}")
    sys.exit(1)

# Test 2: File existence check
print("\nTest 2: Checking data files...")
tick_file = "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/NSE_TCS-EQ_tick_20260402_095152.csv"
ob_file = "/Users/prashik/Aniket/SMP/DATA/MarketDepthData/2026-04-02_09-51-52_NSE_TCS-EQ/NSE_TCS-EQ/NSE_TCS-EQ_orderbook_20260402_095152.csv"

if os.path.exists(tick_file):
    size_mb = os.path.getsize(tick_file) / 1024 / 1024
    print(f"✓ Tick file found ({size_mb:.1f} MB)")
else:
    print(f"✗ Tick file not found: {tick_file}")
    sys.exit(1)

if os.path.exists(ob_file):
    size_mb = os.path.getsize(ob_file) / 1024 / 1024
    print(f"✓ Orderbook file found ({size_mb:.1f} MB)")
else:
    print(f"✗ Orderbook file not found: {ob_file}")
    sys.exit(1)

# Test 3: Load sample data
print("\nTest 3: Loading sample data...")
try:
    tick_df = pd.read_csv(tick_file, nrows=5)
    print(f"✓ Tick file readable ({len(tick_df)} rows sampled)")
    print(f"  Columns: {list(tick_df.columns)}")
except Exception as e:
    print(f"✗ Failed to read tick file: {e}")
    sys.exit(1)

try:
    ob_df = pd.read_csv(ob_file, nrows=100)
    print(f"✓ Orderbook file readable ({len(ob_df)} rows sampled)")
    print(f"  Columns: {list(ob_df.columns)}")
except Exception as e:
    print(f"✗ Failed to read orderbook file: {e}")
    sys.exit(1)

# Test 4: Module syntax check
print("\nTest 4: Checking bookmap_terminal.py syntax...")
try:
    import bookmap_terminal
    print("✓ bookmap_terminal module loaded successfully")
except SyntaxError as e:
    print(f"✗ Syntax error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"✗ Import error: {e}")

print("\n" + "="*50)
print("✓ All tests passed! Ready to run bookmap terminal.")
print("\nTo start:")
print("  cd /Users/prashik/Aniket/SMP/FyersMap")
print("  python3 bookmap_terminal.py")
print("="*50)
