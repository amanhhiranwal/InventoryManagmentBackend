from typing import Optional

from pydantic import BaseModel


class CreateStateRequest(BaseModel):
    name: str
    code: str
    #: The two-digit GST state code. Left out, the statutory table
    #: decides; given, it overrides - which is how a state that table
    #: does not know becomes taxable without a deployment.
    gst_code: Optional[str] = None
    country: Optional[str] = "India"


class UpdateStateRequest(BaseModel):
    """Everything correctable from Masters > States.

    All optional: a form that does not carry a field is not asking for
    it to be blanked.
    """

    name: Optional[str] = None
    code: Optional[str] = None
    gst_code: Optional[str] = None
    country: Optional[str] = None
