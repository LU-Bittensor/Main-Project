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

