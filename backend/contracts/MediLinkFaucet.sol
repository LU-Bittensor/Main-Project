// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * MediLinkFaucet
 * ==============
 * Ops deposits native ETH/TAO-EVM. An authorized operator drips small amounts
 * to enrolled user H160s so they can pay AccessRegistry gas.
 *
 * Users with zero balance cannot self-claim; the API operator calls drip(to).
 */
contract MediLinkFaucet {
    address public owner;
    address public operator;

    uint256 public dripAmount;
    uint256 public minBalance;
    uint256 public cooldown;

    mapping(address => uint256) public lastDripTime;

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event OperatorUpdated(address indexed previousOperator, address indexed newOperator);
    event DripAmountUpdated(uint256 amount);
    event MinBalanceUpdated(uint256 amount);
    event CooldownUpdated(uint256 seconds_);
    event Deposited(address indexed from, uint256 amount);
    event Dripped(address indexed to, uint256 amount);
    event Withdrawn(address indexed to, uint256 amount);

    error NotOwner();
    error NotOperator();
    error ZeroAddress();
    error CooldownActive();
    error BalanceAboveThreshold();
    error InsufficientFaucetBalance();
    error TransferFailed();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    modifier onlyOperator() {
        if (msg.sender != operator) revert NotOperator();
        _;
    }

    constructor(uint256 dripAmount_, uint256 minBalance_, uint256 cooldown_) {
        owner = msg.sender;
        operator = msg.sender;
        dripAmount = dripAmount_;
        minBalance = minBalance_;
        cooldown = cooldown_;
        emit OwnershipTransferred(address(0), msg.sender);
        emit OperatorUpdated(address(0), msg.sender);
        emit DripAmountUpdated(dripAmount_);
        emit MinBalanceUpdated(minBalance_);
        emit CooldownUpdated(cooldown_);
    }

    receive() external payable {
        emit Deposited(msg.sender, msg.value);
    }

    function deposit() external payable {
        emit Deposited(msg.sender, msg.value);
    }

    function drip(address to) external onlyOperator {
        if (to == address(0)) revert ZeroAddress();
        if (to.balance >= minBalance) revert BalanceAboveThreshold();
        if (block.timestamp < lastDripTime[to] + cooldown) revert CooldownActive();
        if (address(this).balance < dripAmount) revert InsufficientFaucetBalance();

        lastDripTime[to] = block.timestamp;
        (bool ok, ) = to.call{value: dripAmount}("");
        if (!ok) revert TransferFailed();
        emit Dripped(to, dripAmount);
    }

    function setDripAmount(uint256 amount) external onlyOwner {
        dripAmount = amount;
        emit DripAmountUpdated(amount);
    }

    function setMinBalance(uint256 amount) external onlyOwner {
        minBalance = amount;
        emit MinBalanceUpdated(amount);
    }

    function setCooldown(uint256 seconds_) external onlyOwner {
        cooldown = seconds_;
        emit CooldownUpdated(seconds_);
    }

    function setOperator(address newOperator) external onlyOwner {
        if (newOperator == address(0)) revert ZeroAddress();
        emit OperatorUpdated(operator, newOperator);
        operator = newOperator;
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function withdraw(address to, uint256 amount) external onlyOwner {
        if (to == address(0)) revert ZeroAddress();
        (bool ok, ) = to.call{value: amount}("");
        if (!ok) revert TransferFailed();
        emit Withdrawn(to, amount);
    }
}
