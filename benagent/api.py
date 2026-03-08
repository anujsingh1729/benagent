from fastapi import FastAPI, UploadFile, File
import json
import tempfile
import os
from bencode import main  # imports your existing main() function

app = FastAPI()

@app.post("/process-benefits")
async def process_benefits(
    json_file: UploadFile = File(...),
    xlsx_file: UploadFile = File(...)
):
    # Save uploaded files to temp location
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp_json:
        tmp_json.write(await json_file.read())
        json_path = tmp_json.name

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp_xlsx:
        tmp_xlsx.write(await xlsx_file.read())
        xlsx_path = tmp_xlsx.name

    # Load JSON and run your existing logic
    with open(json_path, 'r') as f:
        input_json = json.load(f)

    result = main(input_json, xlsx_path)

    # Cleanup temp files
    os.unlink(json_path)
    os.unlink(xlsx_path)

    return result