from sqlalchemy import Column, Integer, String, Boolean, Numeric, BigInteger
from app.extensions import Base


class Vertical(Base):
    """A top-level industry the platform sells data for (Auto Insurance, etc.)."""
    __tablename__ = 'verticals'

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(40), nullable=False, unique=True)
    display_name = Column(String(80), nullable=False)
    icon = Column(String(8), nullable=True)           # emoji shorthand
    description = Column(String(240), nullable=True)
    base_rate = Column(Numeric(10, 4), default=0.018) # $/record before tier discount
    available_records = Column(BigInteger, default=0) # admin-maintained inventory snapshot
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'slug': self.slug,
            'display_name': self.display_name,
            'icon': self.icon,
            'description': self.description,
            'base_rate': float(self.base_rate) if self.base_rate is not None else 0.0,
            'available_records': int(self.available_records or 0),
            'is_active': bool(self.is_active),
            'sort_order': int(self.sort_order or 0),
        }
