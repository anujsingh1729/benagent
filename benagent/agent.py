"""
BDD (Benefit Design Document) PDF Parser
=========================================
Uses Claude Vision API to parse complex benefit design documents
into structured JSON with full hierarchy preservation.

Pipeline:
  1. Convert PDF pages → high-res images (pdf2image)
  2. Send each page image to Claude Vision with a schema prompt
  3. Collect per-page JSON fragments
  4. Merge fragments into a single document JSON
  5. Validate and write final output

Requirements:
    pip install pdf2image anthropic pillow
    brew install poppler   # macOS (for pdf2image)
    apt install poppler-utils  # Linux

Usage:
    python bdd_parser.py path/to/document.pdf
    python bdd_parser.py path/to/document.pdf --output result.json
"""

import anthropic
import base64
import json
import sys
import argparse
from pathlib import Path
from io import BytesIO
from PIL import Image


# ─────────────────────────────────────────────
# STEP 1: PDF → Page Images
# ─────────────────────────────────────────────

def pdf_to_images(pdf_path: str, dpi: int = 250) -> list[Image.Image]:
    """
    Convert each PDF page to a PIL Image at the given DPI.
    Higher DPI = better OCR accuracy but larger payloads.
    250 DPI is a good balance for form documents.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise ImportError("Run: pip install pypdfium2")

    print(f"  Converting PDF to images at {dpi} DPI...")
    pdf = pdfium.PdfDocument(pdf_path)
    scale = dpi / 72  # pypdfium2 uses 72 DPI as base
    images = []
    for page in pdf:
        bitmap = page.render(scale=scale, rotation=0)
        images.append(bitmap.to_pil())
    print(f"  → {len(images)} pages found")
    return images


def image_to_base64(image: Image.Image, max_width: int = 1600) -> tuple[str, str]:
    """
    Encode a PIL Image to base64 PNG, optionally downscaling wide pages.
    Returns (base64_string, media_type).
    """
    # Downscale if too wide (keeps API payload reasonable)
    if image.width > max_width:
        ratio = max_width / image.width
        new_size = (max_width, int(image.height * ratio))
        image = image.resize(new_size, Image.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    b64 = base64.standard_b64encode(buffer.getvalue()).decode("utf-8")
    return b64, "image/png"


# ─────────────────────────────────────────────
# STEP 2: Per-Page Extraction Schema & Prompts
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are a precise document parsing assistant specializing in 
insurance Benefit Design Documents (BDDs). Your job is to extract ALL data from 
the provided page image into a structured JSON object.

CRITICAL RULES:
1. Extract EVERY field visible on the page — leave nothing out
2. Preserve hierarchical relationships (in-network vs out-of-network, 
   per-service rows, copay/coinsurance pairs, etc.)
3. For checkboxes: use true/false
4. For empty/N/A fields: use null
5. For dollar amounts: extract as strings (e.g. "$250", "$20 Per Day/Visit")
6. For percentage amounts: extract as strings (e.g. "10%", "30%")
7. Preserve conditional notes exactly as written (e.g. "Pend for MNR")
8. Do NOT infer or guess — only extract what is explicitly on the page
9. Return ONLY valid JSON — no markdown, no explanation, no backticks
"""

