import os
import tempfile

from cryptography.fernet import Fernet

# set before app modules are imported; fresh DB per test session
os.environ.update(
    JWT_SECRET="s",
    CREDENTIAL_KEY=Fernet.generate_key().decode(),
    DB_PATH=os.path.join(tempfile.mkdtemp(), "t.db"),
)
