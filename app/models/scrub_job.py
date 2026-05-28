from datetime import datetime
from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, Enum, ForeignKey,
    Boolean, Numeric, JSON, Index,
)
from app.extensions import Base


class ScrubJob(Base):
    """A mailer's upload-and-scrub workflow.

    Lifecycle:
        created -> uploading -> awaiting_mapping -> importing -> validating ->
        scrubbing -> priced -> awaiting_payment -> paid -> complete
        (or failed at any point)

    Upload data lives in Spaces (see s3_key); parsed rows live in
    scrub_job_records + scrub_job_record_fields. The legacy local-file
    columns (uploaded_file_path, result_file_path) remain for any rows
    written before S3 wiring but are not populated by new jobs.
    """
    __tablename__ = 'scrub_jobs'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(BigInteger, ForeignKey('mailer_companies.id'), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey('mailer_users.id'), nullable=False, index=True)

    status = Column(Enum(
        'created', 'uploading', 'uploaded',
        'awaiting_mapping', 'importing',
        'validating', 'scrubbing', 'priced',
        'awaiting_payment', 'paid', 'complete', 'failed',
        name='scrub_job_status'),
        default='created', nullable=False, index=True)

    selected_verticals_json = Column(JSON, nullable=True)   # [{vertical_id, subcategory_ids:[]}]

    # ── Spaces-backed upload ──
    s3_bucket = Column(String(120), nullable=True)
    s3_key = Column(String(500), nullable=True)
    multipart_upload_id = Column(String(255), nullable=True)
    original_filename = Column(String(255), nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    content_type = Column(String(120), nullable=True)
    detected_headers_json = Column(JSON, nullable=True)     # ['Email','First','Last',...]
    delimiter = Column(String(8), nullable=True)            # sniffed delimiter for CSV/TSV/TXT

    # ── Legacy local-FS fields (kept for backward-compat row reads) ──
    uploaded_file_path = Column(String(500), nullable=True)

    uploaded_count = Column(Integer, default=0)
    cleaning_opted_in = Column(Boolean, default=False)
    validated_count = Column(Integer, default=0)
    invalid_count = Column(Integer, default=0)
    unique_count = Column(Integer, default=0)
    overlap_count = Column(Integer, default=0)

    rate_per_record = Column(Numeric(10, 4), default=0)
    price_cents = Column(BigInteger, default=0)
    stripe_payment_intent_id = Column(String(120), nullable=True)
    paid_at = Column(DateTime, nullable=True)

    # ── Result file (Spaces) ──
    result_s3_key = Column(String(500), nullable=True)
    result_file_path = Column(String(500), nullable=True)   # legacy
    result_filename = Column(String(160), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    failure_reason = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index('idx_sj_company_status', 'company_id', 'status'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'company_id': self.company_id,
            'user_id': self.user_id,
            'status': self.status,
            'selected_verticals': self.selected_verticals_json or [],
            'cleaning_opted_in': bool(self.cleaning_opted_in),
            'original_filename': self.original_filename,
            'file_size_bytes': int(self.file_size_bytes or 0),
            'detected_headers': self.detected_headers_json or [],
            'uploaded_count': int(self.uploaded_count or 0),
            'validated_count': int(self.validated_count or 0),
            'invalid_count': int(self.invalid_count or 0),
            'unique_count': int(self.unique_count or 0),
            'overlap_count': int(self.overlap_count or 0),
            'rate_per_record': float(self.rate_per_record) if self.rate_per_record else 0.0,
            'price_cents': int(self.price_cents or 0),
            'price_dollars': round(int(self.price_cents or 0) / 100, 2),
            'result_filename': self.result_filename,
            'failure_reason': self.failure_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
        }
