"""Test-only telephony webhook signing secret.

This value exists only so the test suite can sign and verify webhooks. It is
never read by application code, is not a credential, and is not used by any
deployment. Production and staging must supply TELEPHONY_WEBHOOK_SECRET; the
verifier fails closed when it is missing.
"""

TEST_WEBHOOK_SECRET = "test-only-telephony-webhook-signing-key"
