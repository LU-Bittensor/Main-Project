import getpass
from pathlib import Path
import subprocess
import sys
import re

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
import secrets

SCRIPT_PATH = Path(__file__).parent / "linuxSetup.sh"

def generate_keys(keyname: str, passphrase: str, overwrite: bool = False):

    overwrite_answer = "y" if overwrite else "n"
    stdin_input = f"{keyname}\n{passphrase}\n{passphrase}\n{overwrite_answer}\n"

    result = subprocess.run(["bash", str(SCRIPT_PATH)], input=stdin_input, capture_output=True, text=True, env={"PATH":"/usr/bin:/bin:/usr/local/bin"})

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("linuxSetup.sh failed with code {result.returncode}")
    
    print(result.stdout)

    private_match = re.search(r"Private Key:\s+(\S+\.pem)", result.stdout)
    public_match = re.search(r"Public Key:\s+(\S+\.pem)", result.stdout)

    if not private_match or not public_match:
        raise RuntimeError(f"Failed to parse key paths from linuxSetup.sh output.\nOutput was:\n{result.stdout}")
    
    return Path(private_match.group(1)), Path(public_match.group(1))

class DHUser:
    def __init__(self, name: str, private_key_path: Path, passphrase: str):
        self.name = name
        self.shared_secret: bytes | None = None

        private_pem = private_key_path.read_bytes()
        public_path = private_key_path.with_name(private_key_path.name.replace("private", "public"))
        public_pem = public_path.read_bytes()

        self.private_key = serialization.load_pem_private_key(private_pem, password=passphrase.encode(), backend=default_backend())
        self.public_key = serialization.load_pem_public_key(public_pem, backend=default_backend())

    