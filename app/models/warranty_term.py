from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, func

from app.database.base import Base


class WarrantyTerm(Base):
    """A length of cover, and what extending to it costs.

    The standard cover is included in the price, so it carries an uplift of
    nothing. Longer cover is sold on top, priced either as a flat amount per
    unit or as a percentage of the line - which it is depends on the
    product, so both are offered and a term says which one it uses.

    The rate lives here rather than on the product because it is a
    commercial decision that changes for the whole catalogue at once, and
    because a salesperson must not be able to retype it on a proposal.
    """

    __tablename__ = "sales_warranty_term"

    id = Column(Integer, primary_key=True, autoincrement=True)

    #: What the customer sees: "3 Years", "5 Years".
    name = Column(String(100), unique=True, nullable=False)

    #: Years of cover, used to order the list and to find the standard term.
    years = Column(Integer, nullable=False, default=0)

    #: "PERCENT" of the line, or "AMOUNT" per unit.
    rate_mode = Column(String(10), nullable=False, default="PERCENT")

    #: How much extending to this term costs, read against rate_mode.
    rate = Column(Float, nullable=False, default=0.0)

    #: The term a line gets when nobody picks one. Exactly one should hold
    #: this, and it is the one included in the price.
    is_default = Column(Boolean, default=False, nullable=False)

    description = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )
