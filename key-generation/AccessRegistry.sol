// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * AccessRegistry (multi-grantee)
 * ==============================
 * One contract for many EHR resources. Validators poll ONE address.
 *
 * - registerResource: metadata only (dataHash / dataCid); one resourceId = one record
 * - grantAccess / revokeAccess: per-(resourceId, grantee) commitment = keccak256(sharedSecret)
 * - requestChallenge / verifyAccess: prove knowledge of THAT grantee's secret
 *
 * Off-chain: X25519 DH per owner–grantee pair; DEK wraps live on Hippius sidecars.
 */
contract AccessRegistry {
    struct Resource {
        address owner;
        bytes32 dataHash;
        string dataCid;
        bool exists;
    }

    mapping(bytes32 => Resource) public resources;
    // resourceId => grantee => keccak256(sharedSecret)
    mapping(bytes32 => mapping(address => bytes32)) public commitments;

    mapping(bytes32 => mapping(address => bytes32)) private pendingChallenge;
    mapping(bytes32 => mapping(address => uint256)) private challengeExpiry;

    uint256 public constant CHALLENGE_TTL = 5 minutes;

    event ResourceRegistered(
        bytes32 resourceId,
        address owner,
        bytes32 dataHash,
        string dataCid
    );

    event AccessGrantSet(
        bytes32 indexed resourceId,
        address indexed grantee,
        bytes32 dataHash
    );

    event AccessRevoked(
        bytes32 indexed resourceId,
        address indexed grantee
    );

    event ChallengeIssued(
        bytes32 indexed resourceId,
        address indexed requester,
        bytes32 nonce,
        uint256 expiry
    );

    event AccessGranted(
        bytes32 indexed resourceId,
        address indexed requester,
        bytes32 dataHash,
        uint256 grantedAt
    );

    event AccessDenied(
        bytes32 indexed resourceId,
        address indexed requester,
        uint256 deniedAt
    );

    modifier onlyResourceOwner(bytes32 resourceId) {
        require(resources[resourceId].exists, "Unknown resource");
        require(msg.sender == resources[resourceId].owner, "Not resource owner");
        _;
    }

    /**
     * Register or update resource metadata. First caller becomes owner.
     * Does NOT set any grantee commitment — use grantAccess.
     */
    function registerResource(
        bytes32 resourceId,
        bytes32 dataHash,
        string calldata dataCid
    ) external {
        require(resourceId != bytes32(0), "Empty resourceId");
        require(dataHash != bytes32(0), "Empty dataHash");

        Resource storage r = resources[resourceId];
        if (r.exists) {
            require(msg.sender == r.owner, "Not resource owner");
        } else {
            r.owner = msg.sender;
            r.exists = true;
        }

        r.dataHash = dataHash;
        r.dataCid = dataCid;

        emit ResourceRegistered(resourceId, r.owner, dataHash, dataCid);
    }

    /**
     * Owner authorizes grantee with keccak256(sharedSecret) from off-chain DH.
     */
    function grantAccess(
        bytes32 resourceId,
        address grantee,
        bytes32 commitment
    ) external onlyResourceOwner(resourceId) {
        require(grantee != address(0), "Empty grantee");
        require(commitment != bytes32(0), "Empty commitment");

        commitments[resourceId][grantee] = commitment;
        emit AccessGrantSet(resourceId, grantee, resources[resourceId].dataHash);
    }

    function revokeAccess(bytes32 resourceId, address grantee) external onlyResourceOwner(resourceId) {
        require(grantee != address(0), "Empty grantee");
        delete commitments[resourceId][grantee];
        delete pendingChallenge[resourceId][grantee];
        delete challengeExpiry[resourceId][grantee];
        emit AccessRevoked(resourceId, grantee);
    }

    function requestChallenge(bytes32 resourceId) external returns (bytes32 nonce) {
        require(resources[resourceId].exists, "Unknown resource");
        require(commitments[resourceId][msg.sender] != bytes32(0), "Not granted");

        nonce = keccak256(
            abi.encodePacked(
                block.prevrandao,
                block.timestamp,
                msg.sender,
                resourceId,
                blockhash(block.number - 1)
            )
        );

        pendingChallenge[resourceId][msg.sender] = nonce;
        challengeExpiry[resourceId][msg.sender] = block.timestamp + CHALLENGE_TTL;

        emit ChallengeIssued(
            resourceId,
            msg.sender,
            nonce,
            challengeExpiry[resourceId][msg.sender]
        );
    }

    /**
     * response = keccak256(abi.encodePacked(commitment, nonce))
     * commitment = keccak256(sharedSecret) for THIS msg.sender grantee.
     */
    function verifyAccess(bytes32 resourceId, bytes32 response) external returns (bool granted) {
        require(resources[resourceId].exists, "Unknown resource");

        bytes32 nonce = pendingChallenge[resourceId][msg.sender];
        require(nonce != bytes32(0), "No pending challenge");
        require(block.timestamp <= challengeExpiry[resourceId][msg.sender], "Challenge expired");

        bytes32 commitment = commitments[resourceId][msg.sender];
        require(commitment != bytes32(0), "Not granted");

        delete pendingChallenge[resourceId][msg.sender];
        delete challengeExpiry[resourceId][msg.sender];

        bytes32 expected = keccak256(abi.encodePacked(commitment, nonce));
        granted = (response == expected);

        if (granted) {
            emit AccessGranted(
                resourceId,
                msg.sender,
                resources[resourceId].dataHash,
                block.timestamp
            );
        } else {
            emit AccessDenied(resourceId, msg.sender, block.timestamp);
        }
    }

    function hasPendingChallenge(bytes32 resourceId, address user) external view returns (bool) {
        return pendingChallenge[resourceId][user] != bytes32(0)
            && block.timestamp <= challengeExpiry[resourceId][user];
    }

    function hasGrant(bytes32 resourceId, address grantee) external view returns (bool) {
        return commitments[resourceId][grantee] != bytes32(0);
    }
}
