import argparse
import json
from pathlib import Path
from typing import Any


def load_fhir_bundle(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fixture_file:
        bundle = json.load(fixture_file)

    if bundle.get("resourceType") != "Bundle":
        raise ValueError("Expected a FHIR Bundle resource.")
    if not isinstance(bundle.get("entry"), list):
        raise ValueError("Expected FHIR Bundle.entry to be a list.")

    return bundle


def iter_bundle_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        entry["resource"]
        for entry in bundle.get("entry", [])
        if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)
    ]


def _coding_text(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict):
        return None

    if value.get("text"):
        return str(value["text"])

    coding = value.get("coding")
    if isinstance(coding, list):
        for item in coding:
            if isinstance(item, dict) and (item.get("display") or item.get("code")):
                return item.get("display") or item.get("code")

    return None


def _reference_display(value: dict[str, Any] | None) -> str | None:
    if not isinstance(value, dict):
        return None

    return value.get("display") or value.get("reference")


def _first(items: list[Any] | None) -> Any | None:
    if isinstance(items, list) and items:
        return items[0]
    return None


def map_allergy(resource: dict[str, Any]) -> dict[str, str]:
    first_reaction = _first(resource.get("reaction")) or {}
    first_manifestation = _first(first_reaction.get("manifestation")) or {}

    return {
        "substance": _coding_text(resource.get("code")) or "Unknown allergy",
        "reaction": _coding_text(first_manifestation) or "Unknown reaction",
        "severity": first_reaction.get("severity") or "unknown",
    }


def map_immunization(resource: dict[str, Any]) -> dict[str, str]:
    performer = _first(resource.get("performer")) or {}

    return {
        "name": _coding_text(resource.get("vaccineCode")) or "Unknown immunization",
        "dateAdministered": resource.get("occurrenceDateTime") or "",
        "provider": _reference_display(performer.get("actor")) or "",
    }


def map_condition(resource: dict[str, Any]) -> str:
    return _coding_text(resource.get("code")) or "Unknown condition"


def _quantity_value(quantity: dict[str, Any]) -> tuple[str | None, str | None]:
    value = quantity.get("value")
    unit = quantity.get("unit") or quantity.get("code")
    if value is None:
        return None, unit

    return str(value), unit


def _blood_pressure_value(resource: dict[str, Any]) -> tuple[str | None, str | None]:
    systolic = None
    diastolic = None
    unit = None

    for component in resource.get("component", []):
        code = (_coding_text(component.get("code")) or "").lower()
        loinc_codes = {coding.get("code") for coding in component.get("code", {}).get("coding", [])
                       if isinstance(coding, dict) and coding.get("system") == "http://loinc.org"}
        quantity = component.get("valueQuantity") or {}
        value, component_unit = _quantity_value(quantity)
        unit = unit or component_unit

        if "8480-6" in loinc_codes or "systolic" in code:
            systolic = value
        elif "8462-4" in loinc_codes or "diastolic" in code:
            diastolic = value

    if systolic and diastolic:
        return f"{systolic}/{diastolic}", unit

    return None, unit


def map_observation(resource: dict[str, Any]) -> dict[str, str | None]:
    value = None
    unit = None

    if "valueQuantity" in resource:
        value, unit = _quantity_value(resource["valueQuantity"])
    elif "valueString" in resource:
        value = resource["valueString"]
    elif "valueCodeableConcept" in resource:
        value = _coding_text(resource["valueCodeableConcept"])
    elif "valueBoolean" in resource:
        value = str(resource["valueBoolean"]).lower()
    elif "valueInteger" in resource:
        value = str(resource["valueInteger"])
    elif resource.get("component"):
        value, unit = _blood_pressure_value(resource)

    return {
        "name": _coding_text(resource.get("code")) or "Unknown observation",
        "value": value,
        "unit": unit,
        "effectiveDate": resource.get("effectiveDateTime"),
        "status": resource.get("status"),
    }


def map_diagnostic_report(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "testName": _coding_text(resource.get("code")) or "Unknown diagnostic report",
        "date": resource.get("effectiveDateTime"),
        "status": resource.get("status"),
        "summary": resource.get("conclusion"),
        "resultReferences": [
            result.get("reference")
            for result in resource.get("result", [])
            if isinstance(result, dict) and result.get("reference")
        ],
    }


def map_medication_request(resource: dict[str, Any]) -> dict[str, str | None]:
    return {
        "name": (
            _coding_text(resource.get("medicationCodeableConcept"))
            or (resource.get("medicationReference") or {}).get("display")
            or "Unknown medication"
        ),
        "status": resource.get("status"),
        "authoredOn": resource.get("authoredOn"),
        "requester": _reference_display(resource.get("requester")),
    }


def map_encounter(resource: dict[str, Any]) -> dict[str, str | None]:
    encounter_class = resource.get("class") or {}
    period = resource.get("period") or {}

    return {
        "encounterId": resource.get("id") or "unknown-encounter",
        "status": resource.get("status"),
        "classCode": encounter_class.get("code") or encounter_class.get("display"),
        "periodStart": period.get("start"),
        "periodEnd": period.get("end"),
    }


def map_document_reference(resource: dict[str, Any]) -> dict[str, str | None]:
    first_content = _first(resource.get("content")) or {}
    attachment = first_content.get("attachment") or {}
    document_type = _coding_text(resource.get("type")) or "Unknown document"

    return {
        "documentId": resource.get("id") or "unknown-document",
        "documentType": document_type,
        "title": attachment.get("title") or document_type,
        "date": resource.get("date"),
        "contentReference": attachment.get("url"),
    }


def normalize_bundle_to_ehr_payload(
    bundle: dict[str, Any],
    patient_id: str = "patient-001",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "patientId": patient_id,
        "allergies": [],
        "immunizations": [],
        "problems": [],
        "labResults": [],
        "medications": [],
        "encounters": [],
        "documents": [],
        "observations": [],
    }

    for resource in iter_bundle_resources(bundle):
        resource_type = resource.get("resourceType")

        if resource_type == "AllergyIntolerance":
            payload["allergies"].append(map_allergy(resource))
        elif resource_type == "Immunization":
            payload["immunizations"].append(map_immunization(resource))
        elif resource_type == "Condition":
            payload["problems"].append(map_condition(resource))
        elif resource_type == "Observation":
            payload["observations"].append(map_observation(resource))
        elif resource_type == "DiagnosticReport":
            payload["labResults"].append(map_diagnostic_report(resource))
        elif resource_type == "MedicationRequest":
            payload["medications"].append(map_medication_request(resource))
        elif resource_type == "Encounter":
            payload["encounters"].append(map_encounter(resource))
        elif resource_type == "DocumentReference":
            payload["documents"].append(map_document_reference(resource))

    return payload


def normalize_fixture_file(
    fixture_path: str | Path,
    patient_id: str = "patient-001",
) -> dict[str, Any]:
    bundle = load_fhir_bundle(fixture_path)
    return normalize_bundle_to_ehr_payload(bundle, patient_id=patient_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normalize an Epic-style FHIR Bundle into the project EHR payload."
    )
    parser.add_argument(
        "--fixture",
        required=True,
        help="Path to a FHIR Bundle JSON fixture or exported Epic sandbox bundle.",
    )
    parser.add_argument(
        "--patient-id",
        default="patient-001",
        help="Internal project patient ID to use in the normalized payload.",
    )
    parser.add_argument(
        "--output",
        help="Optional path for the normalized EHR JSON payload. Prints to stdout when omitted.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = normalize_fixture_file(args.fixture, patient_id=args.patient_id)
    output = json.dumps(payload, indent=2, sort_keys=True)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
