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
            if key in known_fields and key != 'extra_fields':
                asset_data[key] = value
            else:
                extra_fields[key] = value
        
        if extra_fields:
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
