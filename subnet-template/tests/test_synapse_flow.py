import importlib.util
import pathlib
import sys
import types
import unittest
from types import SimpleNamespace


ROOT = pathlib.Path(__file__).resolve().parents[1]


def install_dependency_stubs():
    if "pydantic" not in sys.modules:
        pydantic_module = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                annotations = {}
                for cls in reversed(type(self).__mro__):
                    annotations.update(getattr(cls, "__annotations__", {}))

                for field_name in annotations:
                    if field_name in kwargs:
                        value = kwargs[field_name]
                    elif hasattr(type(self), field_name):
                        value = getattr(type(self), field_name)
                    else:
                        value = None
                    setattr(self, field_name, value)

            def model_dump(self, exclude_none=False):
                data = dict(self.__dict__)
                if exclude_none:
                    data = {
                        key: value for key, value in data.items() if value is not None
                    }
                return data

        pydantic_module.BaseModel = BaseModel
        sys.modules["pydantic"] = pydantic_module

    if "bittensor" not in sys.modules:
        bittensor_module = types.ModuleType("bittensor")

        class Synapse:
            dendrite = SimpleNamespace(hotkey="recognized-hotkey")

            def __init__(self, **kwargs):
                annotations = {}
                for cls in reversed(type(self).__mro__):
                    annotations.update(getattr(cls, "__annotations__", {}))

                for field_name in annotations:
                    if field_name in kwargs:
                        value = kwargs[field_name]
                    elif hasattr(type(self), field_name):
                        value = getattr(type(self), field_name)
                    else:
                        value = None
                    setattr(self, field_name, value)

                self.dendrite = kwargs.get(
                    "dendrite", SimpleNamespace(hotkey="recognized-hotkey")
                )

            def model_dump(self, exclude_none=False):
                data = {
                    key: value
                    for key, value in self.__dict__.items()
                    if key != "dendrite"
                }
                if exclude_none:
                    data = {
                        key: value for key, value in data.items() if value is not None
                    }
                return data

        class Placeholder:
            @staticmethod
            def add_args(_parser):
                return None

            def __init__(self, *args, **kwargs):
                pass

        class Config:
            def __init__(self, parser):
                self._parser = parser

        bittensor_module.Synapse = Synapse
        bittensor_module.Subtensor = Placeholder
        bittensor_module.Wallet = Placeholder
        bittensor_module.Config = Config
        bittensor_module.Dendrite = Placeholder
        bittensor_module.Axon = Placeholder
        sys.modules["bittensor"] = bittensor_module

        btlogging_module = types.ModuleType("bittensor.utils.btlogging")

        class LoggingStub:
            @staticmethod
            def add_args(_parser):
                return None

            def __call__(self, *args, **kwargs):
                return None

            def __getattr__(self, _name):
                return lambda *args, **kwargs: None

        btlogging_module.logging = LoggingStub()
        sys.modules["bittensor.utils.btlogging"] = btlogging_module
        bittensor_utils_module = types.ModuleType("bittensor.utils")
        sys.modules["bittensor.utils"] = bittensor_utils_module


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


install_dependency_stubs()
protocol = load_module("protocol", ROOT / "protocol.py")
miner_module = load_module("miner_under_test", ROOT / "miner.py")
validator_module = load_module("validator_under_test", ROOT / "validator.py")


class SynapseFlowTests(unittest.TestCase):
    def setUp(self):
        self.miner = miner_module.Miner.__new__(miner_module.Miner)
        self.miner.metagraph = SimpleNamespace(hotkeys=["recognized-hotkey"])

    def test_dummy_flow_returns_doubled_value(self):
        synapse = protocol.Dummy(dummyInput=7)

        response = self.miner.dummy(synapse)

        self.assertEqual(response.dummyOutput, 14)

    def test_metadata_flow_returns_expected_record_catalog(self):
        synapse = protocol.RecordMetadataSynapse(
            patientId="patient-001",
            requestId="request-001",
        )

        response = self.miner.record_metadata(synapse)

        self.assertEqual(len(response.availableRecords), 2)
        self.assertEqual(response.availableRecords[0]["recordId"], "record-001")

    def test_access_request_flow_approves_treatment(self):
        synapse = protocol.AccessRequestSynapse(
            requesterId="requester-001",
            patientId="patient-001",
            recordId="record-001",
            purposeOfUse="treatment",
        )

        response = self.miner.access_request(synapse)

        self.assertTrue(response.accessGranted)
        self.assertEqual(response.contractDecision, "approved")
        self.assertEqual(response.keyReference, "mock-key://patient-001/record-001")

    def test_retrieval_flow_returns_payload_and_digest(self):
        synapse = protocol.MedicalDataRetrievalSynapse(
            requesterId="requester-001",
            patientId="patient-001",
            recordId="record-001",
            chunkRangeStart=0,
            chunkRangeEnd=2,
        )

        response = self.miner.medical_data_retrieval(synapse)

        self.assertEqual(
            response.encryptedPayload,
            "mock-encrypted-payload:record-001:chunks-0-2",
        )
        self.assertEqual(response.digestReference, "digest::record-001")

    def test_integrity_flow_detects_matching_digest(self):
        synapse = protocol.IntegrityVerificationSynapse(
            recordId="record-001",
            providedDigest="digest::record-001",
        )

        response = self.miner.integrity_verification(synapse)

        self.assertEqual(response.blockchainDigest, "digest::record-001")
        self.assertTrue(response.isValid)

    def test_ehr_flow_returns_mock_patient_payload(self):
        synapse = protocol.ElectronicHealthRecordSynapse(patientId="patient-001")

        response = self.miner.electronic_health_record(synapse)

        self.assertEqual(response.patientId, "patient-001")
        self.assertEqual(response.allergies[0].substance, "Penicillin")
        self.assertEqual(response.immunizations[0].name, "Influenza")
        self.assertIn("Hypertension", response.problems)
        self.assertEqual(response.labResults[0]["testName"], "CBC")
        self.assertEqual(response.medications[0].name, "Lisinopril")
        self.assertEqual(response.encounters[0].encounterId, "encounter-001")
        self.assertEqual(response.documents[0].documentType, "clinical-note")
        self.assertEqual(response.observations[0].name, "Blood pressure")

    def test_validator_builds_each_request_type(self):
        validator = validator_module.Validator.__new__(validator_module.Validator)
        request_cases = {
            "dummy": protocol.Dummy,
            "metadata": protocol.RecordMetadataSynapse,
            "access": protocol.AccessRequestSynapse,
            "retrieval": protocol.MedicalDataRetrievalSynapse,
            "integrity": protocol.IntegrityVerificationSynapse,
            "ehr": protocol.ElectronicHealthRecordSynapse,
        }

        for request_type, expected_class in request_cases.items():
            with self.subTest(request_type=request_type):
                validator.config = SimpleNamespace(
                    request_type=request_type,
                    request_id="request-001",
                    requester_id="requester-001",
                    patient_id="patient-001",
                    record_id="record-001",
                    record_type="ehr",
                    purpose_of_use="treatment",
                    date_range_start="2026-01-01",
                    date_range_end="2026-04-01",
                    chunk_range_start=0,
                    chunk_range_end=2,
                    provided_digest="digest::record-001",
                )

                synapse = validator.build_synapse_from_cli()

                self.assertIsInstance(synapse, expected_class)


if __name__ == "__main__":
    unittest.main()
