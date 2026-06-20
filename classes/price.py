import json
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict


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
        """Create Prices from either a list or dict payload."""
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
