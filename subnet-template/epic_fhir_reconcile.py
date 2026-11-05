"""Audit resource counts and selected clinical fields against stored EHR data.

This is a targeted mapping audit, not FHIR profile or clinical validation.
The report records source IDs and field names, not the clinical values.
"""

from collections import defaultdict


RESOURCE_FIELDS = {
    "Observation": "observations", "Condition": "problems",
    "MedicationRequest": "medications", "Encounter": "encounters",
    "AllergyIntolerance": "allergies", "Immunization": "immunizations",
    "DiagnosticReport": "labResults", "DocumentReference": "documents",
}


def concept_label(concept):
    if not isinstance(concept, dict):
        return None
    if concept.get("text"):
        return str(concept["text"])
    for coding in concept.get("coding", []):
        if isinstance(coding, dict) and (coding.get("display") or coding.get("code")):
            return coding.get("display") or coding.get("code")
    return None


def observation_fields(resource):
    expected = {"status": resource.get("status"), "effectiveDate": resource.get("effectiveDateTime")}
    expected["name"] = concept_label(resource.get("code")) or "Unknown observation"
    if "valueQuantity" in resource:
        quantity = resource["valueQuantity"]
        expected["value"] = str(quantity["value"]) if quantity.get("value") is not None else None
        expected["unit"] = quantity.get("unit") or quantity.get("code")
    elif "valueString" in resource:
        expected["value"] = resource["valueString"]
    elif "valueCodeableConcept" in resource:
        expected["value"] = concept_label(resource["valueCodeableConcept"])
    elif "valueBoolean" in resource:
        expected["value"] = str(resource["valueBoolean"]).lower()
    elif "valueInteger" in resource:
        expected["value"] = str(resource["valueInteger"])
    else:
        pressures = {}
        for component in resource.get("component", []):
            label = (concept_label(component.get("code")) or "").lower()
            codes = {c.get("code") for c in component.get("code", {}).get("coding", [])
                     if c.get("system") == "http://loinc.org"}
            kind = "systolic" if "8480-6" in codes or "systolic" in label else (
                "diastolic" if "8462-4" in codes or "diastolic" in label else None)
            if kind and component.get("valueQuantity", {}).get("value") is not None:
                pressures[kind] = component["valueQuantity"]
        if len(pressures) == 2:
            expected["value"] = f"{pressures['systolic']['value']}/{pressures['diastolic']['value']}"
            expected["unit"] = pressures["systolic"].get("unit") or pressures["systolic"].get("code")
    return expected


def reconcile_bundle(bundle, payload):
    if bundle.get("resourceType") != "Bundle" or not isinstance(bundle.get("entry"), list):
        raise ValueError("Reconciliation requires a Bundle with entries.")
    grouped = defaultdict(list)
    for entry in bundle["entry"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("resource"), dict):
            raise ValueError("Reconciliation encountered an invalid entry.")
        resource = entry["resource"]
        grouped[resource.get("resourceType", "Unknown")].append(resource)
    mismatches, warnings, checks, source_index = [], [], [], []
    patients = grouped["Patient"]
    if len(patients) != 1 or patients[0].get("id") != payload.get("patientId"):
        mismatches.append({"field": "patientId", "reason": "patient_context_mismatch"})

    for resource_type, field in RESOURCE_FIELDS.items():
        resources, normalized = grouped[resource_type], payload.get(field)
        count_matches = isinstance(normalized, list) and len(resources) == len(normalized)
        checks.append({"resourceType": resource_type, "normalizedField": field,
                       "rawCount": len(resources), "normalizedCount": len(normalized) if isinstance(normalized, list) else None,
                       "countsMatch": count_matches})
        if not count_matches:
            mismatches.append({"field": field, "reason": "resource_count_mismatch"})
            continue
        for index, (resource, mapped) in enumerate(zip(resources, normalized)):
            location = {"resourceType": resource_type, "resourceId": resource.get("id"),
                        "normalizedField": field, "index": index}
            source_index.append(location)
            expected = {}
            if resource_type == "Condition":
                if mapped != (concept_label(resource.get("code")) or "Unknown condition"):
                    mismatches.append({**location, "field": "problemText", "reason": "value_mismatch"})
                continue
            if resource_type == "Observation":
                expected = observation_fields(resource)
                has_value = any(key.startswith("value") for key in resource) or bool(resource.get("component"))
                if has_value and "value" not in expected:
                    warnings.append({**location, "reason": "observation_value_shape_not_mapped"})
                if resource.get("effectivePeriod") or resource.get("valueQuantity", {}).get("comparator"):
                    warnings.append({**location, "reason": "period_or_quantity_comparator_preserved_only_in_raw_bundle"})
            elif resource_type == "MedicationRequest":
                name = concept_label(resource.get("medicationCodeableConcept")) or resource.get("medicationReference", {}).get("display")
                expected = {"name": name or "Unknown medication", "status": resource.get("status"),
                            "authoredOn": resource.get("authoredOn")}
                if not name:
                    warnings.append({**location, "reason": "medication_name_unresolved"})
            elif resource_type == "Encounter":
                period, encounter_class = resource.get("period", {}), resource.get("class", {})
                expected = {"encounterId": resource.get("id") or "unknown-encounter", "status": resource.get("status"),
                            "classCode": encounter_class.get("code") or encounter_class.get("display"),
                            "periodStart": period.get("start"), "periodEnd": period.get("end")}
            for key, expected_value in expected.items():
                if not isinstance(mapped, dict) or mapped.get(key) != expected_value:
                    mismatches.append({**location, "field": key, "reason": "value_mismatch"})

    for resource_type, resources in grouped.items():
        if resources and resource_type not in RESOURCE_FIELDS and resource_type != "Patient":
            warnings.append({"resourceType": resource_type, "reason": "resource_not_normalized", "count": len(resources)})
    return {
        "status": "failed" if mismatches else ("passed_with_warnings" if warnings else "passed"),
        "countChecks": checks, "sourceIndex": source_index, "mismatches": mismatches, "warnings": warnings,
        "coverage": "Counts for all mapped resource types; selected values for Patient, Observation, Condition, MedicationRequest, and Encounter.",
        "limitations": ["Patient demographics remain in the raw Bundle.",
                        "The normalized summary omits many FHIR fields; retain the raw Bundle.",
                        "This audit does not validate clinical meaning or FHIR profiles."],
    }
