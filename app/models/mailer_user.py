from datetime import datetime
from sqlalchemy import Column, BigInteger, String, DateTime, Enum, ForeignKey, Index
from app.extensions import Base


class MailerUser(Base):
    """A person (login). Tied to one MailerCompany."""
    __tablename__ = 'mailer_users'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(BigInteger, ForeignKey('mailer_companies.id'), nullable=False, index=True)
    full_name = Column(String(120), nullable=False)
    email = Column(String(255), nullable=False, unique=True)
    phone = Column(String(30), nullable=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum('owner', 'member', name='mailer_user_role'), default='owner')
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index('idx_mu_email', 'email'),)

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'full_name': self.full_name,
            'email': self.email,
            'phone': self.phone,
            'role': self.role,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
        }