PAGE_EXTRACTION_PROMPT = """Extract all data from this Benefit Design Document page into JSON.

Use this schema as a guide (add fields as needed if the page contains data not covered):

{
  "page_number": <int>,
  "section": "<section name visible on page>",
  "content": {
    // For financial maximums pages:
    "deductible": {
      "applies_to_benefit": <bool>,
      "in_network": {
        "applies": <bool>,
        "applies_to": { "OP": <bool>, "IP": <bool>, "ALOC": <bool> },
        "embedded": { "individual": "<amount>", "family": "<amount>" },
        "aggregate": { "EE": "<amount or null>", "family": "<amount or null>" },
        "applies_to_all_services": <bool>,
        "exceptions": "<text or null>"
      },
      "out_of_network": { <same structure as in_network> },
      "combined_in_out_of_network": <bool>,
      "inn_applies_to_oon": <bool>,
      "oon_applies_to_inn": <bool>,
      "inn_deductible_for_oon_services": ["<service name>", ...]
    },
    "out_of_pocket": { <same structure as deductible, plus cost_sharing_applies_to field> },

    // For outpatient benefits pages:
    "EAP": {
      "covered": <bool>,
      "visit_limit": "<number>",
      "limit_type": "<per member/per problem/per year>",
      "exceptions": "<text or null>"
    },
    "outpatient_therapies": [
      {
        "service": "<service name>",
        "in_network": {
          "covered": <bool>,
          "psych_or_sa": "<Psych Only | SA Only | Psych & SA>",
          "auth_requirement": "<Managed | No Auth Required | Auth Required>",
          "copay": "<amount or null>",
          "coinsurance": "<percentage or null>",
          "max_visit": "<amount or null>"
        },
        "out_of_network": { <same structure> }
      }
    ],
    "ABA": { ... },

    // For inpatient benefits pages:
    "pre_certification_penalty": {
      "applies_to_benefit": <bool>,
      "in_network": { "applies": <bool>, "penalty": "<text or null>" },
      "out_of_network": { "applies": <bool>, "penalty": "<text or null>" }
    },
    "inpatient_facility": [
      {
        "service": "<IP Psych | IP Substance | IP Detox | Administratively Necessary Days>",
        "in_network": {
          "covered": <bool>,
          "psych_or_sa": "<Psych Only | SA Only | Psych & SA>",
          "auth_requirement": "<text>",
          "if_no_auth_on_file": "<text or null>",
          "penalty_applies": <bool>,
          "copay": "<amount or null>",
          "coinsurance": "<percentage or null>"
        },
        "out_of_network": { <same structure> }
      }
    ],
    "inpatient_professional_services": [ ... ],
    "alternative_levels_of_care": [ ... ],

    // For other benefits pages:
    "emergency_services": [ ... ],
    "ECT": [ ... ],
    "out_of_country": {
      "covered": <bool>,
      "emergency_only": <bool>,
      "paid_at_inn_billed_charges": <bool>,
      "service_dx_codes_required": <bool>,
      "payment_to_member": <bool>,
      "member_name_dob_required": <bool>,
      "provider_name_address_required": <bool>,
      "rate_of_exchange_required": <bool>
    },
    "other_outpatient_professional": [ ... ],
    "other_services": [ ... ],
    "transition_benefits": {
      "covered": <bool>,
      "applies_to_psych": <bool>,
      "applies_to_sub_abuse": <bool>,
      "applies_to_OP": <bool>,
      "applies_to_IP": <bool>,
      "applies_to_ALOC": <bool>,
      "auth_required": "<text>",
      "start_date": "<date>",
      "end_date": "<date>",
      "paid_same_as_regular": <bool>
    },

    // For claims/special handling pages:
    "claims_handling": {
      "coversheeted_carrier": "<text or null>",
      "carrier_customer_service_phone": "<text or null>",
      "comments": "<text or null>",
      "break_in_treatment": "<text or null>",
      "expatriates_covered": <bool>,
      "medicare_primary_auth_required": <bool or null>
    }
  }
}

Return ONLY the JSON object for this page. No markdown, no explanation."""


# ─────────────────────────────────────────────
# STEP 3: Call Claude Vision Per Page
# ─────────────────────────────────────────────

def extract_page_json(
    client: anthropic.Anthropic,
    image: Image.Image,
    page_num: int,
    model: str = "claude-haiku-4-5-20251001"
) -> dict:
    """
    Send one page image to Claude Vision and get back a parsed JSON dict.
    """
    print(f"  Extracting page {page_num}...")
    b64_image, media_type = image_to_base64(image)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": PAGE_EXTRACTION_PROMPT
                    }
                ],
            }
        ],
    )

    raw_text = response.content[0].text.strip()

    # Strip any accidental markdown fences
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        raw_text = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])

    try:
        page_data = json.loads(raw_text)
        page_data["page_number"] = page_num  # Ensure page number is set
        return page_data
    except json.JSONDecodeError as e:
        print(f"  ⚠️  JSON parse error on page {page_num}: {e}")
        print(f"  Raw response preview: {raw_text[:300]}")
        # Return a fallback structure with the raw text for manual review
        return {
            "page_number": page_num,
            "parse_error": str(e),
            "raw_text": raw_text
        }


