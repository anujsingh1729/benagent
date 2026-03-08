import json
import re
import openpyxl
from copy import deepcopy


def load_benefit_table(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        code, desc = row[0], row[1]
        if code and desc:
            rows.append({"code": str(code).strip(), "desc": str(desc).strip().upper()})
    return rows


BENEFIT_RULES = {
    "psychotherapy":              (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "group therapy":              (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "family therapy":             (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "crisis psychotherapy":       (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "medication mgt":             (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "evaluations":                (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "consultations":              (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "in-home therapy":            (["OUTPATIENT"], ["TESTING", "ECT", "LAB", "EMERGENCY"]),
    "psychtesting":               (["PSYCH TESTING"], []),
    "psych testing":              (["PSYCH TESTING"], []),
    "aba":                        (["ABA"], []),
    "ip psych":                   (["INPATIENT", "FACILITY", "PSYCH"], ["PROFESSIONAL", "DETOX", "TESTING"]),
    "ip substance":               (["INPATIENT", "FACILITY", "SA"], ["PROFESSIONAL", "TESTING"]),
    "ip detox":                   (["INPATIENT", "DETOX"], []),
    "ip professional psych":      (["INPATIENT", "PROFESSIONAL", "PSYCH"], ["DETOX", "TESTING"]),
    "ip professional sa":         (["INPATIENT", "PROFESSIONAL", "SA"], ["DETOX", "TESTING"]),
    "ip prof detox":              (["INPATIENT", "PROFESSIONAL", "DETOX"], []),
    "medical consults on a bh floor": (["INPATIENT", "PROFESSIONAL"], []),
    "bh consults on a medical floor": (["INPATIENT", "PROFESSIONAL"], []),
    "ip psych testing":           (["INPATIENT", "PSYCH TESTING"], []),
    "group home":                 (["GROUP HOME"], []),
    "halfway house":              (["HALFWAY HOUSE"], []),
    "iop":                        (["IOP"], []),
    "rtc":                        (["RTC"], []),
    "partial hosp":               (["PARTIAL"], []),
    "ambulatory detox":           (["AMBULATORY", "DETOX"], []),
    "crisis intervention":        (["CRISIS"], []),
    "treatment/observation room (23 hour bed)": (["23 HOUR"], []),
    "observation room (72 hour bed)":           (["72 HOUR"], []),
    "er facility":                (["EMERGENCY", "FACILITY"], []),
    "er professional":            (["EMERGENCY", "PROFESSIONAL"], []),
    "ect ip facility":            (["ECT", "INPATIENT", "FACILITY"], []),
    "ect ip professional":        (["ECT", "INPATIENT", "PROFESSIONAL"], []),
    "ect ip anesthesia":          (["ECT", "INPATIENT", "ANESTHESIA"], []),
    "ect op facility":            (["ECT", "OUTPATIENT", "FACILITY"], []),
    "ect op professional":        (["ECT", "OUTPATIENT", "PROFESSIONAL"], []),
    "ect op anesthesia":          (["ECT", "OUTPATIENT", "ANESTHESIA"], []),
    "methadone maintenance":      (["METHADONE"], []),
    "suboxone":                   (["SUBOXONE"], []),
    "biofeedback":                (["BIOFEEDBACK"], []),
    "hypnotherapy":               (["HYPNOTHERAPY"], []),
    "tms - transcranial magnetic stimulation": (["TMS"], []),
    "op labs & diagnostic testing":  (["LAB"], ["ECT", "INPATIENT"]),
    "ip labs & diagnostic testing":  (["LAB", "INPATIENT"], ["ECT"]),
    "injections":                 (["INJECTIONS"], []),
    "ambulance":                  (["AMBULANCE"], []),
    "deductible":                 (["DEDUCTIBLE"], []),
    "out of pocket":              (["OUT OF POCKET"], []),
}


def desc_network(desc):
    has_oon = any(m in desc for m in ["OUT-OF-NETWORK", "OUT OF NETWORK"]) or bool(re.search(r'\bOON\b', desc))
    has_inn = any(m in desc for m in ["IN-NETWORK", "IN NETWORK"]) or bool(re.search(r'\bINN\b', desc))
    if has_inn and has_oon:
        return "BOTH"
    if has_oon:
        return "OON"
    if has_inn:
        return "INN"
    return None


def desc_category(desc):
    has_sa = bool(re.search(r'\bSA\b|SUBSTANCE ABUSE', desc))
    has_psych = "PSYCH" in desc
    if has_psych and has_sa:
        return "BOTH"
    if has_sa:
        return "SA"
    if has_psych:
        return "PSYCH"
    return None


def classify_psych_sa(value):
    if not value:
        return "BOTH"
    val = str(value).upper().strip()
    if "PSYCH" in val and ("SA" in val or "SUBSTANCE" in val):
        return "BOTH"
    if "SA" in val or "SUBSTANCE" in val:
        return "SA"
    if "PSYCH" in val:
        return "PSYCH"
    return "BOTH"


def find_best_rule(service_name):
    name = re.sub(r'[\s*]+$', '', service_name.lower().strip())
    if name in BENEFIT_RULES:
        return name
    for rule_key in BENEFIT_RULES:
        if rule_key in name or name in rule_key:
            return rule_key
    return None


def match_benefit(service_name, network_type, psych_sa, benefit_table):
    rule_key = find_best_rule(service_name)
    if not rule_key:
        return []
    required_kw, excluded_kw = BENEFIT_RULES[rule_key]
    codes = []
    for entry in benefit_table:
        desc = entry["desc"]
        if not all(kw in desc for kw in required_kw):
            continue
        if any(kw in desc for kw in excluded_kw):
            continue
        row_net = desc_network(desc)
        if network_type and row_net and row_net != "BOTH" and row_net != network_type:
            continue
        if psych_sa != "BOTH":
            row_cat = desc_category(desc)
            if row_cat and row_cat != "BOTH" and row_cat != psych_sa:
                continue
        codes.append(entry["code"])
    return codes


def add_codes_to_service(service_obj, benefit_table):
    """
    Add benefit_codes into in_network and out_of_network blocks if they exist,
    otherwise add at the top level of the service object.
    """
    service_name = service_obj.get("service", "")

    if "in_network" in service_obj and isinstance(service_obj["in_network"], dict):
        psych_sa = classify_psych_sa(service_obj["in_network"].get("psych_or_sa", ""))
        service_obj["in_network"]["benefit_codes"] = match_benefit(service_name, "INN", psych_sa, benefit_table)

    if "out_of_network" in service_obj and isinstance(service_obj["out_of_network"], dict):
        psych_sa = classify_psych_sa(service_obj["out_of_network"].get("psych_or_sa", ""))
        service_obj["out_of_network"]["benefit_codes"] = match_benefit(service_name, "OON", psych_sa, benefit_table)

    # Flat service objects (no in/out split) — e.g. ALOC, other_services
    if "in_network" not in service_obj and "out_of_network" not in service_obj:
        psych_sa = classify_psych_sa(service_obj.get("psych_or_sa", service_obj.get("psych_or_SA", "")))
        service_obj["benefit_codes"] = match_benefit(service_name, None, psych_sa, benefit_table)


def process_json(data, benefit_table):
    result = deepcopy(data)

    # Sections with lists of service objects that have in_network / out_of_network splits
    service_list_paths = [
        ("outpatient_benefits", "outpatient_therapies"),
        ("inpatient_benefits", "inpatient_facility"),
        ("inpatient_benefits", "inpatient_professional_services"),
        ("inpatient_benefits", "alternative_levels_of_care"),
        ("other_benefits", "emergency_services"),
        ("other_benefits", "other_outpatient_professional"),
        ("other_benefits", "other_services"),
    ]

    for section_key, list_key in service_list_paths:
        services = result.get(section_key, {}).get(list_key, [])
        if isinstance(services, list):
            for svc in services:
                if isinstance(svc, dict) and "service" in svc:
                    add_codes_to_service(svc, benefit_table)

    # ECT — nested lists under in_network / out_of_network
    ect = result.get("other_benefits", {}).get("ECT", {})
    for net_key in ["in_network", "out_of_network"]:
        net_type = "OON" if "out" in net_key else "INN"
        for svc in ect.get(net_key, []):
            if isinstance(svc, dict) and "service" in svc:
                psych_sa = classify_psych_sa(svc.get("psych_or_sa", ""))
                svc["benefit_codes"] = match_benefit(svc["service"], net_type, psych_sa, benefit_table)

    # ABA — nested in_network / out_of_network dicts
    aba = result.get("outpatient_benefits", {}).get("ABA", {})
    if isinstance(aba, dict):
        for net_key, net_type in [("in_network", "INN"), ("out_of_network", "OON")]:
            if net_key in aba and isinstance(aba[net_key], dict):
                aba[net_key]["benefit_codes"] = match_benefit("ABA", net_type, "BOTH", benefit_table)

    return result


def main(json_input, xlsx_path="RF1MAS.xlsx"):
    if isinstance(json_input, str):
        json_input = json.loads(json_input)
    benefit_table = load_benefit_table(xlsx_path)
    return process_json(json_input, benefit_table)


if __name__ == "__main__":
    JSON_PATH = '/Users/anujsingh6/projects/Claude/benagent/file.json'
    XLSX_PATH = "/Users/anujsingh6/projects/Claude/benagent/RF1MAS.xlsx"

    with open(JSON_PATH, 'r') as f:
        SAMPLE_JSON = json.load(f)

    result = main(SAMPLE_JSON, XLSX_PATH)

    # Write benefit_codes back into the same JSON file
    with open(JSON_PATH, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"✅ benefit_codes added and saved to: {JSON_PATH}")

    # Print summary of all benefit_codes added
    def print_codes(obj, label=""):
        if isinstance(obj, dict):
            if "benefit_codes" in obj:
                print(f"  [{label}] benefit_codes: {obj['benefit_codes']}")
            for k, v in obj.items():
                if k != "benefit_codes":
                    print_codes(v, label=f"{label}.{k}" if label else k)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    name = item.get("service", label)
                    print_codes(item, label=name)

    print_codes(result)