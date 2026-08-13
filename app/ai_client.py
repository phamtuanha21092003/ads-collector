import logging
from dataclasses import dataclass
from openai import OpenAI, RateLimitError
from app.config import Settings
import json

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

    def _prepare_group_keyword(self, keywords: list[str], existing_keywords: list[str]):
        existing_keywords_bullets = "\n".join(f"- {kw}" for kw in existing_keywords)
        keywords_bullets = "\n".join(f"- {kw}" for kw in keywords)
        prompt = f""" 
        You are an expert e-commerce advertising data analyst. You will receive a list of keywords extracted from multiple ads within the same niche, along with a list of keywords that ALREADY EXIST in the system (from previous crawls/levels). Your task is to GROUP the new keywords that share the same meaning or describe the same product type/feature into clusters, select one representative keyword for each cluster, and EXCLUDE any keyword (or cluster) that has the same meaning as one already present in existing_keywords.

        MANDATORY RULES:

        1. Two keywords are considered THE SAME GROUP if they describe the same product type and the same standout feature (USP), even if worded differently (synonyms, abbreviations, different word order, singular/plural forms). Example: "mini deep fryer with oil filter", "compact oil-filtering fryer", "small fryer with automatic oil filter" → SAME GROUP.

        2. Two keywords describing the same product type but with a DIFFERENT USP/main feature must NOT be merged together.
        Example: "mini deep fryer with oil filter" and "stainless steel deep fryer" → DIFFERENT GROUPS (different USP: oil filtering vs. material).

        3. For each group, select one representative keyword based on the following criteria: - Shortest, clearest phrasing - No spelling errors, no redundant/repeated words - Prioritize the keyword with the highest frequency within the group (if frequency data is available)

        4. Discard keywords that are too generic and do not describe a distinctive product feature (e.g., "kitchen gadget", "home essentials", "trending item") — place these in a separate "generic - discarded" group; do not count them as a product group.

        5. CHECK AGAINST existing_keywords: if a new keyword (or an entire group of new keywords) has the SAME meaning/same product type + same USP as any keyword already listed in existing_keywords, DO NOT create a new group for it. Instead, place it in a separate "duplicate_existing" list, along with the matching existing keyword it duplicates. Only keywords/groups that are genuinely NEW (not semantically covered by existing_keywords) should appear in "groups".

        6. Do not invent any keyword that is not present in the input list.

        OUTPUT FORMAT — return JSON in the following structure, with no explanation outside the JSON:
        {{"groups": [{{"representative_keyword": "mini deep fryer with oil filter","members": [  "mini deep fryer with oil filter",  "compact oil-filtering fryer",  "small fryer with automatic oil filter"],"count": 3}}], "duplicate_existing": [{{"new_keyword": "small fryer with oil filtering function","matched_existing_keyword": "mini deep fryer with oil filter"}}],"discarded_generic": ["kitchen gadget","home essentials"]}}

        EXISTING KEYWORDS (already in the system — do not duplicate these):
        {existing_keywords_bullets}

        KEYWORD LIST TO GROUP:
        {keywords_bullets} """
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

        try:
            response = client.responses.create(
                model=self.settings.openai_model,
                instructions="You are an expert e-commerce advertising analyst.",
                input=prompt,
            )
        except RateLimitError:
            log.warning("AI rate limit reached; skipping keyword analysis")
            return ""
        return response.choices[0]["message"]["content"]

    def _llm_group_keywords(
        self, keywords: list[str], existing_keywords: list[str]
    ) -> list[str]:
        client = self._get_client()
        prompt = self._prepare_group_keyword(
            keywords=keywords, existing_keywords=existing_keywords
        )
        list_key = []
        try:
            response = client.responses.create(
                model=self.settings.openai_model,
                instructions="You are an expert e-commerce advertising analyst.",
                input=prompt,
            )
        except RateLimitError:
            log.warning("AI rate limit reached; skipping keyword grouping")
            return []
        content = json.loads(response.choices[0]["message"]["content"])
        for g in content["groups"]:
            list_key.append(g["representative_keyword"])
        return list_key
