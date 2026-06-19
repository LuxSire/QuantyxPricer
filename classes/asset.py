import json
from typing import Optional, Dict, Any
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
    extra_fields: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Asset':
        """Create an Asset instance from a dictionary.
        
        Args:
            data: Dictionary containing asset data
            
        Returns:
            Asset instance
        """
        # Extract known fields
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        
        asset_data = {}
        extra_fields = {}
        
        for key, value in data.items():
            if key in known_fields and key != 'extra_fields':
                asset_data[key] = value
            else:
                extra_fields[key] = value
        
        if extra_fields:
            asset_data['extra_fields'] = extra_fields
            
        return cls(**asset_data)
    
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
            if field_name != 'extra_fields':
                result[field_name] = value
        
        # Merge extra fields
        if self.extra_fields:
            result.update(self.extra_fields)
        
        return result
    
    def to_json(self, indent: Optional[int] = 2) -> str:
        """Convert Asset to JSON string.
        
        Args:
            indent: JSON indentation level
            
        Returns:
            JSON string representation
        """
        return json.dumps(self.to_dict(), indent=indent, default=str)
