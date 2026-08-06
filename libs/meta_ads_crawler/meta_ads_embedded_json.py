import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait


def build_url(keyword: str, country: str) -> str:
    params = {
        "active_status": "all",
        "ad_type": "all",
        "country": country.upper(),
        "q": keyword,
        "search_type": "keyword_unordered",
        "media_type": "all",
    }
    return "https://www.facebook.com/ads/library/?" + urlencode(params)


def find_values_by_key(value: Any, target_key: str) -> list[Any]:
    """Tìm mọi value có key tương ứng trong cây dict/list."""
    found: list[Any] = []

    if isinstance(value, dict):
        for key, child in value.items():
            if key == target_key:
                found.append(child)
            found.extend(find_values_by_key(child, target_key))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_values_by_key(child, target_key))

    return found


def timestamp_to_iso(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or not value:
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def extract_image(image: dict[str, Any]) -> dict[str, Any]:
    return {
        "original_url": image.get("original_image_url"),
        "resized_url": image.get("resized_image_url"),
        "watermarked_url": image.get("watermarked_resized_image_url"),
    }


def extract_video(video: dict[str, Any]) -> dict[str, Any]:
    return {
        "hd_url": video.get("video_hd_url"),
        "sd_url": video.get("video_sd_url"),
        "preview_url": video.get("video_preview_image_url"),
        "watermarked_hd_url": video.get("watermarked_video_hd_url"),
        "watermarked_sd_url": video.get("watermarked_video_sd_url"),
    }


def normalize_ad(ad: dict[str, Any]) -> dict[str, Any]:
    snapshot = ad.get("snapshot")
    if not isinstance(snapshot, dict):
        snapshot = {}

    body = snapshot.get("body")
    if not isinstance(body, dict):
        body = {}

    impression_data = ad.get("impressions_with_index")
    if not isinstance(impression_data, dict):
        impression_data = {}

    raw_images = snapshot.get("images") or []
    raw_videos = snapshot.get("videos") or []

    return {
        "ad_archive_id": ad.get("ad_archive_id"),
        "collation_id": ad.get("collation_id"),
        "collation_count": ad.get("collation_count"),
        "is_active": ad.get("is_active"),
        "page_id": ad.get("page_id"),
        "page_name": ad.get("page_name") or snapshot.get("page_name"),
        "page_profile_uri": snapshot.get("page_profile_uri"),
        "page_profile_picture_url": snapshot.get("page_profile_picture_url"),
        "page_categories": snapshot.get("page_categories") or [],
        "page_like_count": snapshot.get("page_like_count"),
        "title": snapshot.get("title"),
        "body": body.get("text"),
        "caption": snapshot.get("caption"),
        "cta_text": snapshot.get("cta_text"),
        "cta_type": snapshot.get("cta_type"),
        "display_format": snapshot.get("display_format"),
        "link_url": snapshot.get("link_url"),
        "link_description": snapshot.get("link_description"),
        "images": [
            extract_image(image) for image in raw_images if isinstance(image, dict)
        ],
        "videos": [
            extract_video(video) for video in raw_videos if isinstance(video, dict)
        ],
        "publisher_platform": ad.get("publisher_platform") or [],
        "start_timestamp": ad.get("start_date"),
        "start_date": timestamp_to_iso(ad.get("start_date")),
        "end_timestamp": ad.get("end_date"),
        "end_date": timestamp_to_iso(ad.get("end_date")),
        "currency": ad.get("currency"),
        "spend": ad.get("spend"),
        "reach_estimate": ad.get("reach_estimate"),
        "impressions": impression_data.get("impressions_text"),
        "categories": ad.get("categories") or [],
        "country_iso_code": snapshot.get("country_iso_code"),
    }


def wait_for_embedded_ads(driver: webdriver.Chrome, timeout: float) -> None:
    WebDriverWait(driver, timeout).until(lambda browser: browser.execute_script("""
            return Array.from(document.querySelectorAll(
                'script[type="application/json"][data-sjs]'
            )).some(element =>
                element.textContent.includes('ad_archive_id') ||
                element.textContent.includes('collated_results')
            );
            """))


def read_script_contents(driver: webdriver.Chrome) -> list[str]:
    return driver.execute_script("""
        return Array.from(document.querySelectorAll(
            'script[type="application/json"][data-sjs]'
        ))
        .map(element => element.textContent || '')
        .filter(text =>
            text.includes('ad_archive_id') ||
            text.includes('collated_results')
        );
        """) or []


def extract_ads_from_scripts(script_contents: list[str]) -> list[dict[str, Any]]:
    ads_by_id: dict[str, dict[str, Any]] = {}
    parsed_count = 0

    for content in script_contents:
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as error:
            print(f"Bỏ qua một script JSON lỗi: {error}")
            continue

        collated_groups = find_values_by_key(payload, "collated_results")

        for group in collated_groups:
            if not isinstance(group, list):
                continue
            for raw_ad in group:
                if not isinstance(raw_ad, dict):
                    continue
                ad_id = raw_ad.get("ad_archive_id")
                if ad_id:
                    ads_by_id[str(ad_id)] = normalize_ad(raw_ad)

    return list(ads_by_id.values())


def getAds(driver):
    try:
        wait_for_embedded_ads(driver, 30)
    except TimeoutException:
        raise RuntimeError(
            "Không tìm thấy JSON quảng cáo. Trang có thể yêu cầu cookie, "
            "CAPTCHA/checkpoint, không có kết quả hoặc cấu trúc đã thay đổi."
        )

    # Đợi ngắn để các payload defer còn lại được đưa vào DOM.
    time.sleep(2)
    script_contents = read_script_contents(driver)
    ads = extract_ads_from_scripts(script_contents)
    print(f"Đã lấy {len(ads)} quảng cáo.")
    return ads
