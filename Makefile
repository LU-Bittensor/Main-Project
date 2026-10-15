# =============================================================================
# Bittensor Local Subtensor Chain - Full Setup Makefile (btcli / SDK v11)
# =============================================================================
# Usage:
#   make all            - Run the full setup from scratch
#   make help           - Show all available targets
#   make clean          - Stop the chain and remove the container
#
# Prerequisites:
#   - Docker installed and running
#   - bittensor >= 11 (SDK + btcli in one package)
#   - Create a .env file that has your wallet passwords (if necessary)
#
# Activate the venv that has bittensor 11 before running, or set BT_VENV.
# =============================================================================

# Prefer a sibling bt_venv (YumaBittensor/bt_venv) if present
BT_VENV ?= $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../bt_venv)
ifneq ($(wildcard $(BT_VENV)/bin/btcli),)
export PATH := $(BT_VENV)/bin:$(PATH)
endif

# --- Configuration -----------------------------------------------------------

# RaoFoundation localnet image (replaces ghcr.io/opentensor/subtensor-localnet:devnet-ready)
DOCKER_IMAGE   := ghcr.io/raofoundation/subtensor-localnet:devnet
CONTAINER_NAME := local_chain
RPC_PORT       := 9944
WS_PORT        := 9945
# `local` resolves to ws://127.0.0.1:9944
NETWORK        := local

SUBNET_NAME    := medchain-first-subnet

# Wallet names
ALICE_COLD     := alice-cold
ALICE_HOT      := alice-hot
CREATOR_WALLET := sn-creator
VALIDATOR_WALLET := test-validator
MINER_WALLET   := test-miner

# Well-known Substrate //Alice seed (public; localnet genesis only)
ALICE_SEED     := 0xe5be9a5092b81bca64be81d212e7f2f9eba183bb7a90954f7b76361f6edb5c0a

# Transfer amounts (TAO)
CREATOR_AMOUNT  := 1001
VALIDATOR_AMOUNT := 1000
MINER_AMOUNT    := 1000
STAKE_AMOUNT    := 50

# Timing (seconds)
CHAIN_STARTUP_WAIT   := 15   # Wait for chain to start accepting connections
REGISTRATION_WAIT    := 6    # Wait between neuron registrations
POST_STAKE_WAIT      := 12   # Wait after staking for permit eligibility
EMISSION_START_WAIT  := 6    # Wait after starting emissions

# Fast blocks mode: set to empty string "" to use normal 12s blocks
FAST_BLOCKS := true

# MEV shield flag (localnet / fast-blocks typically submit in the clear)
MEV_FLAG := --no-mev-shield

# Set NETUID
NETUID = 2

# =============================================================================
# Phony commands to run
# =============================================================================
.PHONY: all help check-btcli \
        pull start-chain wait-for-chain stop-chain clean \
        create-alice-wallet create-creator-wallet create-validator-wallet create-miner-wallet \
        create-wallets fund-wallets verify-balances \
        create-subnet start-emissions disable-commit-reveal \
        register-neurons stake-validator verify-setup \
        logs status metagraph \
        deploy-registry validator-live

# =============================================================================
# Top-level targets
# =============================================================================

## all: Run the complete setup end-to-end
all: check-btcli pull start-chain wait-for-chain \
     create-wallets \
     fund-wallets verify-balances \
     create-subnet start-emissions disable-commit-reveal \
     register-neurons stake-validator \
     verify-setup
	@echo ""
	@echo "============================================================"
	@echo "  ✅  Local Bittensor subnet is fully configured!"
	@echo "============================================================"
	@echo "  Chain:     $(NETWORK)  (ws://127.0.0.1:$(RPC_PORT))"
	@echo "  Subnet:    $(SUBNET_NAME) (netuid $(NETUID))"
	@echo "  Wallets:   $(CREATOR_WALLET), $(VALIDATOR_WALLET), $(MINER_WALLET)"
	@echo "============================================================"
	@echo "  To view subnet:    btcli subnets list -n $(NETWORK)"
	@echo "  To view metagraph: btcli subnets metagraph $(NETUID) -n $(NETWORK)"
	@echo "  To stop chain:     make clean"
	@echo "============================================================"

## help: Show this help message
help:
	@echo "Bittensor Local Chain Setup (bittensor / btcli v11)"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /'

