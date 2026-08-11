import logging
from dataclasses import dataclass
from openai import OpenAI
import os
from app.config import Settings

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
    def __init__(self, settings: Settings, client: OpenAI | None = None):
        self._client = client
        self.settings = settings

    def _get_client(self) -> OpenAI:
        if self._client is None:
            self._client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_api_base,
                admin_api_key=self.settings.openai_api_key,
            )
        return self._client

    def _prepare_keyword(self, title: str, content: str):
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

    def _prepare_group_keyword(seft, keywords: list[str]):
        print(keywords)
        prompt = f""" You are an expert e-commerce advertising data analyst.
        You will receive a list of keywords extracted from multiple ads within the same niche.
        Your task is to GROUP keywords that share the same meaning or describe the same product type/feature into clusters, then select one representative keyword that most accurately represents each cluster.
        MANDATORY RULES:
        1. Two keywords are considered THE SAME GROUP if they describe the same product type and the same standout feature (USP), even if worded differently (synonyms, abbreviations, different word order, singular/plural forms). Example: "mini deep fryer with oil filter", "compact oil-filtering fryer", "small fryer with automatic oil filter" → SAME GROUP. 
        2. Two keywords describing the same product type but with a DIFFERENT USP/main feature must NOT be merged together. 
        Example: "mini deep fryer with oil filter" and "stainless steel deep fryer" → DIFFERENT GROUPS (different USP: oil filtering vs. material). 
        3. For each group, select one representative keyword based on the following criteria: - Shortest, clearest phrasing - No spelling errors, no redundant/repeated words - Prioritize the keyword with the highest frequency within the group (if frequency data is available) 4. Discard keywords that are too generic and do not describe a distinctive product feature (e.g., "kitchen gadget", "home essentials", "trending item") — place these in a separate "generic - discarded" group; do not count them as a product group. 5. Do not invent any keyword that is not present in the input list. 
        OUTPUT FORMAT — return JSON in the following structure, with no explanation outside the JSON: 
        {{ "groups": [ {{ "representative_keyword": "mini deep fryer with oil filter", "members": [ "mini deep fryer with oil filter", "compact oil-filtering fryer", "small fryer with automatic oil filter" ], "count": 3 }} ], "discarded_generic": [ "kitchen gadget", "home essentials" ] }} 
        KEYWORD LIST TO GROUP: {chr(10).join(f"- {keyword}" for keyword in keywords)} """
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

    def _llm_analyze_keyword(self, title: str, content: str) -> str:
        client = self._get_client()
        prompt = self._prepare_keyword(title=title, content=content)

        response = client.responses.create(
            model=self.settings.openai_model,
            instructions="You are an expert e-commerce advertising analyst.",
            input=prompt,
        )
        return response.choices[0]["message"]["content"]

    def _llm_group_keywords(self, keywords: list[str]) -> str:
        client = self._get_client()
        prompt = self._prepare_group_keyword(keywords)

        response = client.responses.create(
            model=self.settings.openai_model,
            instructions="You are an expert e-commerce advertising analyst.",
            input=prompt,
        )
        return response.choices[0]["message"]["content"]
