from supportops_core.secrets import LocalEnvelopeSecretProvider, masked_secret_hint


def test_local_secret_provider_round_trip_and_no_plaintext() -> None:
    provider = LocalEnvelopeSecretProvider("a-secret-encryption-key-longer-than-32")
    plaintext = "sk-sensitive-value-1234567890"
    encrypted = provider.seal(plaintext)

    assert plaintext not in encrypted
    assert provider.open(encrypted) == plaintext
    assert masked_secret_hint(plaintext) == "••••7890"
