import typing
from dataclasses import dataclass, field

from pydantic import BaseModel

try:
    from bittensor import Synapse
except ImportError:  # Bittensor v11+ no longer ships Synapse
    class Synapse:  # type: ignore[no-redef]
        """Minimal stand-in so legacy typed synapses still import on v11."""

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)


class Immunization(BaseModel):
    "Immunization model for EHR payload testing."

    name: str
    dateAdministered: str
    provider: str


class Allergy(BaseModel):
    "Allergy model for EHR payload testing."

    substance: str
    reaction: str
    severity: str


class Medication(BaseModel):
    "Medication model for EHR payload testing and Epic FHIR ingestion."

    name: str
    status: str | None = None
    authoredOn: str | None = None
    requester: str | None = None


class Encounter(BaseModel):
    "Encounter model for visit context imported from EHR systems."

    encounterId: str
    status: str | None = None
    classCode: str | None = None
    periodStart: str | None = None
    periodEnd: str | None = None


class ClinicalDocument(BaseModel):
    "Clinical document metadata used for EHR record discovery and retrieval."

    documentId: str
    documentType: str
    title: str
    date: str | None = None
    contentReference: str | None = None


class ObservationResult(BaseModel):
    "Observation or diagnostic result normalized from EHR systems."

    name: str
    value: str | None = None
    unit: str | None = None
    effectiveDate: str | None = None
    status: str | None = None


class RecordMetadataSynapse(Synapse):
    "Discovers available medical records and returns metadata before a requester asks for access to a specific record."

    patientId: str
    requestId: str
    recordType: str | None = None
    availableRecords: list[dict[str, typing.Any]] | None = None


class AccessRequestSynapse(Synapse):
    "Captures an on-chain access request and returns the contract decision together with any key reference needed for retrieval."

    requesterId: str
    patientId: str
    recordId: str
    purposeOfUse: str
    dateRangeStart: str | None = None
    dateRangeEnd: str | None = None
    accessGranted: bool | None = None
    contractDecision: str | None = None
    keyReference: str | None = None


class MedicalDataRetrievalSynapse(Synapse):
    "Requests encrypted medical record content after access has been evaluated. Supports partial retrieval with chunks."

    requesterId: str
    patientId: str
    recordId: str
    chunkRangeStart: int | None = None
    chunkRangeEnd: int | None = None
    encryptedPayload: str | None = None
    digestReference: str | None = None


class IntegrityVerificationSynapse(Synapse):
    "Checks whether a provided digest matches the blockchain record."

    recordId: str
    providedDigest: str
    blockchainDigest: str | None = None
    isValid: bool | None = None


class ElectronicHealthRecordSynapse(Synapse):
    "Represents a patient EHR payload used for testing flows and record modeling."

    patientId: str
    allergies: list[Allergy] | None = None
    immunizations: list[Immunization] | None = None
    problems: list[str] | None = None
    labResults: list[dict[str, typing.Any]] | None = None
    medications: list[Medication] | None = None
    encounters: list[Encounter] | None = None
    documents: list[ClinicalDocument] | None = None
    observations: list[ObservationResult] | None = None


class Dummy(Synapse):
    """This protocol handles dummy request and response communication between
    the miner and the validator.

    Attributes:
    - dummyInput: An integer value representing the input request sent by the validator.
    - dummyOutput: An optional integer value representing the response from the miner.
    """

    dummyInput: int

    dummyOutput: typing.Optional[int] = None


@dataclass
class MessageSynapse:
    """Generic envelope: arbitrary fields travel in `payload`.

    Used by validatorv1/minerv1 — Bittensor v11 no longer ships Synapse/Axon/Dendrite.
    """

    payload: dict[str, typing.Any] = field(default_factory=dict)
