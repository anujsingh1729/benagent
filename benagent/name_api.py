from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class NameInput(BaseModel):
    first_name: str
    last_name: str

class NameOutput(BaseModel):
    consolidated_name: str

@app.post("/consolidate-name", response_model=NameOutput)
def consolidate_name(input: NameInput):
    consolidated = f"{input.first_name} {input.last_name}"
    return NameOutput(consolidated_name=consolidated)