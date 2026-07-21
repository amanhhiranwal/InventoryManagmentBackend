from pydantic import BaseModel

class CreateCategoryGroupRequest(BaseModel):
    name: str
    code: str
