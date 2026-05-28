from datetime import datetime
from sqlalchemy import Column, BigInteger, Integer, String, Boolean, DateTime, Numeric, Index
from app.extensions import Base


class PromoCode(Base):
    __tablename__ = 'promo_codes'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    code = Column(String(40), nullable=False, unique=True)
    discount_pct = Column(Numeric(5, 2), default=0)   # 0..100
    discount_cents = Column(BigInteger, default=0)    # flat dollar discount as cents
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    max_redemptions = Column(Integer, nullable=True)
    redeemed_count = Column(Integer, default=0)
    applies_to_vertical_id = Column(Integer, nullable=True)  # None = all verticals
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index('idx_promo_code', 'code'),)

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'discount_pct': float(self.discount_pct) if self.discount_pct else 0.0,
            'discount_cents': int(self.discount_cents or 0),
            'is_active': bool(self.is_active),
            'starts_at': self.starts_at.isoformat() if self.starts_at else None,
            'ends_at': self.ends_at.isoformat() if self.ends_at else None,
            'redeemed_count': int(self.redeemed_count or 0),
            'max_redemptions': self.max_redemptions,
            'applies_to_vertical_id': self.applies_to_vertical_id,
        }
