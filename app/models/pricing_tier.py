from sqlalchemy import Column, Integer, Numeric, Boolean
from app.extensions import Base


class PricingTier(Base):
    """Volume-discount table. min_volume INCLUSIVE → discount_pct applied
    to the line subtotal (per-vertical base_rate × volume)."""
    __tablename__ = 'pricing_tiers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    min_volume = Column(Integer, nullable=False)
    discount_pct = Column(Numeric(5, 2), default=0)  # 0..100
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    def to_dict(self):
        return {
            'id': self.id,
            'min_volume': int(self.min_volume or 0),
            'discount_pct': float(self.discount_pct) if self.discount_pct else 0.0,
            'is_active': bool(self.is_active),
            'sort_order': int(self.sort_order or 0),
        }
