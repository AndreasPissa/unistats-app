"""Hosted adapter tests use only fake credentials and in-memory HTTP responses."""
import json
import os
import unittest
from unittest.mock import patch

import httpx

from backend.ai_provider import HostedProvider, AIUnavailable


CONFIG = {
    'UNISTATS_AI_API_KEY': 'test-private-secret',
    'UNISTATS_AI_BASE_URL': 'https://provider.example/custom/v1/',
    'UNISTATS_AI_MODEL': 'test-model', 'UNISTATS_AI_ENABLED': 'true',
    'UNISTATS_AI_RESPONSE_FORMAT': 'json_schema',
    'UNISTATS_AI_TOKEN_PARAMETER': 'max_completion_tokens',
    'UNISTATS_AI_MAX_TOKENS': '2048', 'UNISTATS_AI_TIMEOUT': '90',
}


class HostedProviderTests(unittest.IsolatedAsyncioTestCase):
    async def provider(self, **changes):
        with patch.dict(os.environ, {**CONFIG, **changes}):
            provider = HostedProvider()
        self.addAsyncCleanup(provider.close)
        return provider

    async def transport(self, provider, handler):
        await provider.client.aclose()
        provider.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_json_object_and_legacy_token_parameter(self):
        provider = await self.provider(UNISTATS_AI_RESPONSE_FORMAT='json_object', UNISTATS_AI_TOKEN_PARAMETER='max_tokens')
        def respond(request):
            self.assertEqual(str(request.url), 'https://provider.example/custom/v1/chat/completions')
            body = json.loads(request.content)
            self.assertEqual(body['response_format'], {'type': 'json_object'})
            self.assertEqual(body['max_tokens'], 2048)
            self.assertNotIn('max_completion_tokens', body)
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"mode":"clarify"}'}}]})
        await self.transport(provider, respond)
        self.assertEqual(await provider.complete([], {}), '{"mode":"clarify"}')

    async def test_missing_configuration_and_disabled_never_send(self):
        for changes in [{'UNISTATS_AI_API_KEY': ''}, {'UNISTATS_AI_BASE_URL': ''}, {'UNISTATS_AI_MODEL': ''}, {'UNISTATS_AI_ENABLED': 'false'}]:
            with self.subTest(changes=changes):
                provider = await self.provider(**changes)
                await self.transport(provider, lambda _: self.fail('Unexpected network call'))
                self.assertFalse(provider.status()['ready'])
                with self.assertRaises(AIUnavailable):
                    await provider.complete([], {})

    async def test_invalid_configuration_does_not_crash_app_or_disclose_values(self):
        for field, values in {
            'UNISTATS_AI_BASE_URL': ['http://provider.example/v1', 'https://user:SECRET@provider.example/v1', 'https://provider.example/v1?key=SECRET', 'https://provider.example/v1/chat/completions', 'https://provider.example:bad/v1', 'https://[bad'],
            'UNISTATS_AI_TIMEOUT': ['nan', 'inf', '-1', 'secret-value'],
            'UNISTATS_AI_MAX_TOKENS': ['0', '999999', 'secret-value'],
            'UNISTATS_AI_RESPONSE_FORMAT': ['secret-value'],
            'UNISTATS_AI_TOKEN_PARAMETER': ['secret-value'],
            'UNISTATS_AI_API_KEY': ['private\nsecret'],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    provider = await self.provider(**{field: value})
                    self.assertFalse(provider.status()['ready'])
                    self.assertNotIn('SECRET', json.dumps(provider.status()))
                    self.assertNotIn('secret-value', json.dumps(provider.status()))

    async def test_status_is_configuration_only_without_network_or_secret(self):
        provider = await self.provider()
        await self.transport(provider, lambda _: self.fail('Status must not contact the provider'))
        self.assertTrue(provider.status()['ready'])
        self.assertFalse(provider.status()['connectionVerified'])
        self.assertNotIn(CONFIG['UNISTATS_AI_API_KEY'], json.dumps(provider.status()))

    async def test_provider_errors_are_sanitized_and_not_retried(self):
        for status, fragment in [(401, 'authentication'), (403, 'authentication'), (429, 'limit'), (400, 'rejected'), (404, 'rejected'), (422, 'rejected'), (500, 'unavailable'), (302, 'unavailable')]:
            with self.subTest(status=status):
                provider = await self.provider()
                calls = []
                def respond(request):
                    calls.append(request)
                    return httpx.Response(status, json={'error': CONFIG['UNISTATS_AI_API_KEY']}, headers={'location': 'https://elsewhere.example/'})
                await self.transport(provider, respond)
                with self.assertRaises(AIUnavailable) as caught:
                    await provider.complete([], {})
                self.assertIn(fragment, str(caught.exception))
                self.assertNotIn(CONFIG['UNISTATS_AI_API_KEY'], str(caught.exception))
                self.assertEqual(len(calls), 1)

    async def test_timeouts_and_network_failures(self):
        for exception, fragment in [(httpx.ReadTimeout, 'timed out'), (httpx.ConnectError, 'connect')]:
            provider = await self.provider()
            def respond(request):
                raise exception('private diagnostic', request=request)
            await self.transport(provider, respond)
            with self.assertRaises(AIUnavailable) as caught:
                await provider.complete([], {})
            self.assertIn(fragment, str(caught.exception))
            self.assertNotIn('private diagnostic', str(caught.exception))

    async def test_refusal_incomplete_and_malformed_responses(self):
        invalid = [None, [], {}, {'choices': []}, {'choices': [None]},
                   {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
                   {'choices': [{'finish_reason': 'content_filter', 'message': {}}]},
                   {'choices': [{'finish_reason': 'stop', 'message': {'refusal': 'No'}}]},
                   {'choices': [{'finish_reason': 'stop', 'message': {'content': None}}]},
                   {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}', 'tool_calls': [{}]}}]}]
        provider = await self.provider()
        for payload in invalid:
            with self.subTest(payload=payload):
                await self.transport(provider, lambda _: httpx.Response(200, json=payload))
                with self.assertRaises(AIUnavailable):
                    await provider.complete([], {})


if __name__ == '__main__':
    unittest.main()
