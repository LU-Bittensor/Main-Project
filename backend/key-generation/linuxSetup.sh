set -euo pipefail

KEY_SIZE=4096

read -p "Enter the name for the key pair (default: medilink): " KEY_NAME
KEY_NAME=${KEY_NAME:-medilink}
PRIVATE_KEY="${KEY_NAME}_private_key.pem"
PUBLIC_KEY="${KEY_NAME}_public_key.pem"

if [ -t 1 ]; then
    GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
else
    GREEN=''; YELLOW=''; NC=''
fi

log() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

printf "Enter the passphrase for the private key: " >&2
IFS= read -r -s PASSPHRASE
printf "\n" >&2
printf "Confirm the passphrase: " >&2
IFS= read -r -s CONFIRM_PASSPHRASE
printf "\n" >&2

PASSPHRASE=$(printf '%s' "${PASSPHRASE}" | tr -d '\r\n')
CONFIRM_PASSPHRASE=$(printf '%s' "${CONFIRM_PASSPHRASE}" | tr -d '\r\n')

if [ "${PASSPHRASE}" != "${CONFIRM_PASSPHRASE}" ]; then
    echo "Passphrases do not match. Please try again."
    exit 1
fi

if [ -f "${PRIVATE_KEY}" ] || [ -f "${PUBLIC_KEY}" ]; then
    warn "One or both keys already exist. Do you want to overwrite them? (y/n)"
    read -r OVERWRITE
    if [ "${OVERWRITE}" != "y" ]; then
        echo "Exiting without generating keys."
        exit 0
    fi
fi

echo ""
log "Generating X25519 private key..."
openssl genpkey -algorithm X25519 -aes256 -pass fd:3 -out "${PRIVATE_KEY}" 2>/dev/null 3< <(printf "%s" "${PASSPHRASE}")
log "Private key encrypted with passphrase."

chmod 600 ${PRIVATE_KEY}
log "Private key saved: ${PRIVATE_KEY} (permissions set to 600)"

openssl pkey -pubout -in "${PRIVATE_KEY}" -passin fd:3 -out "${PUBLIC_KEY}" 2>/dev/null 3< <(printf "%s" "${PASSPHRASE}")
log "Public key saved: ${PUBLIC_KEY}"

echo ""
log "Key pair setup complete!"
echo "Private Key: $(pwd)/${PRIVATE_KEY} (encrypted with passphrase)"
echo "Public Key: $(pwd)/${PUBLIC_KEY}"
echo ""
warn "NOTICE: Keep your private key and passphrase secure. Losing them will result in losing access to encrypted data. "
warn "Never share your private key or passphrase with anyone. Never commit your passphrase or private key to version control or any public repository."