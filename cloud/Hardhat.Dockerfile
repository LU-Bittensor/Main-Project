# Context: Main-Project
FROM node:22-bookworm-slim
WORKDIR /chain
COPY backend/key-generation/testContractDeployment/package*.json ./
RUN npm ci
COPY backend/key-generation/testContractDeployment/hardhat.config.* ./
COPY backend/contracts/ ./contracts/
RUN npx hardhat compile
COPY cloud/hardhat-sidecar.cjs ./
USER node
CMD ["node", "hardhat-sidecar.cjs"]
