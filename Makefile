# =============================================================================
# Bittensor Local Subtensor Chain - Full Setup Makefile
# =============================================================================
# Usage:
#   make all            - Run the full setup from scratch
#   make help           - Show all available targets
#   make clean          - Stop the chain and remove the container
#
# Prerequisites:
#   - Docker installed and running
#   - btcli installed (Bittensor CLI)
#   - Create a .env file that has your wallet passwords (if necessary)
# =============================================================================

# --- Configuration -----------------------------------------------------------

DOCKER_IMAGE   := ghcr.io/opentensor/subtensor-localnet:devnet-ready
CONTAINER_NAME := local_chain
RPC_PORT       := 9944
WS_PORT        := 9945
NETWORK        := ws://127.0.0.1:$(WS_PORT)

SUBNET_NAME    := medchain-first-subnet

# Wallet names
ALICE_COLD     := alice-cold
ALICE_HOT      := alice-hot
CREATOR_WALLET := sn-creator
VALIDATOR_WALLET := test-validator
MINER_WALLET   := test-miner

# Transfer amounts (TAO)
CREATOR_AMOUNT  := 1001
VALIDATOR_AMOUNT := 1000
MINER_AMOUNT    := 1000
STAKE_AMOUNT    := 50

# Timing (seconds)
CHAIN_STARTUP_WAIT   := 10   # Wait for chain to start accepting connections
REGISTRATION_WAIT    := 6    # Wait between neuron registrations
POST_STAKE_WAIT      := 12   # Wait after staking for permit eligibility
EMISSION_START_WAIT  := 6    # Wait after starting emissions

# Fast blocks mode: set to empty string "" to use normal 12s blocks
FAST_BLOCKS := true

# MEV protection flag (required for fast blocks localnet)
MEV_FLAG := --no-mev-protection

# =============================================================================
# Phony commands to run
# =============================================================================
.PHONY: all help \
        pull start-chain wait-for-chain stop-chain clean \
        create-alice-wallet create-creator-wallet create-validator-wallet create-miner-wallet \
        create-wallets fund-wallets verify-balances \
        create-subnet start-emissions \
        register-neurons stake-validator verify-setup

# =============================================================================
# Top-level targets
# =============================================================================

## all: Run the complete setup end-to-end
all: pull start-chain wait-for-chain \
     create-wallets \
     fund-wallets verify-balances \
     create-subnet start-emissions \
     register-neurons stake-validator \
     verify-setup
	@echo ""
	@echo "============================================================"
	@echo "  ✅  Local Bittensor subnet is fully configured!"
	@echo "============================================================"
	@echo "  Chain:     $(NETWORK)"
	@echo "  Subnet:    $(SUBNET_NAME)"
	@echo "  Wallets:   $(CREATOR_WALLET), $(VALIDATOR_WALLET), $(MINER_WALLET)"
	@echo "============================================================"
	@echo "  To view subnet:    btcli subnet list --network $(NETWORK)"
	@echo "  To view metagraph: btcli subnet metagraph --netuid \$$NETUID --subtensor.chain_endpoint $(NETWORK)"
	@echo "  To stop chain:     make clean"
	@echo "============================================================"

## help: Show this help message
help:
	@echo "Bittensor Local Chain Setup"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /'

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
	@echo ">>> Verifying chain is reachable..."
	@btcli subnet list --network $(NETWORK) > /dev/null 2>&1 && \
		echo "    ✅ Chain is up." || \
		(echo "    ⚠️  Chain not yet ready — waiting another 10s..." && sleep 10 && \
		 btcli subnet list --network $(NETWORK) > /dev/null 2>&1 && echo "    ✅ Chain is up." || \
		 (echo "    ❌ Chain failed to start. Check: docker logs $(CONTAINER_NAME)" && exit 1))

## stop-chain: Stop the running chain container
stop-chain:
	@echo ">>> Stopping chain container '$(CONTAINER_NAME)'..."
	@docker stop $(CONTAINER_NAME) 2>/dev/null || echo "    Container not running."