## check-btcli: Verify btcli from bittensor >= 11 is on PATH
check-btcli:
	@command -v btcli >/dev/null 2>&1 || { \
		echo "❌ btcli not found. Install with:"; \
		echo "    pip uninstall -y bittensor-cli bittensor-wallet"; \
		echo "    pip install -U 'bittensor>=11'"; \
		echo "    (or: source $(BT_VENV)/bin/activate)"; \
		exit 1; \
	}
	@python3 -c "import shutil,subprocess,sys; \
v=subprocess.check_output(['btcli','--version'], text=True, stderr=subprocess.STDOUT); \
print('    btcli', v.strip().splitlines()[-1])"

# =============================================================================
# Docker / Chain management
# =============================================================================

## pull: Pull the Subtensor localnet Docker image
pull:
	@echo ">>> Pulling Subtensor localnet Docker image..."
	docker pull $(DOCKER_IMAGE)

## start-chain: Start the local Subtensor chain in fast-blocks mode (background)
start-chain:
	@echo ">>> Starting local Subtensor chain (fast-blocks=$(FAST_BLOCKS))..."
	@if docker ps -q --filter "name=$(CONTAINER_NAME)" | grep -q .; then \
		echo "    Container '$(CONTAINER_NAME)' is already running — skipping."; \
	else \
		if [ "$(FAST_BLOCKS)" = "true" ]; then \
			docker run -d --rm --name $(CONTAINER_NAME) \
				-p $(RPC_PORT):$(RPC_PORT) \
				-p $(WS_PORT):$(WS_PORT) \
				$(DOCKER_IMAGE); \
		else \
			docker run -d --rm --name $(CONTAINER_NAME) \
				-p $(RPC_PORT):$(RPC_PORT) \
				-p $(WS_PORT):$(WS_PORT) \
				$(DOCKER_IMAGE) False; \
		fi; \
		echo "    Container started."; \
	fi

## wait-for-chain: Wait for the chain RPC to become available
wait-for-chain:
	@echo ">>> Waiting $(CHAIN_STARTUP_WAIT)s for chain to initialise..."
	@sleep $(CHAIN_STARTUP_WAIT)
	@echo ">>> Verifying chain is reachable (btcli query is-fast-blocks)..."
	@btcli query is-fast-blocks -n $(NETWORK) --json > /dev/null 2>&1 && \
		echo "    ✅ Chain is up." || \
		(echo "    ⚠️  Chain not yet ready — waiting another 15s..." && sleep 15 && \
		 btcli query is-fast-blocks -n $(NETWORK) --json > /dev/null 2>&1 && echo "    ✅ Chain is up." || \
		 (echo "    ⚠️  Still waiting — retrying once more (15s)..." && sleep 15 && \
		  btcli query is-fast-blocks -n $(NETWORK) --json > /dev/null 2>&1 && echo "    ✅ Chain is up." || \
		  (echo "    ❌ Chain failed to start. Check: docker logs $(CONTAINER_NAME)" && exit 1)))

## stop-chain: Stop the running chain container
stop-chain:
	@echo ">>> Stopping chain container '$(CONTAINER_NAME)'..."
	@docker stop $(CONTAINER_NAME) 2>/dev/null || echo "    Container not running."

## clean: Stop chain and remove any leftover containers
clean: stop-chain
	@echo ">>> Cleanup complete."

# =============================================================================
# Wallet creation
# =============================================================================

## create-alice-wallet: Materialize the pre-funded Alice localnet account
create-alice-wallet:
	@echo ">>> Creating Alice wallet (coldkey=$(ALICE_COLD), hotkey=$(ALICE_HOT))..."
	@btcli wallet regen-coldkey \
		-w $(ALICE_COLD) \
		--seed $(ALICE_SEED) \
		--no-password 2>/dev/null || true
	@btcli wallet new-hotkey \
		-w $(ALICE_COLD) \
		-H $(ALICE_HOT) \
		--quiet 2>/dev/null || true
	@echo "    Alice wallet ready."

## create-creator-wallet: Create the subnet creator wallet
create-creator-wallet:
	@echo ">>> Creating subnet creator wallet ($(CREATOR_WALLET))..."
	@btcli wallet create \
		-w $(CREATOR_WALLET) \
		-H default \
		--no-password 2>/dev/null || true

