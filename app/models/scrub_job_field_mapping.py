"""User's column-mapping decisions for a single uploaded file.

One row per detected header in the uploaded CSV/XLSX. Captures whether the
header maps to one of the standard fixed columns (`first_name`, `email`, …)
or to a custom field name the user provided, plus an opt-in to skip a
column entirely.

The import worker reads these rows to know how to populate each parsed row
into `scrub_job_records` (standard fields) and `scrub_job_record_fields`
(EAV for custom fields).
"""
from datetime import datetime
from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, Boolean, ForeignKey,
    UniqueConstraint, Index,
)
from app.extensions import Base


# Fixed standard fields the wizard offers as drop-down targets. Anything not
# in this set is treated as a custom (EAV) field.
STANDARD_FIELDS = (
    'first_name', 'last_name', 'email', 'phone',
    'address', 'city', 'state', 'zip',
)


class ScrubJobFieldMapping(Base):
    __tablename__ = 'scrub_job_field_mappings'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    scrub_job_id = Column(BigInteger, ForeignKey('scrub_jobs.id', ondelete='CASCADE'),
                          nullable=False, index=True)

    source_header = Column(String(255), nullable=False)   # column name from the file
    column_index = Column(Integer, nullable=False)         # 0-based position in file
    target_field = Column(String(120), nullable=False)     # 'email' or 'car_model'
    is_standard = Column(Boolean, nullable=False, default=False)
    skip = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('scrub_job_id', 'source_header', name='uk_sjfm_job_header'),
        Index('idx_sjfm_job', 'scrub_job_id'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'source_header': self.source_header,
            'column_index': int(self.column_index),
            'target_field': self.target_field,
            'is_standard': bool(self.is_standard),
            'skip': bool(self.skip),
        }
