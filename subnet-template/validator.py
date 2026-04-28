import argparse
import os
import random
import time
import traceback
from typing import Any

from bittensor import Subtensor, Wallet, Config, Dendrite
from bittensor.utils.btlogging import logging

from protocol import (
    AccessRequestSynapse,
    Dummy,
    ElectronicHealthRecordSynapse,
    IntegrityVerificationSynapse,
    MedicalDataRetrievalSynapse,
    RecordMetadataSynapse,
)

REQUEST_TYPE_CHOICES = (
    "dummy",
    "metadata",
    "access",
    "retrieval",
    "integrity",
    "ehr",
)


class Validator:
    def __init__(self):
        self.config = self.get_config()
        self.setup_logging()
        self.setup_bittensor_objects()
        self.my_uid = self.metagraph.hotkeys.index(self.wallet.hotkey.ss58_address)
        self.scores = [0] * len(self.metagraph.S)
        self.last_update = self.subtensor.blocks_since_last_update(
            self.config.netuid, self.my_uid
        )
        self.tempo = self.subtensor.tempo(self.config.netuid)
        self.moving_avg_scores = [0] * len(self.metagraph.S)
        self.alpha = 0.1

    def get_config(self):
        # Set up the configuration parser.
        parser = argparse.ArgumentParser()
        # TODO: Add your custom validator arguments to the parser.
        parser.add_argument(
            "--custom",
            default="my_custom_value",
            help="Adds a custom value to the parser.",
        )
        # Adds override arguments for network and netuid.
        parser.add_argument(
            "--netuid", type=int, default=1, help="The chain subnet uid."
        )
        parser.add_argument(
            "--request-type",
            choices=REQUEST_TYPE_CHOICES,
            default="dummy",
            help="Synapse request type to send to miners.",
        )
        parser.add_argument("--request-id", type=str, default="request-001")
        parser.add_argument("--requester-id", type=str, default="requester-001")
        parser.add_argument("--patient-id", type=str, default="patient-001")
        parser.add_argument("--record-id", type=str, default="record-001")
        parser.add_argument(
            "--record-type",
            type=str,
            default=None,
            help="Optional record category filter for metadata discovery.",
        )
        parser.add_argument(
            "--purpose-of-use",
            type=str,
            default="treatment",
            help="Purpose of use for access request testing.",
        )
        parser.add_argument(
            "--date-range-start",
            type=str,
            default=None,
            help="Optional ISO-style start date for access requests.",
        )
        parser.add_argument(
            "--date-range-end",
            type=str,
            default=None,
            help="Optional ISO-style end date for access requests.",
        )
        parser.add_argument(
            "--chunk-range-start",
            type=int,
            default=None,
            help="Optional starting chunk index for retrieval requests.",
        )
        parser.add_argument(
            "--chunk-range-end",
            type=int,
            default=None,
            help="Optional ending chunk index for retrieval requests.",
        )
        parser.add_argument(
            "--provided-digest",
            type=str,
            default="digest-001",
            help="Digest value to validate during integrity checks.",
        )
        # Adds subtensor specific arguments.
        Subtensor.add_args(parser)
        # Adds logging specific arguments.
        logging.add_args(parser)
        # Adds wallet specific arguments.
        Wallet.add_args(parser)
        # Parse the config.
        config = Config(parser)
        # Set up logging directory.
        config.full_path = os.path.expanduser(
            "{}/{}/{}/netuid{}/validator".format(
                config.logging.logging_dir,
                config.wallet.name,
                config.wallet.hotkey,
                config.netuid,
            )
        )
        # Ensure the logging directory exists.
        os.makedirs(config.full_path, exist_ok=True)
        return config

    def setup_logging(self):
        # Set up logging.
        logging(config=self.config, logging_dir=self.config.full_path)
        logging.info(
            f"Running validator for subnet: {self.config.netuid} on network: {self.config.subtensor.network} with config:"
        )
        logging.info(self.config)

    def setup_bittensor_objects(self):
        # Build Bittensor validator objects.
        logging.info("Setting up Bittensor objects.")

        # Initialize wallet.
        self.wallet = Wallet(config=self.config)
        logging.info(f"Wallet: {self.wallet}")

        # Initialize subtensor.
        self.subtensor = Subtensor(config=self.config)
        logging.info(f"Subtensor: {self.subtensor}")

        # Initialize dendrite.
        self.dendrite = Dendrite(wallet=self.wallet)
        logging.info(f"Dendrite: {self.dendrite}")

        # Initialize metagraph.
        self.metagraph = self.subtensor.metagraph(netuid=self.config.netuid)
        logging.info(f"Metagraph: {self.metagraph}")

        # Connect the validator to the network.
        if self.wallet.hotkey.ss58_address not in self.metagraph.hotkeys:
            logging.error(
                f"Your validator: {self.wallet} is not registered to chain connection: {self.subtensor} \nRun 'btcli register' and try again."
            )
            exit()
        else:
            # Each validator gets a unique identity (UID) in the network.
            self.my_subnet_uid = self.metagraph.hotkeys.index(
                self.wallet.hotkey.ss58_address
            )
            logging.info(f"Running validator on uid: {self.my_subnet_uid}")

        # Set up initial scoring weights for validation.
        logging.info("Building validation weights.")
        self.scores = [0] * len(self.metagraph.S)
        weights_with_uids = [(int(self.metagraph.uids[i]), score) for i, score in enumerate(self.scores)]
        logging.info(f"Weights (uid, weight): {weights_with_uids}")

    def build_synapse_from_cli(self):
        if self.config.request_type == "dummy":
            return Dummy(dummyInput=random.randint(0, 100))

        if self.config.request_type == "metadata":
            return RecordMetadataSynapse(
                patientId=self.config.patient_id,
                requestId=self.config.request_id,
                recordType=self.config.record_type,
            )

        if self.config.request_type == "access":
            return AccessRequestSynapse(
                requesterId=self.config.requester_id,
                patientId=self.config.patient_id,
                recordId=self.config.record_id,
                purposeOfUse=self.config.purpose_of_use,
                dateRangeStart=self.config.date_range_start,
                dateRangeEnd=self.config.date_range_end,
            )

        if self.config.request_type == "retrieval":
            return MedicalDataRetrievalSynapse(
                requesterId=self.config.requester_id,
                patientId=self.config.patient_id,
                recordId=self.config.record_id,
                chunkRangeStart=self.config.chunk_range_start,
                chunkRangeEnd=self.config.chunk_range_end,
            )

        if self.config.request_type == "integrity":
            return IntegrityVerificationSynapse(
                recordId=self.config.record_id,
                providedDigest=self.config.provided_digest,
            )

        if self.config.request_type == "ehr":
            return ElectronicHealthRecordSynapse(
                patientId=self.config.patient_id,
            )

        raise ValueError(f"Unsupported request type: {self.config.request_type}")

    def serialize_response(self, response: Any) -> Any:
        if response is None:
            return None
        if hasattr(response, "model_dump"):
            return response.model_dump(exclude_none=True)
        return response

    def log_structured_responses(self, responses):
        structured_responses = []
        for i, response in enumerate(responses):
            structured_responses.append(
                {
                    "uid": int(self.metagraph.uids[i]),
                    "response": self.serialize_response(response),
                }
            )
        logging.info(
            f"Received {self.config.request_type} responses: {structured_responses}"
        )

    def run(self):
        # The Main Validation Loop.
        logging.info("Starting validator loop.")
        while True:
            try:
                # time.sleep(int(self.subtensor.tempo(self.config.netuid) * 0.25))
                # Build a synapse from the configured CLI request shape.
                synapse = self.build_synapse_from_cli()

                # Broadcast a query to all miners on the network.
                responses = self.dendrite.query(
                    axons=self.metagraph.axons, synapse=synapse, timeout=12
                )
                logging.info(
                    f"Sending {self.config.request_type} synapse: {synapse.model_dump()}"
                )

                if self.config.request_type != "dummy":
                    self.log_structured_responses(responses)
                    time.sleep(5)
                    continue
                
                # Log the results with UIDs showing input and output
                responses_with_uids = []
                for i, response in enumerate(responses):
                    uid = int(self.metagraph.uids[i])
                    if response is not None and response.dummyOutput is not None:
                        responses_with_uids.append({
                            'uid': uid,
                            'input': response.dummyInput,
                            'output': response.dummyOutput
                        })
                    else:
                        responses_with_uids.append({
                            'uid': uid,
                            'input': synapse.dummyInput,
                            'output': None
                        })
                logging.info(f"Received responses: {responses_with_uids}")

                # Store original responses for scoring before filtering
                original_responses = responses
                if responses:
                    responses = [
                        response.dummyOutput
                        for response in responses
                        if response is not None
                    ]

                # Log the results.
                logging.info(f"Received dummy responses: {responses}")

                # Adjust the length of moving_avg_scores to match the number of miners
                if len(self.moving_avg_scores) < len(self.metagraph.S):
                    self.moving_avg_scores.extend(
                        [0] * (len(self.metagraph.S) - len(self.moving_avg_scores))
                    )

                # Adjust the scores based on responses from miners and update moving average.
                # Process by original miner index to maintain UID mapping
                for i, response in enumerate(original_responses):
                    if response is not None and response.dummyOutput is not None:
                        current_score = 1 if response.dummyOutput == synapse.dummyInput * 2 else 0
                    else:
                        current_score = 0
                    self.moving_avg_scores[i] = (
                        1 - self.alpha
                    ) * self.moving_avg_scores[i] + self.alpha * current_score

                # Create list of (uid, score) tuples
                scores_with_uids = [(int(self.metagraph.uids[i]), score) for i, score in enumerate(self.moving_avg_scores)]
                logging.info(f"Moving Average Scores (uid, score): {scores_with_uids}")
                self.last_update = self.subtensor.blocks_since_last_update(
                    self.config.netuid, self.my_uid
                )

                # set weights once every tempo
                total = sum(self.moving_avg_scores)
                if total > 0:
                    weights = [score / total for score in self.moving_avg_scores]
                else:
                    # If no miners responded, set zero weights
                    weights = [0.0] * len(self.moving_avg_scores)
                # Create list of (uid, weight) tuples
                weights_with_uids = [(int(self.metagraph.uids[i]), weight) for i, weight in enumerate(weights)]
                logging.info(f"[blue]Setting weights (uid, weight): {weights_with_uids}[/blue]")
                # Update the incentive mechanism on the Bittensor blockchain.
                response = self.subtensor.set_weights(
                    wallet=self.wallet,
                    netuid=self.config.netuid,
                    uids=self.metagraph.uids,
                    weights=weights,
                    wait_for_inclusion=True,
                    period=self.tempo,  # Good for fast blocks - otherwise make sure to set proper period or remove this argument completely
                )
                if response.success:
                    logging.success(
                        f"Weights set successfully. Fee: {response.extrinsic_fee}"
                    )
                else:
                    logging.error(
                        f"Failed to set weights: {response.error} - {response.message}"
                    )
                self.metagraph.sync()
                # sleep until next tempo
                time.sleep(
                    (((self.subtensor.block // self.tempo) + 1) * self.tempo) + 1
                )

            except RuntimeError as e:
                logging.error(e)
                traceback.print_exc()

            except KeyboardInterrupt:
                logging.success("Keyboard interrupt detected. Exiting validator.")
                exit()


# Run the validator.
if __name__ == "__main__":
    validator = Validator()
    validator.run()
