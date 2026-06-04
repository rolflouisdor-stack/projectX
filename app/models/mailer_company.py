from datetime import datetime
from sqlalchemy import Column, BigInteger, String, DateTime, Enum, Index
from app.extensions import Base


class MailerCompany(Base):
    """A company that runs mail campaigns. Owns one or more MailerUsers."""
    __tablename__ = 'mailer_companies'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_name = Column(String(120), nullable=False, unique=True)
    status = Column(Enum('pending', 'active', 'suspended', name='mailer_company_status'),
                    default='active')
    stripe_customer_id = Column(String(100), nullable=True)   # Stripe Customer for saved cards / charges
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index('idx_mc_name', 'company_name'),)

    def to_dict(self):
        return {
            'id': self.id,
            'company_name': self.company_name,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
