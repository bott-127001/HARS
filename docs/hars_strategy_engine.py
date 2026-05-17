import pandas as pd
import numpy as np

class HARSStrategyEngine:
    """
    Hurst-Adaptive Regime Strategy (HARS) Engine - v5.0 (Dual Engine)
    Optimized for Nifty 50 (5-minute data)
    1. MEAN REVERSION: High ATR / No Memory
    2. VOLATILITY SHOCK: High RVOL / Low Momentum (Accumulation Filter)
    """
    
    def __init__(self, h_threshold=0.55):
        self.h_threshold = h_threshold

    @staticmethod
    def calculate_hurst(ts):
        ts = np.array(ts)
        if len(ts) < 100: return np.nan
        n_list = [20, 50, 100, 200, 400]
        n_list = [n for n in n_list if n < len(ts)//2]
        rs_values = []
        for n in n_list:
            num_blocks = len(ts) // n
            rs_block = []
            for i in range(num_blocks):
                block = ts[i*n : (i+1)*n]
                mean = np.mean(block)
                z = np.cumsum(block - mean)
                r = np.max(z) - np.min(z)
                s = np.std(block)
                if s > 0: rs_block.append(r / s)
            if rs_block: rs_values.append(np.mean(rs_block))
        if len(rs_values) < 2: return np.nan
        poly = np.polyfit(np.log(n_list[:len(rs_values)]), np.log(rs_values), 1)
        return poly[0]

    def classify_regime(self, idx_rets, vix_rets):
        h_idx = self.calculate_hurst(idx_rets)
        h_vix = self.calculate_hurst(vix_rets)
        
        if np.isnan(h_idx) or np.isnan(h_vix):
            return "UNKNOWN", h_idx, h_vix
            
        if h_idx <= self.h_threshold and h_vix <= self.h_threshold:
            return "MEAN_REVERTING", h_idx, h_vix
        elif h_idx <= self.h_threshold and h_vix > self.h_threshold:
            return "VOLATILITY_SHOCK", h_idx, h_vix
        else:
            return "NO_TRADE", h_idx, h_vix

    def get_signals(self, regime, stock_data_pool):
        """
        stock_data_pool needs: 'prices', 'highs', 'lows', 'volumes'
        """
        prices = stock_data_pool['prices']
        volumes = stock_data_pool['volumes']
        highs = stock_data_pool['highs']
        lows = stock_data_pool['lows']
        
        if regime == "MEAN_REVERTING":
            day_range = (highs.max() - lows.min())
            atr_pct = day_range / prices.iloc[-1]
            selected_sym = atr_pct.idxmax()
            return {
                "symbol": selected_sym,
                "target": 1.5,
                "stop": 1.0,
                "description": "High-ATR Mean Reversion"
            }
            
        elif regime == "VOLATILITY_SHOCK":
            # Accumulation Filter: High RVOL + Low Momentum
            rvol = volumes.iloc[-1] / volumes.iloc[-20:].mean()
            # 15-min Momentum (last 3 candles of 5-min data)
            mom = (prices.iloc[-1] / prices.iloc[-3]) - 1
            
            # Candidates: RVOL > 2.0 and Momentum < 0.3%
            candidates = rvol[(rvol > 2.0) & (mom < 0.003)]
            
            if not candidates.empty:
                selected_sym = candidates.idxmax()
                return {
                    "symbol": selected_sym,
                    "target": 2.0, # Shocks have higher target potential
                    "stop": 1.0,
                    "description": "Accumulation Volatility Shock"
                }
            
        return None

if __name__ == "__main__":
    print("HARS Strategy Engine v5.0 Loaded.")
