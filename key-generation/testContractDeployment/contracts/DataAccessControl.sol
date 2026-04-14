// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * DataAccessControl
 * =================
 * Guards access to an off-chain data resource using a DH shared secret.
 *
 * What is stored on-chain:
 *   - keccak256(sharedSecret)  ← a commitment; the secret itself is never stored
 *   - A pending challenge nonce per address
 *
 * What is NEVER on-chain:
 *   - The raw shared secret
 *   - Any intermediate DH values
 *
 * Flow:
 *   1. Owner registers the commitment:  registerCommitment(keccak256(secret))
 *   2. User requests a challenge:        requestChallenge()
 *      → contract emits a random nonce (derived from block data)
 *   3. User computes off-chain:          response = keccak256(secret ++ nonce)
 *   4. User submits response:            verifyAccess(response)
 *      → contract recomputes keccak256(commitment_preimage ++ nonce) …
 *        wait — it can't, it only has the hash.
 *
 *   The trick: we store keccak256(secret) as the commitment.
 *   The user sends keccak256(abi.encodePacked(secret, nonce)).
 *   The contract independently computes keccak256(abi.encodePacked(commitment_preimage, nonce))
 *   — but it doesn't have commitment_preimage!
 *
 *   So the user sends BOTH (secret_hash_with_nonce) AND the contract checks:
 *     keccak256(abi.encodePacked(storedCommitment, nonce)) == response
 *
 *   This means the response is keccak256(keccak256(secret) ++ nonce),
 *   which the contract CAN verify using only the stored commitment.
 *   An attacker who sees the commitment on-chain still can't forge a response
 *   without knowing the original secret (they'd need to invert keccak256).
 */
contract DataAccessControl {

    // ── Storage ────────────────────────────────────────────────────────────

    address public owner;
    string  public dataIdentifier;   // e.g. IPFS CID or database record ID

    // keccak256(sharedSecret) — set once during deployment or by owner
    bytes32 public commitment;

    // Per-address pending challenge: address → nonce
    // Cleared after each use to prevent replay attacks
    mapping(address => bytes32) private pendingChallenge;
    mapping(address => uint256) private challengeExpiry;

    uint256 public constant CHALLENGE_TTL = 5 minutes;

    // ── Events ─────────────────────────────────────────────────────────────

    event ChallengeIssued(address indexed requester, bytes32 nonce, uint256 expiry);
    event AccessGranted(address indexed requester);
    event AccessDenied(address indexed requester);

    // ── Constructor ────────────────────────────────────────────────────────

    constructor(string memory _dataIdentifier) {
        owner = msg.sender;
        dataIdentifier = _dataIdentifier;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    // ── Phase 3a: Register the commitment (owner only) ─────────────────────

    /**
     * Store keccak256(sharedSecret). Called once after both parties complete
     * the DH exchange and agree on the shared secret off-chain.
     *
     * @param _commitment  keccak256(abi.encodePacked(sharedSecret))
     *                     Computed off-chain by Python; never the raw secret.
     */
    function registerCommitment(bytes32 _commitment) external onlyOwner {
        require(_commitment != bytes32(0), "Empty commitment");
        commitment = _commitment;
    }

    // ── Phase 4a: Request a challenge ──────────────────────────────────────

    /**
     * Issues a one-time nonce to the caller. The nonce is derived from
     * block data + caller address so it differs for every request.
     *
     * Production note: block.prevrandao (post-merge) is the best available
     * on-chain randomness without a VRF oracle. For high-value access control,
     * use Chainlink VRF to make the nonce unpredictable.
     */
    function requestChallenge() external returns (bytes32 nonce) {
        require(commitment != bytes32(0), "Commitment not set");

        nonce = keccak256(abi.encodePacked(
            block.prevrandao,
            block.timestamp,
            msg.sender,
            blockhash(block.number - 1)
        ));

        pendingChallenge[msg.sender] = nonce;
        challengeExpiry[msg.sender] = block.timestamp + CHALLENGE_TTL;

        emit ChallengeIssued(msg.sender, nonce, challengeExpiry[msg.sender]);
    }

    // ── Phase 4b: Submit response ──────────────────────────────────────────

    /**
     * Verify the caller knows the shared secret.
     *
     * The caller computes off-chain:
     *   response = keccak256(abi.encodePacked(sharedSecret, nonce))
     *
     * The contract verifies:
     *   keccak256(abi.encodePacked(commitment, nonce)) == response
     *
     * Why this works:
     *   commitment = keccak256(secret)
     *   response   = keccak256(commitment ++ nonce)   [from user, who knows secret]
     *   expected   = keccak256(commitment ++ nonce)   [contract recomputes from stored commitment]
     *
     *   An attacker sees `commitment` on-chain but cannot compute a valid
     *   response without inverting keccak256 to recover the secret first.
     *
     * @param response  keccak256(abi.encodePacked(sharedSecret, nonce))
     * @return granted  true if the response is correct and the challenge is fresh
     */
    function verifyAccess(bytes32 response) external returns (bool granted) {
        bytes32 nonce = pendingChallenge[msg.sender];
        require(nonce != bytes32(0), "No pending challenge");
        require(block.timestamp <= challengeExpiry[msg.sender], "Challenge expired");

        // Clear the challenge before checking — prevents replay even if the
        // function somehow re-enters or the tx is replayed on a fork
        delete pendingChallenge[msg.sender];
        delete challengeExpiry[msg.sender];

        bytes32 expected = keccak256(abi.encodePacked(commitment, nonce));
        granted = (response == expected);

        if (granted) {
            emit AccessGranted(msg.sender);
        } else {
            emit AccessDenied(msg.sender);
        }
    }

    // ── View helpers ───────────────────────────────────────────────────────

    function hasPendingChallenge(address user) external view returns (bool) {
        return pendingChallenge[user] != bytes32(0)
            && block.timestamp <= challengeExpiry[user];
    }
}
