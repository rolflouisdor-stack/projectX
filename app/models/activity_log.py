from datetime import datetime
from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, ForeignKey, JSON, Index,
)
from app.extensions import Base


# Canonical action types — keep this list small + meaningful. Add new ones
# rather than overloading existing ones.
ACTION_LOGIN          = 'login'
ACTION_SIGNUP         = 'signup'
ACTION_LOGOUT         = 'logout'
ACTION_VIEW_DASHBOARD = 'view_dashboard'
ACTION_VIEW_VERTICAL  = 'view_vertical'      # user opened/inspected a vertical card
ACTION_TRACK_VERTICAL = 'track_vertical'     # user selected (toggled-on) a vertical
ACTION_UPLOAD_LIST    = 'upload_list'
ACTION_RUN_SCRUB      = 'run_scrub'
ACTION_BUY_INIT       = 'buy_init'
ACTION_PURCHASE       = 'purchase'           # payment captured
ACTION_DOWNLOAD       = 'download'           # file actually downloaded
ACTION_APPLY_PROMO    = 'apply_promo'


class ActivityLog(Base):
    """Every meaningful interaction a mailer takes — the data source behind the
    cross-system activity API the CX3 Dashboard reads from."""
    __tablename__ = 'activity_log'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(BigInteger, ForeignKey('mailer_companies.id'), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey('mailer_users.id'), nullable=True, index=True)
    action = Column(String(40), nullable=False, index=True)

    # Optional pointers
    vertical_id = Column(Integer, nullable=True, index=True)
    scrub_job_id = Column(BigInteger, nullable=True, index=True)
    purchase_job_id = Column(BigInteger, nullable=True, index=True)

    # Flexible payload: free-form details about the action (verticals selected,
    # promo code applied, price, etc.)
    meta = Column(JSON, nullable=True)

    ip = Column(String(45), nullable=True)
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index('idx_act_company_action', 'company_id', 'action'),
        Index('idx_act_company_created', 'company_id', 'created_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'user_id': self.user_id,
            'action': self.action,
            'vertical_id': self.vertical_id,
            'scrub_job_id': self.scrub_job_id,
            'purchase_job_id': self.purchase_job_id,
            'meta': self.meta or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
