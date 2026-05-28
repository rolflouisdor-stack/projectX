"""EAV-style storage for non-standard columns from an uploaded list.

When a user's file has columns the standard schema doesn't cover (e.g. a
car-loan list with `vin`, `make`, `model`, `year`), the user names each
column in the mapping step and the import worker stuffs each parsed value
into one row here. `scrub_job_id` is denormalized off `record_id` so a
"delete all custom fields for job X" cleanup runs in a single index range.
"""
from datetime import datetime
from sqlalchemy import (
    Column, BigInteger, Text, String, DateTime, ForeignKey, Index,
)
from app.extensions import Base


class ScrubJobRecordField(Base):
    __tablename__ = 'scrub_job_record_fields'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    record_id = Column(BigInteger, ForeignKey('scrub_job_records.id', ondelete='CASCADE'),
                       nullable=False, index=True)
    scrub_job_id = Column(BigInteger, ForeignKey('scrub_jobs.id', ondelete='CASCADE'),
                          nullable=False, index=True)
    field_name = Column(String(120), nullable=False)
    value_text = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('idx_sjrf_record', 'record_id'),
        Index('idx_sjrf_job_field', 'scrub_job_id', 'field_name'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'field_name': self.field_name,
            'value_text': self.value_text,
        }
