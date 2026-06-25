import json
from typing import Dict, Optional, Any, List, Union
from dataclasses import dataclass, asdict, field
import numpy as np

@dataclass
class OHLCV:
    """Single OHLCV bar in a time series."""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'OHLCV':
        """Create OHLCV from a dict with open/high/low/close/volume keys (case-insensitive)."""
        def _get(*keys: str) -> float:
            for k in keys:
                v = d.get(k)
                if v is not None:
                    return float(v)
            return 0.0
        return cls(
            open=_get('open', 'o', 'O'),
            high=_get('high', 'h', 'H'),
            low=_get('low', 'l', 'L'),
            close=_get('close', 'c', 'C', 'adjusted_close'),
            volume=_get('volume', 'v', 'V'),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TimeSeries:
    """Dictionary-like collection of OHLCV bars keyed by date string.

    Example:
        ts = TimeSeries()
        ts['2025-06-23'] = OHLCV(open=201.63, high=202.3, low=198.96, close=201.5, volume=55814300)
        print(ts['2025-06-23'].close)   # 201.5
        print(len(ts))                   # 1
    """

    def __init__(self, bars: Optional[Dict[str, OHLCV]] = None):
        self._bars: Dict[str, OHLCV] = bars or {}


    def volatility(self) -> float:
        """
        Calculate the annualized volatility of daily returns.

        Returns are computed as (close_t / close_{t-1}) - 1 for each pair of
        consecutive trading days, then the standard deviation is annualised
        by multiplying by sqrt(252).

        Returns:
            float: The annualized volatility of daily returns.
        """
        # Sort close prices chronologically by date
        sorted_bars = sorted(self._bars.items(), key=lambda kv: kv[0])
        if len(sorted_bars) < 2:
            return 0.0
        close_prices = np.array([bar.close for _, bar in sorted_bars])
        # Daily returns: (close_t / close_{t-1}) - 1
        returns = close_prices[1:] / close_prices[:-1] - 1.0
        std_dev = np.std(returns, ddof=1)
        return float(std_dev * np.sqrt(252))

    @classmethod    
    def from_data(cls, data: List[Dict[str, Any]]) -> 'TimeSeries':
        """Build a TimeSeries from a list of EODHD-style OHLCV payloads.

        Each dict must contain 'date', 'open', 'high', 'low', 'close', 'volume'.
        """
        ts = cls()
        for row in data:
            date = row.get('date')
            if date:
                ts._bars[date] = OHLCV.from_dict(row)
        return ts

    def __getitem__(self, date: str) -> OHLCV:
        return self._bars[date]

    def __setitem__(self, date: str, bar: OHLCV) -> None:
        self._bars[date] = bar

    def __contains__(self, date: str) -> bool:
        return date in self._bars

    def __len__(self) -> int:
        return len(self._bars)

    def __iter__(self):
        return iter(self._bars)

    def get(self, date: str, default: Optional[OHLCV] = None) -> Optional[OHLCV]:
        return self._bars.get(date, default)

    def keys(self):
        return self._bars.keys()

    def values(self):
        return self._bars.values()

    def items(self):
        return self._bars.items()

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Convert to {date: {open, high, low, close, volume}}."""
        return {date: bar.to_dict() for date, bar in self._bars.items()}

    def to_list(self) -> List[Dict[str, Any]]:
        """Convert to list of OHLCV dicts with date field."""
        return [{'date': date, **bar.to_dict()} for date, bar in self._bars.items()]


class TS_Dict:
    """Dictionary-like collection of TimeSeries objects, keyed by instrument_id.

    Example:
        ts_dict = TS_Dict()
        ts_dict['AAPL'] = TimeSeries()
        ts_dict['AAPL']['2025-06-23'] = OHLCV(open=201.63, high=202.3, low=198.96, close=201.5, volume=55814300)
    """

    def __init__(self, ts_dict: Optional[Dict[str, TimeSeries]] = None):
        self._ts: Dict[str, TimeSeries] = ts_dict or {}

    @classmethod
    def from_data(cls, data: Union[List[Dict[str, Any]], Dict[str, Any]]) -> 'TS_Dict':
        """Build a TS_Dict from either a single EODHD-style payload or a list of them.

        Single payload:  { 'instrument_id': ..., 'provider': ..., 'data': [...] }
        List payload:    [ { 'instrument_id': ..., 'provider': ..., 'data': [...] }, ... ]
        """
        ts_dict = TS_Dict()

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    instrument_id = item.get('instrument_id', '')
                    rows = item.get('data', [])
                    if instrument_id and isinstance(rows, list):
                        ts_dict._ts[instrument_id] = TimeSeries.from_data(rows)
        elif isinstance(data, dict):
            instrument_id = data.get('instrument_id', '')
            rows = data.get('data', [])
            if instrument_id and isinstance(rows, list):
                ts_dict._ts[instrument_id] = TimeSeries.from_data(rows)

        return ts_dict

    def __getitem__(self, instrument_id: str) -> TimeSeries:
        return self._ts[instrument_id]

    def __setitem__(self, instrument_id: str, ts: TimeSeries) -> None:
        self._ts[instrument_id] = ts

    def __contains__(self, instrument_id: str) -> bool:
        return instrument_id in self._ts

    def __len__(self) -> int:
        return len(self._ts)

    def __iter__(self):
        return iter(self._ts)

    def get(self, instrument_id: str, default: Optional[TimeSeries] = None) -> Optional[TimeSeries]:
        return self._ts.get(instrument_id, default)

    def keys(self):
        return self._ts.keys()

    def values(self):
        return self._ts.values()

    def items(self):
        return self._ts.items()

    def to_dict(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Convert to {instrument_id: {date: {open, high, low, close, volume}}}."""
        return {iid: ts.to_dict() for iid, ts in self._ts.items()}

    def to_list(self) -> List[Dict[str, Any]]:
        """Convert to list of instrument-level payloads (EODHD-style)."""
        return [
            {
                'instrument_id': iid,
                'data': ts.to_list(),
            }
            for iid, ts in self._ts.items()
        ]


@dataclass
class Price:
    """Represents a single price record for an instrument."""
    
    instrument_id: str
    bond_file: Optional[str] = None
    model: Optional[str] = None
    currency: Optional[str] = None
    pdf: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Price':
        """Create a Price instance from a dictionary.
        
        Args:
            data: Dictionary containing price data
            
        Returns:
            Price instance
        """
        return cls(
            instrument_id=data.get('instrument_id', ''),
            bond_file=data.get('bond_file'),
            model=data.get('model'),
            currency=data.get('currency'),
            pdf=data.get('pdf'),
            result=data.get('result')
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert Price to dictionary.
        
        Returns:
            Dictionary representation of the price
        """
        return asdict(self)


class Prices:
    """Dictionary-like collection of Price objects, keyed by instrument_id."""
    
    def __init__(self, prices_dict: Optional[Dict[str, Price]] = None):
        """Initialize Prices collection.
        
        Args:
            prices_dict: Optional dictionary of instrument_id -> Price mappings
        """
        self._prices: Dict[str, Price] = prices_dict or {}
    
    @classmethod
    def from_json_file(cls, filepath: str) -> 'Prices':
        """Load Prices from a JSON file.
        
        Expected JSON format: list of objects with 'instrument_id' and price data
        
        Args:
            filepath: Path to JSON file (e.g., output/prices.json)
            
        Returns:
            Prices instance
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_data(data)

    @classmethod
    def from_list(cls, data: List[Dict[str, Any]]) -> 'Prices':
        """Create Prices from a list of dictionaries."""
        prices_dict: Dict[str, Price] = {}
        for item in data:
            if isinstance(item, dict):
                price = Price.from_dict(item)
                prices_dict[price.instrument_id] = price
        return cls(prices_dict)

    @classmethod
    def from_data(cls, data: Union[List[Dict[str, Any]], Dict[str, Any]]) -> 'Prices':
        """Create Prices from either a list, dict, or TimeSeries payload.

        Detects EODHD-style payloads (with 'data' key containing a list of
        OHLCV points) and stores them as TimeSeries objects.
        """
        if isinstance(data, list):
            return cls.from_list(data)

        prices_dict: Dict[str, Price] = {}
        if isinstance(data, dict):

            for key, item in data.items():
                if isinstance(item, dict):
                    price = Price.from_dict(item)
                    prices_dict[price.instrument_id or key] = price
                elif isinstance(item, Price):
                    prices_dict[key] = item
        return cls(prices_dict)
    
    def __getitem__(self, instrument_id: str) -> Price:
        """Get a price by instrument_id.
        
        Args:
            instrument_id: The instrument ID
            
        Returns:
            Price instance
            
        Raises:
            KeyError: If instrument_id not found
        """
        return self._prices[instrument_id]
    
    def __setitem__(self, instrument_id: str, price: Price) -> None:
        """Set a price by instrument_id.
        
        Args:
            instrument_id: The instrument ID
            price: Price instance
        """
        self._prices[instrument_id] = price
    
    def __contains__(self, instrument_id: str) -> bool:
        """Check if instrument_id exists in collection.
        
        Args:
            instrument_id: The instrument ID
            
        Returns:
            True if instrument_id exists, False otherwise
        """
        return instrument_id in self._prices
    
    def __len__(self) -> int:
        """Get number of prices in collection.
        
        Returns:
            Number of price records
        """
        return len(self._prices)
    
    def __iter__(self):
        """Iterate over instrument_ids."""
        return iter(self._prices)
    
    def get(self, instrument_id: str, default: Optional[Price] = None) -> Optional[Price]:
        """Get a price by instrument_id with optional default.
        
        Args:
            instrument_id: The instrument ID
            default: Default value if not found
            
        Returns:
            Price instance or default value
        """
        return self._prices.get(instrument_id, default)
    
    def keys(self):
        """Get all instrument_ids."""
        return self._prices.keys()
    
    def values(self):
        """Get all Price objects."""
        return self._prices.values()
    
    def items(self):
        """Get all (instrument_id, Price) tuples."""
        return self._prices.items()
    
    def to_list(self) -> List[Dict[str, Any]]:
        """Convert to list of dictionaries.
        
        Returns:
            List of price dictionaries
        """
        return [price.to_dict() for price in self._prices.values()]
    
    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Convert to dictionary of dictionaries.
        
        Returns:
            Dictionary with instrument_id as keys
        """
        return {iid: price.to_dict() for iid, price in self._prices.items()}
    
    def to_json(self, indent: Optional[int] = 2, as_list: bool = True) -> str:
        """Convert to JSON string.
        
        Args:
            indent: JSON indentation level
            as_list: If True, export as list; if False, as dict
            
        Returns:
            JSON string representation
        """
        data = self.to_list() if as_list else self.to_dict()
        return json.dumps(data, indent=indent, default=str)
