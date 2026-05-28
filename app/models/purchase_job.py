from datetime import datetime
from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, Enum, ForeignKey,
    Numeric, JSON, Index,
)
from app.extensions import Base


class PurchaseJob(Base):
    """A mailer buying records from Gravitas Leads (no upload step)."""
    __tablename__ = 'purchase_jobs'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(BigInteger, ForeignKey('mailer_companies.id'), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey('mailer_users.id'), nullable=False, index=True)

    status = Column(Enum(
        'created', 'priced', 'awaiting_payment', 'paid', 'generating',
        'complete', 'failed',
        name='purchase_job_status'),
        default='created', nullable=False, index=True)

    selected_verticals_json = Column(JSON, nullable=True)  # [{vertical_id, subcategory_ids, records}]
    volume = Column(Integer, default=0)                    # total records requested
    subtotal_cents = Column(BigInteger, default=0)
    discount_cents = Column(BigInteger, default=0)
    promo_code = Column(String(40), nullable=True)
    price_cents = Column(BigInteger, default=0)
    stripe_payment_intent_id = Column(String(120), nullable=True)
    paid_at = Column(DateTime, nullable=True)

    result_s3_key = Column(String(500), nullable=True)
    result_file_path = Column(String(500), nullable=True)   # legacy
    result_filename = Column(String(160), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    failure_reason = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_pj_company_status', 'company_id', 'status'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'user_id': self.user_id,
            'status': self.status,
            'selected_verticals': self.selected_verticals_json or [],
            'volume': int(self.volume or 0),
            'subtotal_cents': int(self.subtotal_cents or 0),
            'discount_cents': int(self.discount_cents or 0),
            'promo_code': self.promo_code,
            'price_cents': int(self.price_cents or 0),
            'price_dollars': round(int(self.price_cents or 0) / 100, 2),
            'result_filename': self.result_filename,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
