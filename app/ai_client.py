import logging
from dataclasses import dataclass
from openai import OpenAI
import os

log = logging.getLogger(__name__)


class AIClientError(Exception):
    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class ChatResult:
    keyworld: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class AIClient:
    def __init__(self, client: OpenAI):
        self._client = client

    @classmethod
    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=os.getenv("OPENAI_API_KEY"),
                base_url=os.getenv("OPENAI_API_BASE"),
            )
        return self._client

    @classmethod
    def _prepare(title: str, content: str):
        prompt = f"""
        You are an expert e-commerce advertising analyst. Your task is to read the ad content (title + body + hashtags) below and return EXACTLY ONE keyword that most accurately describes the product being advertised.

        MANDATORY RULES:
        1. The keyword must describe the EXACT PRODUCT TYPE + its MOST STANDOUT FEATURE (USP) — not emotions, not generic benefits.
        2. Prioritize the feature that is repeated multiple times or appears in BOTH the description AND the hashtags (strongest signal).
        3. DO NOT use generic, non-distinctive terms such as: "kitchen gadget", "home essentials", "must-have", "trending", "life hack", "cooking", or emotionally/marketing-driven hashtags (e.g., "freedom", "satisfy cravings").
        4. DO NOT use brand names, shop names, or URL slugs.
        5. The keyword must be a short noun phrase, 3-6 words, in English, formatted so it can be used directly to search on ad platforms or e-commerce marketplaces (Google, Facebook Ads Library, Amazon, etc.).
        6. If the product has multiple features, choose only the ONE most differentiating feature (the point that sets this product apart from typical products of the same category on the market).
        7. Return ONLY 1 line containing the keyword — no explanation, no markdown, no quotation marks.

        EXAMPLE:
        Input: ad content about a deep fryer with an automatic oil filter, compact size for one person, stainless steel material, designed for dorm room use.
        Output: mini deep fryer with oil filter

        Now analyze the following content:

        ---
        TITLE: {title}
        CONTENT: {content}
        ---

        Return the keyword:
        """
        return prompt

    def _raise_http(
        self,
        status_code: int,
    ) -> None:
        if status_code in (401, 403):
            raise AIClientError(
                f"AI auth failed ({status_code}).",
                status_code=401,
            )

    async def analyize_keyword(self, prompt: str) -> ChatResult:
        client = self._get_client()
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL"),
            instructions="You are an expert e-commerce advertising analyst.",
            input=prompt,
        )
        return ChatResult(response.choices[0]["message"]["content"])