# ─────────────────────────────────────────────
# STEP 4: Merge Page JSONs into Document JSON
# ─────────────────────────────────────────────

MERGE_PROMPT = """You are given a list of JSON objects, each representing one page 
of a multi-page Benefit Design Document. Your task is to merge them into a single 
comprehensive JSON document.

Rules:
1. Combine all sections into a single top-level structure
2. If a table or section spans multiple pages, merge the rows/entries together
3. Preserve ALL data — do not drop any fields
4. The final structure should be:
{
  "client_information": { ... },
  "financial_maximums": {
    "deductible": { ... },
    "out_of_pocket": { ... }
  },
  "outpatient_benefits": {
    "EAP": { ... },
    "outpatient_therapies": [ ... ],
    "ABA": { ... }
  },
  "inpatient_benefits": {
    "pre_certification_penalty": { ... },
    "inpatient_facility": [ ... ],
    "inpatient_professional_services": [ ... ],
    "alternative_levels_of_care": [ ... ]
  },
  "other_benefits": {
    "emergency_services": [ ... ],
    "ECT": [ ... ],
    "out_of_country": { ... },
    "other_outpatient_professional": [ ... ],
    "other_services": [ ... ],
    "transition_benefits": { ... }
  },
  "claims_handling": { ... },
  "metadata": {
    "total_pages": <int>,
    "effective_date": "<date>",
    "client_name": "<name>",
    "benefit_package": "<name>"
  }
}

Return ONLY the merged JSON. No markdown, no explanation."""


def merge_page_jsons(
    client: anthropic.Anthropic,
    page_jsons: list[dict],
    model: str = "claude-haiku-4-5-20251001"
) -> dict:
    """
    Send all page JSONs to Claude and ask it to merge them into one document.
    Chunks large documents to avoid token limits.
    """
    print("  Merging all pages into final document JSON...")

    # If too many pages, merge in chunks first
    CHUNK_SIZE = 4
    if len(page_jsons) > CHUNK_SIZE:
        chunks = [page_jsons[i:i+CHUNK_SIZE] for i in range(0, len(page_jsons), CHUNK_SIZE)]
        merged_chunks = []
        for idx, chunk in enumerate(chunks):
            print(f"  Merging chunk {idx+1}/{len(chunks)}...")
            merged_chunks.append(_call_merge_api(client, chunk, model))
        print("  Merging all chunks into final document...")
        return _call_merge_api(client, merged_chunks, model)

    return _call_merge_api(client, page_jsons, model)


def _call_merge_api(
    client: anthropic.Anthropic,
    page_jsons: list[dict],
    model: str
) -> dict:
    pages_text = json.dumps(page_jsons, indent=2)

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        messages=[
            {
                "role": "user",
                "content": f"{MERGE_PROMPT}\n\nPage JSONs to merge:\n{pages_text}"
            }
        ],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        raw_text = "\n".join(lines[1:-1]) if lines[-1] == "```" else "\n".join(lines[1:])

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"  ⚠️  Merge JSON parse error: {e}")
        return {
            "merge_error": str(e),
            "raw_pages": page_jsons
        }


# ─────────────────────────────────────────────
# STEP 5: Validation
# ─────────────────────────────────────────────

REQUIRED_TOP_LEVEL_KEYS = [
    "client_information",
    "financial_maximums",
    "outpatient_benefits",
    "inpatient_benefits",
    "other_benefits",
    "claims_handling",
    "metadata"
]

def validate_document(doc: dict) -> list[str]:
    """
    Basic validation — returns a list of warnings for missing expected sections.
    Extend this with deeper schema validation as needed.
    """
    warnings = []

    for key in REQUIRED_TOP_LEVEL_KEYS:
        if key not in doc:
            warnings.append(f"Missing top-level section: '{key}'")

    # Check financial maximums
    fin = doc.get("financial_maximums", {})
    if "deductible" not in fin:
        warnings.append("Missing: financial_maximums.deductible")
    if "out_of_pocket" not in fin:
        warnings.append("Missing: financial_maximums.out_of_pocket")

    # Check pages with parse errors
    if "raw_pages" in doc:
        warnings.append("Merge step failed — raw pages returned instead of merged doc")
    
    for page in doc.get("raw_pages", []):
        if "parse_error" in page:
            warnings.append(f"Page {page.get('page_number')} had a parse error")

    return warnings


