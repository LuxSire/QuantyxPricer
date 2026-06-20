import json
from typing import Optional, Dict, Any, List, Union
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Asset:
    """Represents a financial asset with all its characteristics."""
    
    instrument_id: str
    currency: str
    model: str
    description: str = ""
    evaluation_date: Optional[str] = None
    issue_date: Optional[str] = None
    maturity_date: Optional[str] = None
    calendar: str = "TARGET"
    accrual_day_count: str = "Actual360"
    business_day_convention: str = "ModifiedFollowing"
    date_generation: str = "Forward"
    coupon_structure: str = ""
    fixed_coupon_rate: Optional[float] = None
    coupon_frequency: Optional[str] = None
    par: float = 100.0
    credit_spread_bp: Optional[float] = None
    callable_type: Optional[str] = None
    call_dates: list = field(default_factory=list)
    valuation_mode: Optional[str] = None
    redemption: Optional[float] = None
    call_price: Optional[float] = None
    target_price: Optional[float] = None
    underlying: Optional['Asset'] = None
    extra_fields: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Asset':
        """Create an Asset instance from a dictionary.
        
        Supports both native format and cbonds API format.
        
        Args:
            data: Dictionary containing asset data
            
        Returns:
            Asset instance
        """
        # Normalize cbonds format to standard format
        normalized_data = cls._normalize_cbonds_format(data)
        
        # Extract known fields
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        
        asset_data = {}
        extra_fields = {}
        
        for key, value in normalized_data.items():
            if key == 'underlying' and isinstance(value, dict):
                asset_data['underlying'] = cls.from_dict(value)
            elif key in known_fields and key not in {'extra_fields', 'underlying'}:
                asset_data[key] = value
            asset_data['extra_fields'] = extra_fields
            
        return cls(**asset_data)
    
    @classmethod
    def _normalize_cbonds_format(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize cbonds API response format to internal format.
        
        Args:
            data: cbonds API response or native format dictionary
            
        Returns:
            Normalized dictionary
        """
        # If this looks like cbonds format (has isin_code, emitent_id, etc.)
        if 'isin_code' in data or 'emitent_id' in data:
            normalized = data.copy()
            
            # Map cbonds fields to standard fields
            if 'isin_code' in data and 'instrument_id' not in data:
                normalized['instrument_id'] = data['isin_code']
            
            if 'currency_name' in data and 'currency' not in data:
                normalized['currency'] = data['currency_name']
            
            if 'emitent_name_eng' in data and 'issuer' not in data:
                normalized['issuer'] = data['emitent_name_eng']
            
            if 'document_eng' in data and 'description' not in data:
                normalized['description'] = data['document_eng']
            
            if 'maturity_date' in data and isinstance(data['maturity_date'], str):
                # maturity_date from cbonds should already be ISO format
                normalized['maturity_date'] = data['maturity_date']
            
            if 'offert_date' in data and 'issue_date' not in data:
                normalized['issue_date'] = data['offert_date']
            
            if 'coupon_type_id' in data:
                # cbonds coupon info
                if 'coupon_type_name_eng' in data and 'coupon_structure' not in data:
                    normalized['coupon_structure'] = data['coupon_type_name_eng']
            
            if 'curr_coupon_rate' in data:
                try:
                    normalized['fixed_coupon_rate'] = float(data['curr_coupon_rate'])
                except (ValueError, TypeError):
                    pass
            
            if 'nominal_price' in data:
                try:
                    normalized['par'] = float(data['nominal_price']) / 100.0 if float(data['nominal_price']) > 100 else float(data['nominal_price'])
                except (ValueError, TypeError):
                    pass
            
            return normalized
        
        # Return data as-is if not cbonds format
        return data
    
    @classmethod
    def from_json_string(cls, json_str: str) -> 'Asset':
        """Create an Asset instance from a JSON string.
        
        Args:
            json_str: JSON string containing asset data
            
        Returns:
            Asset instance
        """
        data = json.loads(json_str)
        return cls.from_dict(data)
    
    @classmethod
    def from_json_file(cls, filepath: str) -> 'Asset':
        """Create an Asset instance from a JSON file.
        
        Args:
            filepath: Path to JSON file containing asset data
            
        Returns:
            Asset instance
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert Asset to dictionary.
        
        Returns:
            Dictionary representation of the asset
        """
        result = {}
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if field_name == 'extra_fields':
                continue
            if field_name == 'underlying' and isinstance(value, Asset):
                result[field_name] = value.to_dict()
            else:
                result[field_name] = value
        
        # Merge extra fields
        if self.extra_fields:
            result.update(self.extra_fields)
        
        return result
    
    def __post_init__(self):
        if self.instrument_id:
            self.__class__.assets[self.instrument_id] = self

    def to_json(self, indent: Optional[int] = 2) -> str:
        """Convert Asset to JSON string.
        
        Args:
            indent: JSON indentation level
            
        Returns:
            JSON string representation
        """
        return json.dumps(self.to_dict(), indent=indent, default=str)


class Assets:
    """Dictionary-like collection of Asset objects, keyed by instrument_id."""

    def __init__(self, assets_dict: Optional[Dict[str, Asset]] = None):
        self._assets: Dict[str, Asset] = assets_dict or {}

    @classmethod
    def from_json_file(cls, filepath: str) -> 'Assets':
        """Load assets from a JSON file containing a list or dict of asset records."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_data(data)

    @classmethod
    def from_list(cls, data: List[Dict[str, Any]]) -> 'Assets':
        assets_dict: Dict[str, Asset] = {}
        for item in data:
            if isinstance(item, dict):
                asset = Asset.from_dict(item)
                if asset.instrument_id:
                    assets_dict[asset.instrument_id] = asset
        return cls(assets_dict)

    @classmethod
    def from_data(cls, data: Union[List[Dict[str, Any]], Dict[str, Any]]) -> 'Assets':
        if isinstance(data, list):
            return cls.from_list(data)

        assets_dict: Dict[str, Asset] = {}
        for key, item in data.items():
            if isinstance(item, dict):
                asset = Asset.from_dict(item)
                assets_dict[asset.instrument_id or key] = asset
            elif isinstance(item, Asset):
                assets_dict[key] = item
        return cls(assets_dict)

    def __getitem__(self, instrument_id: str) -> Asset:
        return self._assets[instrument_id]

    def __setitem__(self, instrument_id: str, asset: Asset) -> None:
        self._assets[instrument_id] = asset

    def __contains__(self, instrument_id: str) -> bool:
        return instrument_id in self._assets

    def __len__(self) -> int:
        return len(self._assets)

    def __iter__(self):
        return iter(self._assets)

    def get(self, instrument_id: str, default: Optional[Asset] = None) -> Optional[Asset]:
        return self._assets.get(instrument_id, default)

    def keys(self):
        return self._assets.keys()

    def values(self):
        return self._assets.values()

    def items(self):
        return self._assets.items()

    def to_list(self) -> List[Dict[str, Any]]:
        return [asset.to_dict() for asset in self._assets.values()]

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        return {iid: asset.to_dict() for iid, asset in self._assets.items()}

    def to_json(self, indent: Optional[int] = 2, as_list: bool = True) -> str:
        data = self.to_list() if as_list else self.to_dict()
        return json.dumps(data, indent=indent, default=str)
