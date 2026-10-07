from sqlalchemy import Boolean, Column, DateTime, Integer, String, func

from app.database.base import Base


class WarrantyTerm(Base):
    """A length of cover that may be quoted.

    The lengths are company-wide - every document offers the same three -
    but what they cost is not, because five years on a panel and five
    years on a camera are different undertakings. So this holds the name
    and nothing about the price; the rate for each term is held against
    each product, on the product record.

    One term is the standard one: it is what a line falls back to, and it
    is the cover already included in the price.
    """

    __tablename__ = "sales_warranty_term"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: What the customer sees: "3 Years", "5 Years".
    name = Column(String(100), unique=True, nullable=False)

    #: Years of cover, used to order the list and to find the standard term.
    years = Column(Integer, nullable=False, default=0)

    #: The term a line gets when nobody picks one. Exactly one should hold
    #: this, and it is the one included in the price.
    is_default = Column(Boolean, default=False, nullable=False)

    description = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