## clean: Stop chain and remove any leftover containers
clean: stop-chain
	@echo ">>> Cleanup complete."

# =============================================================================
# Wallet creation (Even if wallets are already created, no passwords - can add your password yourself if necessary)
# =============================================================================

## create-alice-wallet: Create the Alice development wallet (pre-funded on localnet)
create-alice-wallet:
	@echo ">>> Creating Alice wallet (coldkey=$(ALICE_COLD), hotkey=$(ALICE_HOT))..."
	@btcli wallet create \
		--wallet.name $(ALICE_COLD) \
		--wallet.hotkey $(ALICE_HOT) \
		--uri alice \
		--no-use-password 2>/dev/null || true
	@echo "    Alice wallet ready."

## create-creator-wallet: Create the subnet creator wallet
create-creator-wallet:
	@echo ">>> Creating subnet creator wallet ($(CREATOR_WALLET))..."
	@btcli wallet create \
		--wallet.name $(CREATOR_WALLET) \
		--wallet.hotkey default \
		--no-use-password 2>/dev/null || true

## create-validator-wallet: Create the validator wallet
create-validator-wallet:
	@echo ">>> Creating validator wallet ($(VALIDATOR_WALLET))..."
	@btcli wallet create \
		--wallet.name $(VALIDATOR_WALLET) \
		--wallet.hotkey default \
		--no-use-password 2>/dev/null || true

## create-miner-wallet: Create the miner wallet
create-miner-wallet:
	@echo ">>> Creating miner wallet ($(MINER_WALLET))..."
	@btcli wallet create \
		--wallet.name $(MINER_WALLET) \
		--wallet.hotkey default \
		--no-use-password 2>/dev/null || true

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
	@echo ">>> Fetching wallet addresses..."
	$(eval CREATOR_ADDR  := $(shell btcli wallet list | grep -A2 "$(CREATOR_WALLET)" | grep "ss58_address" | head -1 | awk '{print $$NF}'))
	$(eval VALIDATOR_ADDR := $(shell btcli wallet list | grep -A2 "$(VALIDATOR_WALLET)" | grep "ss58_address" | head -1 | awk '{print $$NF}'))
	$(eval MINER_ADDR    := $(shell btcli wallet list | grep -A2 "$(MINER_WALLET)" | grep "ss58_address" | head -1 | awk '{print $$NF}'))

	@echo "    Creator  address: $(CREATOR_ADDR)"
	@echo "    Validator address: $(VALIDATOR_ADDR)"
	@echo "    Miner    address: $(MINER_ADDR)"

	@echo ">>> Transferring $(CREATOR_AMOUNT) TAO → subnet creator..."
	@btcli wallet transfer \
		--wallet.name $(ALICE_COLD) \
		--destination $(CREATOR_ADDR) \
		--amount $(CREATOR_AMOUNT) \
		--network $(NETWORK) \
		--no-prompt

	@echo ">>> Transferring $(VALIDATOR_AMOUNT) TAO → validator..."
	@btcli wallet transfer \
		--wallet.name $(ALICE_COLD) \
		--destination $(VALIDATOR_ADDR) \
		--amount $(VALIDATOR_AMOUNT) \
		--network $(NETWORK) \
		--no-prompt

	@echo ">>> Transferring $(MINER_AMOUNT) TAO → miner..."
	@btcli wallet transfer \
		--wallet.name $(ALICE_COLD) \
		--destination $(MINER_ADDR) \
		--amount $(MINER_AMOUNT) \
		--network $(NETWORK) \
		--no-prompt

	@echo "    Transfers complete. Waiting for transactions to settle..."
	@sleep 5

## verify-balances: Check balances for all wallets
verify-balances:
	@echo ">>> Verifying wallet balances..."
	@echo "--- Alice ---"
	@btcli wallet balance --wallet.name $(ALICE_COLD) --network $(NETWORK)
	@echo "--- Creator ---"
	@btcli wallet balance --wallet.name $(CREATOR_WALLET) --network $(NETWORK)
	@echo "--- Validator ---"
	@btcli wallet balance --wallet.name $(VALIDATOR_WALLET) --network $(NETWORK)
	@echo "--- Miner ---"
	@btcli wallet balance --wallet.name $(MINER_WALLET) --network $(NETWORK)

