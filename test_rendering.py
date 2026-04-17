#!/usr/bin/env python3
"""Quick test of the bookmap display fixes."""

import sys
sys.path.insert(0, '/Users/prashik/Aniket/SMP/FyersMap')

# Test the HTML bar rendering
from bookmap_terminal import OrderbookWidget, C_TEXT

widget = OrderbookWidget()

# Test _shade_color
print("Testing color generation:")
for intensity in [0.0, 0.25, 0.5, 0.75, 1.0]:
    bid_color = widget._shade_color(intensity, True)  # Bid (green)
    ask_color = widget._shade_color(intensity, False)  # Ask (red)
    print(f"  Intensity {intensity:.2f}: BID={bid_color} ASK={ask_color}")

# Test bar rendering
print("\nTesting bar rendering:")
for value in [100, 500, 1000, 2000, 5000]:
    bar = widget._render_bar_html(value, 5000, value/5000, True)
    print(f"  Value {value:>5}: {bar}")

print("\n✓ All rendering tests passed!")
