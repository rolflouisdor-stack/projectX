from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Index
from app.extensions import Base


class Subcategory(Base):
    __tablename__ = 'subcategories'

    id = Column(Integer, primary_key=True, autoincrement=True)
    vertical_id = Column(Integer, ForeignKey('verticals.id'), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)

    __table_args__ = (Index('idx_sub_vert', 'vertical_id'),)

    def to_dict(self):
        return {
            'id': self.id,
            'vertical_id': self.vertical_id,
            'name': self.name,
            'is_active': bool(self.is_active),
            'sort_order': int(self.sort_order or 0),
        }