# ─────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────

def parse_bdd_pdf(
    pdf_path: str,
    output_path: str,
    dpi: int = 250,
    model: str = "claude-haiku-4-5-20251001",
    api_key: str = None
) -> dict:
    """
    Full pipeline: PDF → images → per-page JSON → merged document JSON.

    Args:
        pdf_path:    Path to the BDD PDF file
        output_path: Where to write the JSON output (optional)
        dpi:         Image resolution for PDF rendering (higher = more accurate)
        model:       Claude model to use
        api_key:     Anthropic API key (or set ANTHROPIC_API_KEY env var)

    Returns:
        Parsed document as a Python dict
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Initialize Anthropic client
    # If api_key is None, the client reads from ANTHROPIC_API_KEY env var
    client_kwargs = {}
    if api_key:
        client_kwargs["api_key"] = api_key
    client = anthropic.Anthropic(**client_kwargs)

    print(f"\n{'='*60}")
    print(f"  BDD Parser Pipeline")
    print(f"  File: {pdf_path.name}")
    print(f"{'='*60}")

    # ── Step 1: PDF → Images ──────────────────────
    print("\n[Step 1] Converting PDF to images...")
    images = pdf_to_images(str(pdf_path), dpi=dpi)

    # ── Step 2 & 3: Extract each page ────────────
    print(f"\n[Step 2] Extracting {len(images)} pages with Claude Vision...")
    page_jsons = []
    for i, image in enumerate(images, start=1):
        page_data = extract_page_json(client, image, page_num=i, model=model)
        page_jsons.append(page_data)
        print(f"  ✓ Page {i} extracted")

    # Save intermediate page JSONs (useful for debugging)
    intermediate_path = pdf_path.with_suffix(".pages.json")
    with open(intermediate_path, "w") as f:
        json.dump(page_jsons, f, indent=2)
    print(f"\n  Intermediate page JSONs saved to: {intermediate_path}")

    # ── Step 4: Merge ─────────────────────────────
    print("\n[Step 3] Merging pages into document JSON...")
    document = merge_page_jsons(client, page_jsons, model=model)

    # ── Step 5: Validate ──────────────────────────
    print("\n[Step 4] Validating output...")
    warnings = validate_document(document)
    if warnings:
        print(f"  ⚠️  {len(warnings)} validation warning(s):")
        for w in warnings:
            print(f"     - {w}")
        document["_validation_warnings"] = warnings
    else:
        print("  ✓ Validation passed — all expected sections present")

    # ── Write Output ──────────────────────────────
    if output_path is None:
        output_path = pdf_path.with_suffix(".json")

    with open(output_path, "w") as f:
        json.dump(document, f, indent=2)

    print(f"\n{'='*60}")
    print(f"  ✅ Done! Output written to: {output_path}")
    print(f"{'='*60}\n")

    return document


# ─────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Parse a Benefit Design Document PDF into structured JSON"
    )
    parser.add_argument("pdf", help="Path to the BDD PDF file")
    parser.add_argument("--output", "-o", help="Output JSON path (default: same name as PDF)")
    parser.add_argument("--dpi", type=int, default=250, help="Image DPI for rendering (default: 250)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Claude model to use")
    parser.add_argument("--api-key", help="Anthropic API key (or set ANTHROPIC_API_KEY env var)")

    args = parser.parse_args()

    result = parse_bdd_pdf(
        pdf_path=args.pdf,
        output_path=args.output,
        dpi=args.dpi,
        model=args.model,
        api_key=args.api_key
    )

    # Print summary
    meta = result.get("metadata", {})
    print("Document Summary:")
    print(f"  Client: {meta.get('client_name', 'N/A')}")
    print(f"  Package: {meta.get('benefit_package', 'N/A')}")
    print(f"  Effective: {meta.get('effective_date', 'N/A')}")
    print(f"  Top-level keys: {list(result.keys())}")
