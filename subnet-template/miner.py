import argparse
import os
import time
import traceback
from typing import Tuple

from bittensor import Subtensor, Wallet, Config, Axon, Synapse
from bittensor.utils.btlogging import logging

from protocol import (
    AccessRequestSynapse,
    Allergy,
    Dummy,
    ElectronicHealthRecordSynapse,
    Immunization,
    IntegrityVerificationSynapse,
    MedicalDataRetrievalSynapse,
    RecordMetadataSynapse,
)


class Miner:
    def __init__(self):
        self.subtensor = None
        self.wallet = None
        self.metagraph = None
        self.axon = None
        self.my_subnet_uid = None

        self.config = self.get_config()
        self.setup_logging()
        self.setup_bittensor_objects()

    def get_config(self):
        # Set up the configuration parser
        parser = argparse.ArgumentParser()
        # TODO: Add your custom miner arguments to the parser.
        parser.add_argument(
            "--custom",
            default="my_custom_value",
            help="Adds a custom value to the parser.",
        )
        # Adds override arguments for network and netuid.
        parser.add_argument(
            "--netuid", type=int, default=1, help="The chain subnet uid."
        )
        # Adds subtensor specific arguments.
        Subtensor.add_args(parser)
        # Adds logging specific arguments.
        logging.add_args(parser)
        # Adds wallet specific arguments.
        Wallet.add_args(parser)
        # Adds axon specific arguments.
        Axon.add_args(parser)
        # Parse the arguments.
        config = Config(parser)
        # Set up logging directory
        config.full_path = os.path.expanduser(
            "{}/{}/{}/netuid{}/{}".format(
                config.logging.logging_dir,
                config.wallet.name,
                config.wallet.hotkey,
                config.netuid,
                "miner",
            )
        )
        # Ensure the directory for logging exists.
        os.makedirs(config.full_path, exist_ok=True)
        return config

    def setup_logging(self):
        # Activate Bittensor's logging with the set configurations.
        logging(config=self.config, logging_dir=self.config.full_path)
        logging.info(
            f"Running miner for subnet: {self.config.netuid} on network: {self.config.subtensor.network} with config:"
        )
        logging.info(self.config)

    def setup_bittensor_objects(self):
        # Initialize Bittensor miner objects
        logging.info("Setting up Bittensor objects.")

        # Initialize wallet.
        self.wallet = Wallet(config=self.config)
        logging.info(f"Wallet: {self.wallet}")

        # Initialize subtensor.
        self.subtensor = Subtensor(config=self.config)
        logging.info(f"Subtensor: {self.subtensor}")

        # Initialize metagraph.
        self.metagraph = self.subtensor.metagraph(netuid=self.config.netuid)
        logging.info(f"Metagraph: {self.metagraph}")

        if self.wallet.hotkey.ss58_address not in self.metagraph.hotkeys:
            logging.error(
                f"\nYour miner: {self.wallet} is not registered to chain connection: {self.subtensor} \nRun 'btcli register' and try again."
            )
            exit()
        else:
            # Each miner gets a unique identity (UID) in the network.
            self.my_subnet_uid = self.metagraph.hotkeys.index(
                self.wallet.hotkey.ss58_address
            )
            logging.info(f"Running miner on uid: {self.my_subnet_uid}")

    def blacklist_fn(self, synapse: Synapse) -> Tuple[bool, str]:
        # Ignore requests from unrecognized entities.
        if synapse.dendrite.hotkey not in self.metagraph.hotkeys:
            logging.trace(f"Blacklisting unrecognized hotkey {synapse.dendrite.hotkey}")
            return True, None
        logging.trace(f"Not blacklisting recognized hotkey {synapse.dendrite.hotkey}")
        return False, None

    def build_mock_ehr(self, patient_id: str) -> ElectronicHealthRecordSynapse:
        return ElectronicHealthRecordSynapse(
            patientId=patient_id,
            allergies=[
                Allergy(
                    substance="Penicillin",
                    reaction="Rash",
                    severity="moderate",
                )
            ],
            immunizations=[
                Immunization(
                    name="Influenza",
                    dateAdministered="2026-01-12",
                    provider="Campus Health Clinic",
                )
            ],
            problems=["Hypertension", "Seasonal allergies"],
            labResults=[
                {
                    "testName": "CBC",
                    "date": "2026-03-21",
                    "status": "final",
                    "summary": "Within normal range",
                }
            ],
        )

    def dummy(self, synapse: Dummy) -> Dummy:
        # Simple logic: return the input value multiplied by 2.
        synapse.dummyOutput = synapse.dummyInput * 2
        logging.info(
            f"Received input: {synapse.dummyInput}, sending output: {synapse.dummyOutput}"
        )
        return synapse

    def record_metadata(self, synapse: RecordMetadataSynapse) -> RecordMetadataSynapse:
        records = [
            {
                "recordId": "record-001",
                "recordType": "ehr",
                "description": "Patient longitudinal health summary",
                "dateRangeStart": "2025-01-01",
                "dateRangeEnd": "2026-04-01",
            },
            {
                "recordId": "record-002",
                "recordType": "lab-results",
                "description": "Recent laboratory panel results",
                "dateRangeStart": "2026-03-01",
                "dateRangeEnd": "2026-03-31",
            },
        ]
        if synapse.recordType is not None:
            records = [
                record
                for record in records
                if record["recordType"] == synapse.recordType
            ]
        synapse.availableRecords = records
        logging.info(
            f"Returning {len(records)} records for patient {synapse.patientId}"
        )
        return synapse

    def access_request(self, synapse: AccessRequestSynapse) -> AccessRequestSynapse:
        approved = synapse.purposeOfUse.lower() in {"treatment", "operations"}
        synapse.accessGranted = approved
        synapse.contractDecision = "approved" if approved else "denied"
        synapse.keyReference = (
            f"mock-key://{synapse.patientId}/{synapse.recordId}"
            if approved
            else None
        )
        logging.info(
            f"Access request for record {synapse.recordId} returned {synapse.contractDecision}"
        )
        return synapse

    def medical_data_retrieval(
        self, synapse: MedicalDataRetrievalSynapse
    ) -> MedicalDataRetrievalSynapse:
        start = synapse.chunkRangeStart if synapse.chunkRangeStart is not None else 0
        end = synapse.chunkRangeEnd if synapse.chunkRangeEnd is not None else start
        synapse.encryptedPayload = (
            f"mock-encrypted-payload:{synapse.recordId}:chunks-{start}-{end}"
        )
        synapse.digestReference = f"digest::{synapse.recordId}"
        logging.info(
            f"Returning retrieval payload for record {synapse.recordId} chunks {start}-{end}"
        )
        return synapse

    def integrity_verification(
        self, synapse: IntegrityVerificationSynapse
    ) -> IntegrityVerificationSynapse:
        synapse.blockchainDigest = f"digest::{synapse.recordId}"
        synapse.isValid = synapse.providedDigest == synapse.blockchainDigest
        logging.info(
            f"Integrity check for {synapse.recordId} valid={synapse.isValid}"
        )
        return synapse

    def electronic_health_record(
        self, synapse: ElectronicHealthRecordSynapse
    ) -> ElectronicHealthRecordSynapse:
        mock_ehr = self.build_mock_ehr(synapse.patientId)
        synapse.allergies = mock_ehr.allergies
        synapse.immunizations = mock_ehr.immunizations
        synapse.problems = mock_ehr.problems
        synapse.labResults = mock_ehr.labResults
        logging.info(f"Returning EHR payload for patient {synapse.patientId}")
        return synapse

    def setup_axon(self):
        # Build and link miner functions to the axon.
        self.axon = Axon(wallet=self.wallet, config=self.config)

        # Attach functions to the axon.
        logging.info("Attaching forward functions to axon.")
        self.axon.attach(
            forward_fn=self.dummy,
            blacklist_fn=self.blacklist_fn,
        )
        self.axon.attach(
            forward_fn=self.record_metadata,
            blacklist_fn=self.blacklist_fn,
        )
        self.axon.attach(
            forward_fn=self.access_request,
            blacklist_fn=self.blacklist_fn,
        )
        self.axon.attach(
            forward_fn=self.medical_data_retrieval,
            blacklist_fn=self.blacklist_fn,
        )
        self.axon.attach(
            forward_fn=self.integrity_verification,
            blacklist_fn=self.blacklist_fn,
        )
        self.axon.attach(
            forward_fn=self.electronic_health_record,
            blacklist_fn=self.blacklist_fn,
        )

        # Serve the axon.
        logging.info(
            f"Serving axon on network: {self.config.subtensor.network} with netuid: {self.config.netuid}"
        )
        self.axon.serve(netuid=self.config.netuid, subtensor=self.subtensor)
        logging.info(f"Axon: {self.axon}")

        # Start the axon server.
        logging.info(f"Starting axon server on port: {self.config.axon.port}")
        self.axon.start()

    def run(self):
        self.setup_axon()

        # Keep the miner alive.
        logging.info("Starting main loop")
        step = 0
        while True:
            try:
                # Periodically update our knowledge of the network graph.
                if step % 60 == 0:
                    self.metagraph.sync()
                    log = (
                        f"Block: {self.metagraph.block.item()} | "
                        f"Incentive: {self.metagraph.I[self.my_subnet_uid]} | "
                    )
                    logging.info(log)
                step += 1
                time.sleep(1)

            except KeyboardInterrupt:
                self.axon.stop()
                logging.success("Miner killed by keyboard interrupt.")
                break
            except Exception:
                logging.error(traceback.format_exc())
                continue


# Run the miner.
if __name__ == "__main__":
    miner = Miner()
    miner.run()
