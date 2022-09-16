from tests.data_for_tests.propagation_credentials import (
    CREDENTIALS,
    PLAINTEXT_LM_HASH,
    PLAINTEXT_NT_HASH,
    PLAINTEXT_PASSWORD,
    PLAINTEXT_PRIVATE_KEY,
)
from tests.unit_tests.monkey_island.cc.models.test_agent import AGENT_ID

from common.events import CredentialsStolenEvent

TEST_EVENT = CredentialsStolenEvent(stolen_credentials=CREDENTIALS, source=AGENT_ID)


def test_credentials_stolen_event_serialization_json():
    serialized_event = TEST_EVENT.json()
    assert PLAINTEXT_PASSWORD in serialized_event
    assert PLAINTEXT_LM_HASH in serialized_event
    assert PLAINTEXT_NT_HASH in serialized_event
    assert PLAINTEXT_PRIVATE_KEY in serialized_event


def test_credential_stolen_event_deserialization_json():
    serialized_event = TEST_EVENT.json()
    deserialized_event = CredentialsStolenEvent.parse_raw(serialized_event)
    assert deserialized_event == TEST_EVENT