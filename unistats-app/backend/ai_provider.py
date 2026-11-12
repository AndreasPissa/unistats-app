"""Hosted Chat Completions transport. Credentials never leave the backend UI boundary."""
import math
import os
from urllib.parse import urlsplit

import httpx


class AIUnavailable(Exception):
    pass


class HostedProvider:
    name = "openai_compatible"

    def __init__(self):
        self.enabled = os.getenv("UNISTATS_AI_ENABLED", "true").lower() not in {"false", "0", "no"}
        self.model = os.getenv("UNISTATS_AI_MODEL", "").strip()
        self.url = os.getenv("UNISTATS_AI_BASE_URL", "").strip().rstrip("/")
        self._key = os.getenv("UNISTATS_AI_API_KEY", "").strip()
        self.response_format = os.getenv("UNISTATS_AI_RESPONSE_FORMAT", "json_schema").strip()
        self.token_parameter = os.getenv("UNISTATS_AI_TOKEN_PARAMETER", "max_completion_tokens").strip()
        self.timeout = 90.0
        self.max_tokens = 2048
        self.problem = ""
        missing = [name for name, value in [("UNISTATS_AI_API_KEY", self._key),
                   ("UNISTATS_AI_BASE_URL", self.url), ("UNISTATS_AI_MODEL", self.model)] if not value]
        try:
            if missing:
                raise ValueError("AI setup pending. Add " + ", ".join(missing) + " to the backend .env file and restart the app.")
            parsed = urlsplit(self.url)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or any(c.isspace() for c in self.url)
                    or parsed.path.endswith("/chat/completions")):
                raise ValueError("UNISTATS_AI_BASE_URL must be an HTTPS API base URL, without credentials, query parameters or /chat/completions.")
            # Force port validation without including user-supplied values in errors.
            try:
                parsed.port
                httpx.URL(self.url)
            except (ValueError, httpx.InvalidURL):
                raise ValueError("UNISTATS_AI_BASE_URL is invalid.") from None
            if not self._key.isascii() or any(c.isspace() for c in self._key):
                raise ValueError("UNISTATS_AI_API_KEY must contain a valid API token without whitespace.")
            if self.response_format not in {"json_schema", "json_object"}:
                raise ValueError("UNISTATS_AI_RESPONSE_FORMAT must be json_schema or json_object.")
            if self.token_parameter not in {"max_completion_tokens", "max_tokens"}:
                raise ValueError("UNISTATS_AI_TOKEN_PARAMETER must be max_completion_tokens or max_tokens.")
            try:
                timeout = float(os.getenv("UNISTATS_AI_TIMEOUT", "90"))
                tokens = int(os.getenv("UNISTATS_AI_MAX_TOKENS", "2048"))
            except ValueError:
                raise ValueError("AI timeout and maximum tokens must be valid numbers.") from None
            if not math.isfinite(timeout) or not 5 <= timeout <= 300 or not 128 <= tokens <= 8192:
                raise ValueError("AI timeout must be 5-300 seconds and maximum tokens 128-8192.")
            self.timeout, self.max_tokens = timeout, tokens
        except ValueError as exc:
            # A configuration error disables only AI; the explorer still starts.
            self.problem = str(exc) if "UNISTATS_" in str(exc) or str(exc).startswith("AI ") else "AI provider configuration is invalid. Check the backend .env file."
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10),
                                        trust_env=False, follow_redirects=False)

    def ensure_ready(self):
        if not self.enabled:
            raise AIUnavailable("AI is disabled by UNISTATS_AI_ENABLED.")
        if self.problem:
            raise AIUnavailable(self.problem)

    def status(self):
        return {"enabled": self.enabled, "ready": self.enabled and not self.problem,
                "provider": self.name, "model": self.model, "executionSource": "live",
                "connectionVerified": False,
                "message": ("AI is disabled by UNISTATS_AI_ENABLED." if not self.enabled else self.problem
                            or "API configured · connection checked when you ask · live data required")}

    async def close(self):
        await self.client.aclose()

    async def complete(self, messages, schema):
        self.ensure_ready()
        output = {"type": self.response_format}
        if self.response_format == "json_schema":
            output["json_schema"] = {"name": "unistats_query_plan", "strict": True, "schema": schema}
        body = {"model": self.model, "messages": messages, "response_format": output,
                "stream": False, self.token_parameter: self.max_tokens}
        try:
            response = await self.client.post(self.url + "/chat/completions", json=body,
                                             headers={"Authorization": "Bearer " + self._key})
            response.raise_for_status()
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            if message.get("refusal") or choice.get("finish_reason") == "content_filter":
                raise AIUnavailable("The AI provider declined this question. Please rephrase it. No database query was executed.")
            if choice.get("finish_reason") == "length":
                raise AIUnavailable("The AI plan exceeded its output limit. Try a simpler question or increase UNISTATS_AI_MAX_TOKENS.")
            if choice.get("finish_reason") != "stop" or message.get("tool_calls") or message.get("function_call"):
                raise ValueError("Incomplete or unsupported response")
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Empty response")
            return content
        except httpx.TimeoutException as exc:
            raise AIUnavailable("The AI provider timed out. No database query was executed. Please try again.") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                message = "The AI provider rejected authentication or access. Check the backend API key and model permissions."
            elif status == 429:
                message = "The AI provider rate or usage limit was reached. Please try later or check the provider account."
            elif status in {400, 404, 422}:
                message = "The AI provider rejected the request. Check the API base URL, model, response format and token parameter."
            else:
                message = "The AI provider is unavailable. Please try again later."
            # Never forward provider bodies, request headers or URLs to the browser.
            raise AIUnavailable(message) from exc
        except httpx.HTTPError as exc:
            raise AIUnavailable("Could not connect to the AI provider. Check its availability and the backend configuration.") from exc
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise AIUnavailable("The AI provider returned an invalid response. No database query was executed.") from exc
