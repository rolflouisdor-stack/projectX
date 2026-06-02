"""One row per parsed line of an uploaded list.

Standard fields are first-class indexed columns so the scrub engine and any
future analytics can run plain SQL against them. Custom (non-standard)
columns from the upload land in `scrub_job_record_fields` as EAV rows
joined back via `record_id`.

The import worker populates rows in bulk via the ORM in batches. The scrub
engine then mutates `is_valid` / `is_unique` / `invalid_reason` in place.
"""
from datetime import datetime
from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, Boolean, ForeignKey, Index,
)
from app.extensions import Base


class ScrubJobRecord(Base):
    __tablename__ = 'scrub_job_records'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # No per-column index=True here: the composite idx_sjr_job_rowidx covers
    # scrub_job_id (and its FK), and idx_sjr_company covers company_id. Avoids
    # the duplicate indexes that inflated every insert.
    scrub_job_id = Column(BigInteger, ForeignKey('scrub_jobs.id', ondelete='CASCADE'),
                          nullable=False)
    company_id = Column(BigInteger, ForeignKey('mailer_companies.id'),
                        nullable=False)
    row_index = Column(Integer, nullable=False)   # 1-based, header row excluded

    # ── Standard fields ──
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    email = Column(String(320), nullable=True)               # RFC 5321 max
    email_normalized = Column(String(320), nullable=True)    # lowered+stripped
    phone = Column(String(40), nullable=True)
    address = Column(String(255), nullable=True)
    city = Column(String(120), nullable=True)
    state = Column(String(40), nullable=True)
    zip = Column(String(20), nullable=True)

    # ── Scrub bookkeeping (the engine fills these) ──
    is_valid = Column(Boolean, nullable=True)
    invalid_reason = Column(String(80), nullable=True)   # 'syntax' | 'disposable' | 'role' | 'undeliverable'
    is_unique = Column(Boolean, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_sjr_job_rowidx', 'scrub_job_id', 'row_index'),
        Index('idx_sjr_email_norm', 'email_normalized'),
        Index('idx_sjr_company', 'company_id'),
    )

    def to_dict(self, include_standard=True):
        out = {
            'id': self.id,
            'row_index': int(self.row_index),
            'is_valid': self.is_valid,
            'invalid_reason': self.invalid_reason,
            'is_unique': self.is_unique,
        }
        if include_standard:
            out.update({
                'first_name': self.first_name,
                'last_name': self.last_name,
                'email': self.email,
                'phone': self.phone,
                'address': self.address,
                'city': self.city,
                'state': self.state,
                'zip': self.zip,
            })
        return out
