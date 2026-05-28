from datetime import datetime
from sqlalchemy import Column, BigInteger, String, Integer, DateTime, ForeignKey, UniqueConstraint
from app.extensions import Base


class PaymentMethod(Base):
    """A saved card-on-file for a MailerCompany.

    Stores only the parts of a card it is safe to retain (brand, last4,
    expiry) plus the payment-method id Stripe returns. Never raw PAN/CVC.
    Real Stripe wiring should swap the stub on a SetupIntent + Elements flow.
    """
    __tablename__ = 'mailer_payment_methods'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(BigInteger, ForeignKey('mailer_companies.id'),
                        nullable=False, index=True)
    stripe_payment_method_id = Column(String(100), nullable=True)
    brand = Column(String(20), nullable=False)
    last4 = Column(String(4), nullable=False)
    exp_month = Column(Integer, nullable=False)
    exp_year = Column(Integer, nullable=False)
    cardholder_name = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint('company_id', name='uq_mpm_company'),)

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'brand': self.brand,
            'last4': self.last4,
            'exp_month': self.exp_month,
            'exp_year': self.exp_year,
            'cardholder_name': self.cardholder_name,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
