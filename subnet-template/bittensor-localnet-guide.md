# Bittensor Localnet Setup Guide

Complete step-by-step instructions for setting up a local Bittensor blockchain instance for development and testing.

## Table of Contents

1. [Prerequisites and System Requirements](#prerequisites-and-system-requirements)
2. [Install Bittensor SDK](#install-bittensor-sdk)
3. [Install Bittensor CLI](#install-bittensor-cli)
4. [Run Local Subtensor Blockchain](#run-local-subtensor-blockchain)
5. [Provision Wallets](#provision-wallets)
6. [Create a Subnet](#create-a-subnet)
7. [Mining and Validating](#mining-and-validating)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites and System Requirements

Before you begin, ensure your system meets the following requirements:

### Operating System Support

**Supported Platforms:**
- macOS (Intel and Apple Silicon)
- Linux (Ubuntu, Debian, and other distributions)
- Windows (requires WSL 2 with Ubuntu)

**Windows-Specific Note:** While wallet transactions (delegating, transferring, registering, staking) are supported on Windows via WSL 2, mining and validating operations are **not recommended and not supported** on Windows machines.

### Required Software

- **Docker:** Download and install [Docker Desktop](https://docs.docker.com/desktop/use-desktop/) for your operating system
- **Python:** Version 3.10–3.15 (for Bittensor SDK); Python 3.9–3.13 (for CLI and wallet tools)
- **Git:** For cloning repositories
- **Rust:** Required only for Linux installations (see [Rust installation guide](https://www.rust-lang.org/tools/install))

### Hardware Requirements

- **Minimum RAM:** 4 GB
- **Minimum Storage:** 10 GB free space
- **Processor:** Any modern multi-core processor

### Verify Python Installation

Confirm you have Python 3.10 or later installed:

```bash
python3 --version
```

If the version is below 3.10, update Python through your system package manager or [python.org](https://www.python.org/).

---

## Install Bittensor SDK

The Bittensor SDK is the core library for interacting with the Bittensor network and blockchain.

### Create and Activate a Virtual Environment

To avoid dependency conflicts, create a dedicated Python virtual environment:

```bash
python3 -m venv btsdk_venv
source btsdk_venv/bin/activate
```

**On Windows (WSL 2):**
```bash
python3 -m venv btsdk_venv
source btsdk_venv/bin/activate
```

After activation, your terminal prompt should show `(btsdk_venv)` at the beginning.

### Install from PyPI

Install the Bittensor SDK using pip:

```bash
pip install bittensor
```

This command installs both the Bittensor SDK and the Bittensor CLI (btcli).

### Optional: Install with PyTorch Support

If you plan to use machine learning models with your subnet, install Bittensor with PyTorch:

```bash
pip install "bittensor[torch]"
```

**Note:** Use quotes if the command fails in your shell.

### Optional: Install with Cubit Support

Cubit provides GPU acceleration for certain operations. First install Cubit separately:

```bash
# Follow Cubit installation from https://github.com/opentensor/cubit
# Then install Bittensor
pip install bittensor
```

**Note:** Cubit only supports Python 3.9 and 3.10.

### Verify SDK Installation

Confirm the installation was successful:

```bash
python3 -m bittensor
```

**Expected Output:**
```
Bittensor SDK version: 10.x.x
```

---

## Install Bittensor CLI

The Bittensor CLI (btcli) is the command-line tool for managing wallets, subnets, and network operations.

### Python Version Requirements

Ensure you have Python 3.9–3.13 installed. Check your version:

```bash
python3 --version
```

### Install from PyPI

Install btcli using pip:

```bash
pip install bittensor-cli
```

### Verify CLI Installation

Confirm the installation:

```bash
btcli --version
```

**Expected Output:**
```
BTCLI version: 9.2.0
```

### Configure Bittensor CLI

Create a configuration file at `~/.bittensor/config.yml` to set default values for common operations:

```yaml
chain: ws://127.0.0.1:9945
network: local
no_cache: false
wallet_hotkey: hotkey-user1
wallet_name: coldkey-user1
wallet_path: ~/.bittensor/wallets
metagraph_cols:
  ACTIVE: true
  AXON: true
  COLDKEY: true
  CONSENSUS: true
  DIVIDENDS: true
  EMISSION: true
  GLOBAL_STAKE: true
  HOTKEY: true
  INCENTIVE: true
  LOCAL_STAKE: true
  RANK: true
  STAKE_WEIGHT: true
  TRUST: true
  UID: true
  UPDATED: true
  VAL: true
  VTRUST: true
```

**Note:** If both `chain` and `network` are present, `chain` takes precedence.

### Environment Variables

Set optional environment variables to customize btcli behavior:

| Variable | Default | Purpose |
|----------|---------|---------|
| `USE_TORCH` | 0 | Set to 1 to use PyTorch instead of NumPy |
| `DISK_CACHE` | 0 | Set to 1 to cache substrate calls to disk for faster subsequent operations |
| `BTCLI_CONFIG_PATH` | `~/.bittensor/config.yml` | Custom path to configuration file |
| `BTCLI_DEBUG_FILE` | `~/.bittensor/debug.txt` | Debug log file location |

**Example:** Enable disk caching:
```bash
export DISK_CACHE=1
```

### Debugging

Debug logs are automatically saved to `~/.bittensor/debug.txt` and overwritten after each command. To preserve logs:

```bash
btcli --debug
```

This creates a new debug file with a timestamp. Share logs with the development team on [Discord](https://discord.gg/bittensor) or [GitHub Issues](https://github.com/opentensor/btcli/issues/new) for troubleshooting.

---

## Run Local Subtensor Blockchain

A local Subtensor blockchain instance allows you to test and develop without using the mainnet.

### Prerequisites

Before running the local chain, ensure:

- Docker is installed and running
- Bittensor SDK and CLI are installed (from previous sections)

### Pull the Docker Image

Download the official Subtensor localnet Docker image:

```bash
docker pull ghcr.io/opentensor/subtensor-localnet:devnet-ready
```

This image contains a pre-configured Subtensor blockchain ready for local development.

### Run the Docker Container

#### Option 1: Fast Blocks Mode (Recommended for Development)

Fast blocks mode reduces block processing time to 250ms, enabling rapid testing and faster feedback for operations like staking and subnet creation:

```bash
docker run --rm --name local_chain -p 9944:9944 -p 9945:9945 ghcr.io/opentensor/subtensor-localnet:devnet-ready
```

#### Option 2: Normal Blocks Mode (Default 12-Second Blocks)

Normal mode uses the standard 12-second block time and provides additional enhancements like earlier subnet eligibility:

```bash
docker run --rm --name local_chain -p 9944:9944 -p 9945:9945 ghcr.io/opentensor/subtensor-localnet:devnet-ready False
```

### Understanding Docker Flags

- `--rm`: Automatically removes the container when it exits (state is lost)
- `--name local_chain`: Names the container for easy reference
- `-p 9944:9944`: Maps RPC port (9944)
- `-p 9945:9945`: Maps WebSocket port (9945)

**To preserve state between runs**, omit the `--rm` flag and use `docker start local_chain` to restart.

### Verify the Local Chain is Running

In a new terminal, verify your local blockchain is operational:

```bash
btcli subnet list --network ws://127.0.0.1:9944
```

You should see output confirming the connection to the local chain.

---

## Provision Wallets

Wallets are required to participate in the network. Create separate wallets for different roles: subnet creator, validator, and miner.

### Access the Alice Development Account

The local blockchain comes pre-provisioned with an "Alice" account containing 1,000,000 TAO for testing purposes.

Create a wallet for the Alice account:

```bash
btcli wallet create --uri alice
```

You'll be prompted to set a coldkey and hotkey name. Use descriptive names like `alice-cold` and `alice-hot`.

Verify the Alice wallet balance:

```bash
btcli wallet balance --wallet.name alice --network ws://127.0.0.1:9945
```

### Create Wallets for Each Role

Create three separate wallets for subnet creation, validation, and mining:

```bash
# Create subnet creator wallet
btcli wallet create \
  --wallet.name sn-creator \
  --hotkey default

# Create validator wallet
btcli wallet create \
  --wallet.name test-validator \
  --hotkey default

# Create miner wallet
btcli wallet create \
  --wallet.name test-miner \
  --hotkey default
```

**Why separate wallets?** Each role needs its own wallet to independently run processes and receive emissions without conflicts.

### Transfer TAO from Alice to Your Wallets

First, list all wallets to get the SS58 addresses:

```bash
btcli wallet list --network ws://127.0.0.1:9945
```
btcli wallet list (works instead)

*Does not work*

btcli wallet balance --wallet.name <ColdKey> --network ws://127.0.0.1:9945

Note the coldkey SS58 addresses for each wallet. Transfer TAO from Alice to each:

Wallets
├── Coldkey sn-creator  ss58_address 5FJ6TyfpP1aBMBDJZjoCKj6xNu2Xr6R8eVfH4MhWcitKT3Lc
│   └── Hotkey default  ss58_address 5Hm838zLPvu3HU1JWBZT1imypG27LzJBvCMPHHHThNp9x4Kp
│       
├── Coldkey AidanColdKey  ss58_address 5Da8JBtm7wThMMqARpUhKixZwj9rWgDGk5fYaNCAT4WBdBeg
│   └── Hotkey AidanHotKey  ss58_address 5FjpTtAqFYgp62mB2L6jWuP2Exau4cMd25Ym3Hq4PsVnEBw2
│       
├── Coldkey alice-cold  ss58_address 5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY
│   └── Hotkey alice-hot  ss58_address 5GrwvaEF5zXb26Fz9rcQpDWS57CtERHpNehXCPcNoHGKutQY
│       
├── Coldkey test-miner  ss58_address 5G8nqqJUxhgZyburAcbVN8FXbr1hs9z1B4pcKeQSSZJk4hex
│   └── Hotkey default  ss58_address 5D4Vb9QRR36kNV7J5SKGC5CQvQzGvvxaGcQyxhheRg383PpV
│       
└── Coldkey test-validator  ss58_address 5CAa16UCmu8NW1RmXqVBFAQ37NgFUqyRzFBbTYCAqfxXJyMn
    └── Hotkey default  ss58_address 5Hdjvc34NXD8ZL3rksMkVCAC5Vxsx49e4DWgysuTnwRBpuGP

```bash
# Transfer to subnet creator wallet *need 1000 to create subnet, so 1001
btcli wallet transfer \
  --wallet.name alice-cold \
  --destination 5FJ6TyfpP1aBMBDJZjoCKj6xNu2Xr6R8eVfH4MhWcitKT3Lc \
  --amount 1001 \
  --network ws://127.0.0.1:9945

# Transfer to validator wallet
btcli wallet transfer \
  --wallet.name alice-cold \
  --destination 5CAa16UCmu8NW1RmXqVBFAQ37NgFUqyRzFBbTYCAqfxXJyMn \
  --amount 1000 \
  --network ws://127.0.0.1:9945

# Transfer to miner wallet
btcli wallet transfer \
  --wallet.name alice-cold \
  --destination 5G8nqqJUxhgZyburAcbVN8FXbr1hs9z1B4pcKeQSSZJk4hex \
  --amount 1000 \
  --network ws://127.0.0.1:9945
```

Replace `<address>` with actual SS58 addresses obtained from `btcli wallet list`.

### Verify Wallet Balances

Confirm all wallets are funded:

```bash
btcli wallet balance --wallet.name sn-creator --network ws://127.0.0.1:9945
btcli wallet balance --wallet.name test-validator --network ws://127.0.0.1:9945
btcli wallet balance --wallet.name test-miner --network ws://127.0.0.1:9945
```

Each wallet should show a balance of approximately 1,000 TAO.

---

## Create a Subnet

With wallets provisioned, create your first subnet on the local chain.

### Prerequisites

Before creating a subnet, ensure:

- Local Subtensor chain is running (from Section 4)
- Wallets are provisioned and funded (from Section 5)
- Subnet creator wallet has at least 1,000 TAO for the burn cost

### Create the Subnet

Run the subnet creation command:

```bash
btcli subnet create \
  --subnet-name medchain-first-subnet \
  --wallet.name sn-creator \
  --wallet.hotkey default \
  --network ws://127.0.0.1:9945
```

**For fast blocks mode, add the `--no-mev-protection` flag:**

```bash
btcli subnet create \
  --subnet-name medchain-first-subnet \
  --wallet.name sn-creator \
  --wallet.hotkey default \
  --network ws://127.0.0.1:9945 \
  --no-mev-protection
```

### Confirm Subnet Creation

You'll be prompted to confirm the burn cost and enter your wallet password:

```
Subnet burn cost: τ 1,000.0000
Your balance is: τ 1,001.0000
Do you want to burn τ 1,000.0000 to register a subnet? [y/n]: y
Enter your password: ****
Decrypting...
🌏  📡 Registering subnet...
```

### Verify Subnet Creation

List all subnets to confirm creation:

```bash
btcli subnet list --network ws://127.0.0.1:9945
```

Note the `netuid` (subnet ID) from the output—you'll need this for mining and validating.

### Start Emissions on the Subnet

Activate the subnet to enable staking and emission distribution:

```bash
btcli subnet start \
  --netuid <NETUID> \
  --wallet.name sn-creator \
  --network ws://127.0.0.1:9945
```

Replace `<NETUID>` with the subnet ID from the previous step (e.g., `1`).

Wait a moment, then verify emissions have started:

```bash
btcli subnet show --netuid <NETUID> --network ws://127.0.0.1:9945
```

Look for a non-zero value in the "Emission" column, indicating active emissions.

---

## Mining and Validating

Now that your subnet is active, register and run miner and validator processes.

### Prerequisites

Ensure you've completed all previous sections:

- Local Subtensor chain is running
- Wallets are created and funded
- Subnet is created and emissions are active
- Note the subnet's `netuid`

### 1. Register Neuron Hotkeys

Register both the miner and validator hotkeys on the subnet:

```bash
# Register miner hotkey
btcli subnet register \
  --netuid <NETUID> \
  --wallet.name test-miner \
  --hotkey default \
  --network ws://127.0.0.1:9945

# Register validator hotkey
btcli subnet register \
  --netuid <NETUID> \
  --wallet.name test-validator \
  --hotkey default \
  --network ws://127.0.0.1:9945

```

You'll be prompted to confirm the registration fee and enter your wallet password for each.
*Sometimes the recycling TAO will not recieve confirmation from the node that it went through*
check "btcli subnet metagraph --netuid <NETUID> --subtensor.chain_endpoint ws://127.0.0.1:9945" to see if your wallet is there

### Verify Registration

Confirm both hotkeys are registered:

```bash
btcli subnet show --netuid <NETUID> --network ws://127.0.0.1:9945
```

Look for both neurons in the subnet's neuron list.

### 2. Acquire Validator Permit

To validate on the subnet, stake TAO to obtain a validator permit.

Stake TAO to the validator hotkey:

```bash
btcli stake add \
  --netuid 2 \
  --wallet.name test-validator \
  --hotkey default \
  --partial \
  --network ws://127.0.0.1:9945
  --no-mev-protection
```

**For fast blocks mode, add the `--no-mev-protection` flag:**

```bash
btcli stake add \
  --netuid <NETUID> \
  --wallet.name test-validator \
  --hotkey default \
  --partial \
  --network ws://127.0.0.1:9945 \
  --no-mev-protection
```

Enter the amount to stake when prompted. The validator should become eligible for a permit after staking.

### Verify Validator Permit

Check if the validator has a permit:

```bash
btcli wallet overview --wallet.name test-validator --network ws://127.0.0.1:9945
```

An asterisk (*) in the `VPERMIT` column indicates a valid validator permit. On localnet, permits are typically granted quickly after staking. If not immediate, wait until the end of the subnet's tempo (epoch).

### 3. Clone the Subnet Template Repository

The subnet-template contains reference implementations for miners and validators:

```bash
git clone https://github.com/opentensor/subnet-template.git
cd subnet-template
```

### Install Dependencies

Create a virtual environment and install dependencies:

```bash
python3 -m venv subnet_venv
source subnet_venv/bin/activate
pip install bittensor
```

### 4. Run the Miner and Validator

Run the miner and validator processes in separate terminal sessions to ensure they operate concurrently.

#### Terminal 1: Start the Miner

In the subnet-template directory, start the miner process:

```bash
python miner.py \
  --wallet.name test-miner \
  --wallet.hotkey default \
  --netuid <NETUID> \
  --axon.port 8901 \
  --subtensor.network local \
  --logging.info
```

**Expected Output:**
The miner launches an Axon server on port 8901 and begins listening for validator requests. You should see initialization logs confirming the Axon is running.

#### Terminal 2: Start the Validator

In another terminal, navigate to the subnet-template directory and start the validator:

```bash
python validator.py \
  --wallet.name test-validator \
  --wallet.hotkey default \
  --netuid <NETUID> \
  --subtensor.network local \
  --logging.info
```

**Expected Output:**
The validator begins querying miners and setting weights based on responses. You should see logs showing queries and weight updates.

### Monitor Miner and Validator Activity

Use the `--logging.info` flag (included above) to print real-time logs to the console. Both processes should show active communication.

**Miner logs example:**
```
Miner responding to request: input=42, output=84
```

**Validator logs example:**
```
Setting weights on subnet. Weights: [0.5, 0.5]
```

### 5. Check Emissions

After weights have been set successfully, check the updated emissions at the end of the subnet's tempo (epoch):

```bash
btcli subnet show --netuid <NETUID> --network ws://127.0.0.1:9945
```

Look for updated emission values in the output, indicating that miners and validators are earning emissions based on their contributions.

---

## Troubleshooting

### Insufficient Funds Error

**Error:**
```
Subnet burn cost: τ 1,000.0000
Your balance of: τ 0.0000 is not enough to burn τ 1,000.0000 to register a subnet.
```

**Solution:**
The coldkey needs sufficient TAO to cover subnet creation costs. Transfer additional TAO from Alice:

```bash
btcli wallet transfer \
  --wallet.name alice \
  --destination <your-wallet-ss58-address> \
  --amount 2000 \
  --network ws://127.0.0.1:9945
```

### Unregistered Miner or Validator

**Error:**
```
Neuron not registered on subnet.
```

**Solution:**
Register the hotkey on the subnet before running the miner or validator:

```bash
btcli subnet register \
  --netuid <NETUID> \
  --wallet.name <your-wallet> \
  --hotkey default \
  --network ws://127.0.0.1:9945
```

### NeuronNoValidatorPermit

**Error:**
```
NeuronNoValidatorPermit: Neuron attempting to set weights without validator permit.
```

**Solution:**
Stake sufficient TAO to obtain a validator permit:

```bash
btcli stake add \
  --netuid <NETUID> \
  --wallet.name test-validator \
  --hotkey default \
  --partial \
  --network ws://127.0.0.1:9945
```

Wait until the end of the subnet's tempo for the permit to be granted.

### WeightVecLengthIsLow

**Error:**
```
WeightVecLengthIsLow: Validator attempting to set weights for fewer elements than allowed.
```

**Solution:**
Ensure all neurons in the subnet are responsive. This error occurs when:

- The miner is not running or is unresponsive
- All neurons are offline

Verify the miner is running and accessible before running the validator again.

### Docker Connection Issues

**Error:**
```
Cannot connect to local chain at ws://127.0.0.1:9945
```

**Solution:**
Verify Docker is running and the container is active:

```bash
docker ps
```

If not running, restart the container:

```bash
docker run --rm --name local_chain -p 9944:9944 -p 9945:9945 ghcr.io/opentensor/subtensor-localnet:devnet-ready
```

### Port Already in Use

**Error:**
```
Error binding to port 9944 or 9945.
```

**Solution:**
Another process is using the required ports. Stop the conflicting process or use different ports:

```bash
docker run --rm --name local_chain -p 9946:9944 -p 9947:9945 ghcr.io/opentensor/subtensor-localnet:devnet-ready
```

Then update all connection strings to use the new ports (e.g., `ws://127.0.0.1:9947`).

### Python Version Mismatch

**Error:**
```
Python version not supported for bittensor SDK.
```

**Solution:**
Update Python to version 3.10–3.15:

```bash
python3 --version
```

Install a supported version from [python.org](https://www.python.org/) or use a version manager like `pyenv`.

---

## Next Steps

After successfully running miners and validators on your local subnet:

1. **Modify the subnet logic:** Edit `miner.py` and `validator.py` to implement your custom protocol
2. **Deploy to testnet:** Follow the [Bittensor testnet deployment guide](https://docs.learnbittensor.org/subnets/create-a-subnet)
3. **Join mainnet:** Once tested and audited, deploy your subnet to the Bittensor mainnet
4. **Explore advanced features:** Learn about sub-subnets, custom incentive mechanisms, and more

---

## Resources and Support

- **Official Documentation:** [LearnBittensor.org](https://learnbittensor.org/)
- **GitHub Repository:** [opentensor/bittensor](https://github.com/opentensor/bittensor)
- **Discord Community:** [Bittensor Discord](https://discord.gg/bittensor)
- **Issue Tracker:** [GitHub Issues](https://github.com/opentensor/btcli/issues)
- **Subnet Template:** [opentensor/subnet-template](https://github.com/opentensor/subnet-template)

---

## Quick Reference Commands

| Task | Command |
|------|---------|
| Check Python version | `python3 --version` |
| Create virtual environment | `python3 -m venv btsdk_venv && source btsdk_venv/bin/activate` |
| Install Bittensor SDK | `pip install bittensor` |
| Install Bittensor CLI | `pip install bittensor-cli` |
| Pull Docker image | `docker pull ghcr.io/opentensor/subtensor-localnet:devnet-ready` |
| Run Docker (fast blocks) | `docker run --rm --name local_chain -p 9944:9944 -p 9945:9945 ghcr.io/opentensor/subtensor-localnet:devnet-ready` |
| Create wallet | `btcli wallet create --wallet.name <name> --hotkey default` |
| Check balance | `btcli wallet balance --wallet.name <name> --network ws://127.0.0.1:9945` |
| List subnets | `btcli subnet list --network ws://127.0.0.1:9945` |
| Create subnet | `btcli subnet create --subnet-name <name> --wallet.name sn-creator --wallet.hotkey default --network ws://127.0.0.1:9945` |
| Start emissions | `btcli subnet start --netuid <ID> --wallet.name sn-creator --network ws://127.0.0.1:9945` |
| Register neuron | `btcli subnet register --netuid <ID> --wallet.name <name> --hotkey default --network ws://127.0.0.1:9945` |
| Stake for permit | `btcli stake add --netuid <ID> --wallet.name test-validator --hotkey default --partial --network ws://127.0.0.1:9945` |
| Run miner | `python miner.py --wallet.name test-miner --wallet.hotkey default --netuid <ID> --axon.port 8901 --subtensor.network local` |
| Run validator | `python validator.py --wallet.name test-validator --wallet.hotkey default --netuid <ID> --subtensor.network local` |
| Show subnet info | `btcli subnet show --netuid <ID> --network ws://127.0.0.1:9945` |

---

**Last Updated:** February 8, 2026

**Version:** 1.0.0

**Compatibility:** Bittensor SDK v10.x, Bittensor CLI v9.2.0+, Python 3.10–3.15
