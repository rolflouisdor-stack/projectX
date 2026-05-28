"""Register every model so SQLAlchemy's create_all picks them up."""
from app.models.mailer_company import MailerCompany
from app.models.mailer_user import MailerUser
from app.models.vertical import Vertical
from app.models.subcategory import Subcategory
from app.models.scrub_job import ScrubJob
from app.models.scrub_job_field_mapping import ScrubJobFieldMapping
from app.models.scrub_job_record import ScrubJobRecord
from app.models.scrub_job_record_field import ScrubJobRecordField
from app.models.purchase_job import PurchaseJob
from app.models.activity_log import ActivityLog
from app.models.promo_banner import PromoBanner
from app.models.promo_code import PromoCode
from app.models.pricing_tier import PricingTier
from app.models.payment_method import PaymentMethod
