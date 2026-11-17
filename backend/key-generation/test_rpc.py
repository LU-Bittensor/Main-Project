"""Restricted, opt-in JSON-RPC bridge for the disposable local Hardhat sidecar."""
import requests
from flask import jsonify, request

METHODS = frozenset({
    'eth_chainId', 'net_version', 'eth_blockNumber', 'eth_getBalance',
    'eth_getCode', 'eth_call', 'eth_estimateGas', 'eth_gasPrice',
    'eth_maxPriorityFeePerGas', 'eth_feeHistory', 'eth_getTransactionCount',
    'eth_getTransactionReceipt', 'eth_getTransactionByHash',
    'eth_getBlockByNumber', 'eth_getBlockByHash', 'eth_getLogs',
    'eth_sendRawTransaction',
})


def install_rpc(app):
    @app.post('/rpc')
    def rpc():
        request.max_content_length = 256 * 1024
        payload = request.get_json(silent=True)
        calls = payload if isinstance(payload, list) else [payload]
        if not calls or len(calls) > 20 or any(
            not isinstance(call, dict) or call.get('jsonrpc') != '2.0'
            or call.get('method') not in METHODS
            or not isinstance(call.get('params', []), list)
            for call in calls
        ):
            return jsonify(error='Unsupported JSON-RPC request'), 400
        try:
            response = requests.post('http://127.0.0.1:8545', json=payload, timeout=20)
            response.raise_for_status()
            return jsonify(response.json())
        except (requests.RequestException, ValueError):
            return jsonify(error='Test chain unavailable'), 503
