from unittest.mock import MagicMock, patch

from services import redis_client


def test_clients_are_reused_per_url():
    redis_client._client_for_url.cache_clear()
    first_client = MagicMock()
    second_client = MagicMock()

    try:
        with patch.object(
            redis_client.redis,
            "from_url",
            side_effect=[first_client, second_client],
        ) as from_url:
            assert redis_client.get_redis_client("redis://localhost:6379/15") is first_client
            assert redis_client.get_redis_client("redis://localhost:6379/15") is first_client
            assert redis_client.get_redis_client("redis://localhost:6379/14") is second_client

            assert from_url.call_count == 2
            from_url.assert_any_call("redis://localhost:6379/15", decode_responses=True)
            from_url.assert_any_call("redis://localhost:6379/14", decode_responses=True)
    finally:
        redis_client._client_for_url.cache_clear()