# =============================================================================
# Subnet creation & emissions
# =============================================================================

## create-subnet: Create a new subnet using the creator wallet
create-subnet:
	@echo ">>> Creating subnet '$(SUBNET_NAME)'..."
	@btcli subnet create \
		--subnet-name $(SUBNET_NAME) \
		--wallet.name $(CREATOR_WALLET) \
		--wallet.hotkey default \
		--network $(NETWORK) \
		$(MEV_FLAG) \
		--no-prompt
	@echo "    Subnet created. Listing subnets..."
	@btcli subnet list --network $(NETWORK)
	@echo "    ⚠️  Note the netuid above — set NETUID=<id> when running individual targets."

# Set NETUID
NETUID = 2

## start-emissions: Start emissions on the subnet
start-emissions:
	@echo ">>> Starting emissions on netuid $(NETUID)..."
	@btcli subnet start \
		--netuid $(NETUID) \
		--wallet.name $(CREATOR_WALLET) \
		--network $(NETWORK) \
		--no-prompt
	@echo "    Waiting $(EMISSION_START_WAIT)s for emissions to activate..."
	@sleep $(EMISSION_START_WAIT)
	@btcli subnet show --netuid $(NETUID) --network $(NETWORK)

# =============================================================================
# Neuron registration & staking
# =============================================================================

## register-neurons: Register miner and validator hotkeys on the subnet
register-neurons:
	@echo ">>> Registering miner hotkey on netuid $(NETUID)..."
	@btcli subnet register \
		--netuid $(NETUID) \
		--wallet.name $(MINER_WALLET) \
		--hotkey default \
		--network $(NETWORK) \
		--no-prompt
	@echo "    Waiting $(REGISTRATION_WAIT)s before registering validator..."
	@sleep $(REGISTRATION_WAIT)

	@echo ">>> Registering validator hotkey on netuid $(NETUID)..."
	@btcli subnet register \
		--netuid $(NETUID) \
		--wallet.name $(VALIDATOR_WALLET) \
		--hotkey default \
		--network $(NETWORK) \
		--no-prompt
	@echo "    Waiting $(REGISTRATION_WAIT)s for registrations to settle..."
	@sleep $(REGISTRATION_WAIT)

	@echo ">>> Verifying neuron registrations..."
	@btcli subnet show --netuid $(NETUID) --network $(NETWORK)

## stake-validator: Stake TAO to the validator to acquire a validator permit
stake-validator:
	@echo ">>> Staking $(STAKE_AMOUNT) TAO to validator on netuid $(NETUID)..."
	@btcli stake add \
		--netuid $(NETUID) \
		--wallet.name $(VALIDATOR_WALLET) \
		--hotkey default \
		--amount $(STAKE_AMOUNT) \
		--network $(NETWORK) \
		--partial \
		$(MEV_FLAG) \
		--no-prompt
	@echo "    Waiting $(POST_STAKE_WAIT)s for validator permit to be granted..."
	@sleep $(POST_STAKE_WAIT)
	@echo ">>> Checking validator permit (look for '*' in VPERMIT column)..."
	@btcli wallet overview --wallet.name $(VALIDATOR_WALLET) --network $(NETWORK)

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
	@btcli subnet list --network $(NETWORK)
	@echo ""
	@echo ">>> Subnet metagraph (netuid $(NETUID)):"
	@btcli subnet metagraph \
		--netuid $(NETUID) \
		--subtensor.chain_endpoint $(NETWORK)
	@echo ""
	@echo ">>> Validator overview:"
	@btcli wallet overview --wallet.name $(VALIDATOR_WALLET) --network $(NETWORK)

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
	@btcli subnet list --network $(NETWORK)

## metagraph: Show the metagraph for NETUID (e.g. make metagraph NETUID=1)
metagraph:
	@btcli subnet metagraph \
		--netuid $(NETUID) \
		--subtensor.chain_endpoint $(NETWORK)