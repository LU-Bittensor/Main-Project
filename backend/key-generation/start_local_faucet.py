"""Ensure a funded faucet on local Hardhat using its already-compiled artifact."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from web3 import Web3

ROOT = Path(__file__).resolve().parent


def ensure_faucet():
    rpc = os.environ['RPC_URL']
    url = urlsplit(rpc)
    if url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1'):
        raise RuntimeError('Local faucet bootstrap requires loopback Hardhat')
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
    if w3.eth.chain_id != 31337:
        raise RuntimeError('Expected local chain 31337')
    instance = w3.provider.make_request('hardhat_metadata', [])['result']['instanceId']
    artifact = json.loads((ROOT / 'testContractDeployment/artifacts/contracts/MediLinkFaucet.sol/MediLinkFaucet.json').read_text())
    key = os.environ.get('FAUCET_OPERATOR_PRIVATE_KEY') or os.environ['OWNER_PRIVATE_KEY']
    account = w3.eth.account.from_key(key)
    state = ROOT / '.grant-supabase-local/local-faucet.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    saved = json.loads(state.read_text()) if state.exists() else {}
    address = saved.get('address') if saved.get('instance') == instance else None
    if address:
        contract = w3.eth.contract(address=address, abi=artifact['abi'])
        if (bytes(w3.eth.get_code(address)) != bytes.fromhex(artifact['deployedBytecode'][2:])
                or contract.functions.operator().call() != account.address):
            address = None

    def send(tx):
        signed = account.sign_transaction(tx)
        receipt = w3.eth.wait_for_transaction_receipt(w3.eth.send_raw_transaction(signed.raw_transaction), timeout=60)
        if receipt.status != 1:
            raise RuntimeError('Local faucet transaction failed')
        return receipt

    def base():
        return {'from': account.address, 'nonce': w3.eth.get_transaction_count(account.address, 'pending'),
                'chainId': 31337, 'gasPrice': w3.eth.gas_price}

    if not address:
        factory = w3.eth.contract(abi=artifact['abi'], bytecode=artifact['bytecode'])
        args = (int(os.getenv('FAUCET_DRIP_AMOUNT', str(10**16))),
                int(os.getenv('FAUCET_MIN_BALANCE', str(5*10**15))),
                int(os.getenv('FAUCET_COOLDOWN', '86400')))
        receipt = send(factory.constructor(*args).build_transaction(base()))
        address = receipt.contractAddress
        # Persist before funding so an interrupted restart reuses this deployment.
        temp = state.with_suffix('.tmp')
        temp.write_text(json.dumps({'instance': instance, 'address': address}))
        temp.replace(state)
    target = int(os.getenv('LOCAL_FAUCET_BALANCE_WEI', str(10**18)))
    contract = w3.eth.contract(address=address, abi=artifact['abi'])
    if target < contract.functions.dripAmount().call():
        raise RuntimeError('LOCAL_FAUCET_BALANCE_WEI must cover at least one drip')
    balance = w3.eth.get_balance(address)
    if balance < target:
        send({**base(), 'to': address, 'value': target-balance, 'gas': 100000})
    if w3.eth.get_balance(address) < target:
        raise RuntimeError('Faucet funding not confirmed')
    return address


if __name__ == '__main__':
    print(ensure_faucet())
