import os
import tempfile

from cryptography.fernet import Fernet

# set before app modules are imported; fresh DB per test session
os.environ.update(
    JWT_SECRET="s",
    CREDENTIAL_KEY=Fernet.generate_key().decode(),
    DB_PATH=os.path.join(tempfile.mkdtemp(), "t.db"),
    RATE_CHAT_PER_MIN="1000", RATE_CHAT_PER_HOUR="10000", RATE_CHAT_GLOBAL_PER_MIN="100000",  # tests hammer one vendor
    RATE_WA_PER_MIN="1000",
)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    from app import ratelimit
    ratelimit._hits.clear()
    yield