## create-validator-wallet: Create the validator wallet
create-validator-wallet:
	@echo ">>> Creating validator wallet ($(VALIDATOR_WALLET))..."
	@btcli wallet create \
		-w $(VALIDATOR_WALLET) \
		-H default \
		--no-password 2>/dev/null || true

## create-miner-wallet: Create the miner wallet
create-miner-wallet:
	@echo ">>> Creating miner wallet ($(MINER_WALLET))..."
	@btcli wallet create \
		-w $(MINER_WALLET) \
		-H default \
		--no-password 2>/dev/null || true

## create-wallets: Create all wallets (Alice + creator + validator + miner)
create-wallets: create-alice-wallet create-creator-wallet create-validator-wallet create-miner-wallet
	@echo ">>> All wallets created."
	@echo ">>> Listing wallets and addresses..."
	@btcli wallet list

# =============================================================================
# Funding
# =============================================================================

## fund-wallets: Transfer TAO from Alice to each role wallet
fund-wallets:
	@echo ">>> Transferring $(CREATOR_AMOUNT) TAO → subnet creator..."
	@btcli wallet transfer \
		-w $(ALICE_COLD) \
		--dest $(CREATOR_WALLET) \
		--amount-tao $(CREATOR_AMOUNT) \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y

	@echo ">>> Transferring $(VALIDATOR_AMOUNT) TAO → validator..."
	@btcli wallet transfer \
		-w $(ALICE_COLD) \
		--dest $(VALIDATOR_WALLET) \
		--amount-tao $(VALIDATOR_AMOUNT) \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y

	@echo ">>> Transferring $(MINER_AMOUNT) TAO → miner..."
	@btcli wallet transfer \
		-w $(ALICE_COLD) \
		--dest $(MINER_WALLET) \
		--amount-tao $(MINER_AMOUNT) \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y

	@echo "    Transfers complete. Waiting for transactions to settle..."
	@sleep 5

## verify-balances: Check balances for all wallets
verify-balances:
	@echo ">>> Verifying wallet balances..."
	@echo "--- Alice ---"
	@btcli wallet balance -w $(ALICE_COLD) -n $(NETWORK)
	@echo "--- Creator ---"
	@btcli wallet balance -w $(CREATOR_WALLET) -n $(NETWORK)
	@echo "--- Validator ---"
	@btcli wallet balance -w $(VALIDATOR_WALLET) -n $(NETWORK)
	@echo "--- Miner ---"
	@btcli wallet balance -w $(MINER_WALLET) -n $(NETWORK)

# =============================================================================
# Subnet creation & emissions
# =============================================================================

## create-subnet: Register a new subnet using the creator wallet
create-subnet:
	@echo ">>> Creating subnet (owner=$(CREATOR_WALLET))..."
	@btcli subnets create \
		-w $(CREATOR_WALLET) \
		-H default \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y
	@echo "    Subnet created. Listing subnets..."
	@btcli subnets list -n $(NETWORK)
	@echo "    ⚠️  Localnet already has netuid 0 and 1; a new subnet is typically netuid $(NETUID)."

## start-emissions: Start emissions on the subnet (owner: sudo start)
start-emissions:
	@echo ">>> Starting emissions on netuid $(NETUID)..."
	@btcli sudo start \
		--netuid $(NETUID) \
		-w $(CREATOR_WALLET) \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y
	@echo "    Waiting $(EMISSION_START_WAIT)s for emissions to activate..."
	@sleep $(EMISSION_START_WAIT)
	@btcli subnets show $(NETUID) -n $(NETWORK)

## disable-commit-reveal: Disable commit-reveal weights (localnet SDK compatibility)
disable-commit-reveal:
	@echo ">>> Disabling commit-reveal weights on netuid $(NETUID)..."
	@btcli sudo set \
		--name commit_reveal_weights_enabled \
		--value 0 \
		--netuid $(NETUID) \
		-w $(CREATOR_WALLET) \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y 2>/dev/null || \
		echo "    ⚠️  Sudo set may need manual confirmation or a different hyperparameter name."
	@echo "    Waiting 5s for parameter change to settle..."
	@sleep 5
	@echo "    ✅ Commit-reveal disable attempted. Validators submit weights directly."

# =============================================================================
# Neuron registration & staking
# =============================================================================

