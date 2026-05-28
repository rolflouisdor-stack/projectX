from datetime import datetime
from sqlalchemy import Column, BigInteger, String, Boolean, DateTime, Text
from app.extensions import Base


class PromoBanner(Base):
    """Admin-managed marketing banner that surfaces on the mailer dashboard."""
    __tablename__ = 'promo_banners'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tag = Column(String(40), nullable=True)          # e.g. "Spring Special"
    title = Column(String(160), nullable=False)
    body = Column(Text, nullable=True)
    cta_label = Column(String(60), nullable=True)
    cta_url = Column(String(255), nullable=True)
    icon = Column(String(8), nullable=True)
    is_active = Column(Boolean, default=True)
    starts_at = Column(DateTime, nullable=True)
    ends_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'tag': self.tag,
            'title': self.title,
            'body': self.body,
            'cta_label': self.cta_label,
            'cta_url': self.cta_url,
            'icon': self.icon,
            'is_active': bool(self.is_active),
            'starts_at': self.starts_at.isoformat() if self.starts_at else None,
            'ends_at': self.ends_at.isoformat() if self.ends_at else None,
        }
