// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * One contract per recipient-record pair. The owner may permanently deactivate
 * it with killContract(); no function can reactivate it. Existing challenges
 * cannot be verified after deactivation. Code and historical data remain.
 *
 * Prototype limitation: response = keccak256(commitment ++ nonce), with both
 * inputs public. This does not prove knowledge of the DH secret. API-level
 * authentication is still required; the original hash flow is retained here.
 */

// Imports OpenZeppelin's secure access control
import "@openzeppelin/contracts/access/Ownable.sol";

contract DataAccessControl is Ownable {

    bool public isKilled = false;

    // ── Storage ────────────────────────────────────────────────────────────

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
    event ContractKilled(address indexed owner);

    // ── Constructor ────────────────────────────────────────────────────────

    constructor(string memory _dataIdentifier) Ownable(msg.sender) {
        dataIdentifier = _dataIdentifier;
    }

    // Kill Functions/Modifiers

    modifier whenNotKilled() {
        require(!isKilled, "Contract is deactivated");
        _;
    }

    /// @notice Irreversibly disable access. Safe for the owner to call repeatedly.
    function killContract() external onlyOwner {
        if (isKilled) return;
        isKilled = true;
        emit ContractKilled(msg.sender);
    }

    // ── Phase 3a: Register the commitment (owner only) ─────────────────────

    /**
     * Store keccak256(sharedSecret). Called once after both parties complete
     * the DH exchange and agree on the shared secret off-chain.
     *
     * @param _commitment  keccak256(abi.encodePacked(sharedSecret))
     *                     Computed off-chain by Python; never the raw secret.
     */
    function registerCommitment(bytes32 _commitment) external onlyOwner whenNotKilled {
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
    function requestChallenge() external whenNotKilled returns (bytes32 nonce) {
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

    /// @notice Checks the original prototype response against the stored commitment.
    /// @dev Both commitment and nonce are public; this is not a secret-possession proof.
    function verifyAccess(bytes32 response) external whenNotKilled returns (bool granted) {
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
        return !isKilled && pendingChallenge[user] != bytes32(0)
            && block.timestamp <= challengeExpiry[user];
    }
}