## register-neurons: Register miner and validator hotkeys on the subnet
register-neurons:
	@echo ">>> Registering miner hotkey on netuid $(NETUID)..."
	@btcli subnets register \
		--netuid $(NETUID) \
		-w $(MINER_WALLET) \
		-H default \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y
	@echo "    Waiting $(REGISTRATION_WAIT)s before registering validator..."
	@sleep $(REGISTRATION_WAIT)

	@echo ">>> Registering validator hotkey on netuid $(NETUID)..."
	@btcli subnets register \
		--netuid $(NETUID) \
		-w $(VALIDATOR_WALLET) \
		-H default \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y
	@echo "    Waiting $(REGISTRATION_WAIT)s for registrations to settle..."
	@sleep $(REGISTRATION_WAIT)

	@echo ">>> Verifying neuron registrations..."
	@btcli subnets show $(NETUID) -n $(NETWORK)

## stake-validator: Stake TAO to the validator to acquire a validator permit
stake-validator:
	@echo ">>> Staking $(STAKE_AMOUNT) TAO to validator on netuid $(NETUID)..."
	@btcli stake add \
		--netuid $(NETUID) \
		-w $(VALIDATOR_WALLET) \
		-H default \
		--amount-tao $(STAKE_AMOUNT) \
		--no-slippage-protection \
		-n $(NETWORK) \
		$(MEV_FLAG) \
		-y
	@echo "    Waiting $(POST_STAKE_WAIT)s for validator permit to be granted..."
	@sleep $(POST_STAKE_WAIT)
	@echo ">>> Checking validator permit..."
	@btcli wallet overview -w $(VALIDATOR_WALLET) -n $(NETWORK)

# =============================================================================
# Verification
# =============================================================================

## verify-setup: Final verification — print subnet and metagraph info
verify-setup:
	@echo ""
	@echo "============================================================"
	@echo "  FINAL VERIFICATION"
	@echo "============================================================"
	@echo ">>> Subnet list:"
	@btcli subnets list -n $(NETWORK)
	@echo ""
	@echo ">>> Subnet metagraph (netuid $(NETUID)):"
	@btcli subnets metagraph $(NETUID) -n $(NETWORK)
	@echo ""
	@echo ">>> Validator overview:"
	@btcli wallet overview -w $(VALIDATOR_WALLET) -n $(NETWORK)

# =============================================================================
# Individual utility targets
# =============================================================================

## logs: Tail Docker container logs
logs:
	docker logs -f $(CONTAINER_NAME)

## status: Quick status check of chain and subnet
status:
	@echo ">>> Docker container status:"
	@docker ps --filter "name=$(CONTAINER_NAME)" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
	@echo ""
	@echo ">>> Subnet list:"
	@btcli subnets list -n $(NETWORK)

## metagraph: Show the metagraph for NETUID (e.g. make metagraph NETUID=2)
metagraph:
	@btcli subnets metagraph $(NETUID) -n $(NETWORK)

# =============================================================================
# Access Registry (Subtensor EVM — separate from local Bittensor WS)
# =============================================================================

EVM_RPC_URL  ?= https://test.chain.opentensor.ai
EVM_CHAIN_ID ?= 945
VALIDATOR_CONFIG ?= subnet/validator.config.yml

## deploy-registry: Compile and deploy AccessRegistry.sol (needs OWNER_PRIVATE_KEY)
deploy-registry:
	@test -n "$(OWNER_PRIVATE_KEY)" || (echo "Set OWNER_PRIVATE_KEY (funded H160 key)" && exit 1)
	RPC_URL=$(EVM_RPC_URL) CHAIN_ID=$(EVM_CHAIN_ID) OWNER_PRIVATE_KEY=$(OWNER_PRIVATE_KEY) \
		python key-generation/deploy_access_registry.py

## validator-live: Run validator with live eth_getLogs (set ACCESS_REGISTRY)
validator-live:
	@test -n "$(ACCESS_REGISTRY)" || (echo "Usage: make validator-live ACCESS_REGISTRY=0x..." && exit 1)
	cd subnet && python validator.py \
		--config validator.config.yml \
		--no-mock-events \
		--access-registry $(ACCESS_REGISTRY) \
		--evm-rpc $(EVM_RPC_URL) \
		--evm-chain-id $(EVM_CHAIN_ID) \
		--network $(NETWORK) \
		--netuid $(NETUID) \
		-w $(VALIDATOR_WALLET) \
		-H default
