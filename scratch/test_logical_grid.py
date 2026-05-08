from src.grid.grid_manager import GridManager
from src.indicators.bollinger import BBResult

def test_grid_logic():
    # Setup test data based on the user's reported values
    # BB: $79,388 -> $80,318 (Mid: 79853)
    # ATR: $331
    bb = BBResult(mid=79853.0, upper=80318.0, lower=79388.0)
    atr_val = 331.0
    
    manager = GridManager(levels=8, capital_usdt=200, min_reserve=50, spacing_multiplier=0.8)
    grid = manager.calculate_grid(bb, atr_val)
    
    print(f"Mid Price: {grid.mid_price}")
    print(f"ATR Spacing: {grid.spacing}")
    print("\nBuy Levels (Descending):")
    for l in grid.buy_levels:
        print(f"  Level {l['level']}: ${l['price']}")
        
    print("\nSell Levels (Ascending):")
    for l in grid.sell_levels:
        print(f"  Level {l['level']}: ${l['price']}")

    # Logical Assertions
    highest_buy = grid.buy_levels[0]['price']
    lowest_sell = grid.sell_levels[0]['price']
    
    print(f"\nHighest Buy: ${highest_buy}")
    print(f"Lowest Sell: ${lowest_sell}")
    print(f"Gap: ${round(lowest_sell - highest_buy, 2)}")
    
    assert highest_buy < grid.mid_price, "Highest buy must be below mid price"
    assert lowest_sell > grid.mid_price, "Lowest sell must be above mid price"
    assert highest_buy < lowest_sell, "Buys and sells must not overlap"
    assert lowest_sell - highest_buy >= grid.spacing, "Gap must be at least one spacing unit"
    
    print("\n✓ LOGICAL GRID TEST PASSED")

if __name__ == "__main__":
    test_grid_logic()
