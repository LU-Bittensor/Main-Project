import typing

from bittensor import Synapse
from pydantic import BaseModel


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


class Dummy(Synapse):
    """This protocol handles dummy request and response communication between
    the miner and the validator.

    Attributes:
    - dummyInput: An integer value representing the input request sent by the validator.
    - dummyOutput: An optional integer value representing the response from the miner.
    """

    dummyInput: int

    dummyOutput: typing.Optional[int] = None
