import json
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, asdict, field


@dataclass
class Tenor:
    """Represents a single tenor point on a yield curve."""
    
    tenor: str
    rate: float
    source: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Tenor':
        """Create a Tenor instance from a dictionary.
        
        Args:
            data: Dictionary containing pillar data
            
        Returns:
            Tenor instance
        """
        return cls(
            tenor=data.get('tenor', ''),
            rate=float(data.get('rate', 0.0)),
            source=data.get('source')
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert Pillar to dictionary.
        
        Returns:
            Dictionary representation
        """
        return asdict(self)


@dataclass
class Curve:
    """Represents a single yield curve with pillars."""
    
    curve_name: str
    as_of: str
    day_count: str
    calendar: str
    compounding: str
    tenors: List[Tenor] = field(default_factory=list)
    ecb_name: Optional[str] = None
    fed_name: Optional[str] = None
    extra_fields: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Curve':
        """Create a Curve instance from a dictionary.
        
        Args:
            data: Dictionary containing curve data
            
        Returns:
            Curve instance
        """
        # Parse tenors
        tenors = []
        if 'pillars' in data and data['pillars']:
            tenors = [Tenor.from_dict(p) for p in data['pillars']]
        
        # Extract known fields
        curve = cls(
            curve_name=data.get('curve_name', ''),
            as_of=data.get('as_of', ''),
            day_count=data.get('day_count', ''),
            calendar=data.get('calendar', ''),
            compounding=data.get('compounding', ''),
            tenors=tenors,
            ecb_name=data.get('ecb_name'),
            fed_name=data.get('fed_name')
        )
        
        # Store any extra fields
        known_keys = {'curve_name', 'as_of', 'day_count', 'calendar', 'compounding', 'pillars', 'ecb_name', 'fed_name'}
        for key, value in data.items():
            if key not in known_keys:
                curve.extra_fields[key] = value
        
        return curve
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert Curve to dictionary.
        
        Returns:
            Dictionary representation
        """
        result = {
            'curve_name': self.curve_name,
            'as_of': self.as_of,
            'day_count': self.day_count,
            'calendar': self.calendar,
            'compounding': self.compounding,
            'pillars': [p.to_dict() for p in self.tenors]
        }
        
        # Add optional fields if present
        if self.ecb_name:
            result['ecb_name'] = self.ecb_name
        if self.fed_name:
            result['fed_name'] = self.fed_name
        
        # Merge extra fields
        result.update(self.extra_fields)
        
        return result
    
    def to_json(self, indent: Optional[int] = 2) -> str:
        """Convert Curve to JSON string.
        
        Args:
            indent: JSON indentation level
            
        Returns:
            JSON string representation
        """
        return json.dumps(self.to_dict(), indent=indent, default=str)
    
    def get_tenor(self, tenor_name: str) -> Optional[Tenor]:
        """Get a tenor by name.
        
        Args:
            tenor_name: The tenor name (e.g., '3M', '1Y')
            
        Returns:
            Tenor instance or None if not found
        """
        for tenor in self.tenors:
            if tenor.tenor == tenor_name:
                return tenor
        return None


class Curves:
    """Dictionary-like collection of Curve objects, keyed by curve_name."""
    
    def __init__(self, curves_dict: Optional[Dict[str, Curve]] = None):
        """Initialize Curves collection.
        
        Args:
            curves_dict: Optional dictionary of curve_name -> Curve mappings
        """
        self._curves: Dict[str, Curve] = curves_dict or {}
    
    @classmethod
    def from_json_file(cls, filepath: str) -> 'Curves':
        """Load Curves from a JSON file.
        
        Expected JSON format: list of objects with 'curve_name' and curve data
        
        Args:
            filepath: Path to JSON file (e.g., curves/swap_curves.json)
            
        Returns:
            Curves instance
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        curves_dict = {}
        if isinstance(data, list):
            for item in data:
                curve = Curve.from_dict(item)
                curves_dict[curve.curve_name] = curve
        elif isinstance(data, dict):
            # If already a dict, assume keys are curve_names
            for key, item in data.items():
                if isinstance(item, dict):
                    curve = Curve.from_dict(item)
                    curves_dict[curve.curve_name] = curve
                elif isinstance(item, Curve):
                    curves_dict[key] = item
        
        return cls(curves_dict)
    
    def __getitem__(self, curve_name: str) -> Curve:
        """Get a curve by curve_name.
        
        Args:
            curve_name: The curve name
            
        Returns:
            Curve instance
            
        Raises:
            KeyError: If curve_name not found
        """
        return self._curves[curve_name]
    
    def __setitem__(self, curve_name: str, curve: Curve) -> None:
        """Set a curve by curve_name.
        
        Args:
            curve_name: The curve name
            curve: Curve instance
        """
        self._curves[curve_name] = curve
    
    def __contains__(self, curve_name: str) -> bool:
        """Check if curve_name exists in collection.
        
        Args:
            curve_name: The curve name
            
        Returns:
            True if curve_name exists, False otherwise
        """
        return curve_name in self._curves
    
    def __len__(self) -> int:
        """Get number of curves in collection.
        
        Returns:
            Number of curves
        """
        return len(self._curves)
    
    def __iter__(self):
        """Iterate over curve_names."""
        return iter(self._curves)
    
    def get(self, curve_name: str, default: Optional[Curve] = None) -> Optional[Curve]:
        """Get a curve by curve_name with optional default.
        
        Args:
            curve_name: The curve name
            default: Default value if not found
            
        Returns:
            Curve instance or default value
        """
        return self._curves.get(curve_name, default)
    
    def keys(self):
        """Get all curve_names."""
        return self._curves.keys()
    
    def values(self):
        """Get all Curve objects."""
        return self._curves.values()
    
    def items(self):
        """Get all (curve_name, Curve) tuples."""
        return self._curves.items()
    
    def to_list(self) -> List[Dict[str, Any]]:
        """Convert to list of dictionaries.
        
        Returns:
            List of curve dictionaries
        """
        return [curve.to_dict() for curve in self._curves.values()]
    
    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        """Convert to dictionary of dictionaries.
        
        Returns:
            Dictionary with curve_name as keys
        """
        return {cn: curve.to_dict() for cn, curve in self._curves.items()}
    
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
