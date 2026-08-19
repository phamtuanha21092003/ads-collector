from dataclasses import dataclass


@dataclass
class BrowserFingerprint:
    user_agent: str
    rd_challenge: str
    referer: str
    sec_ch_ua_platform: str
    x_fb_friendly_name: str
    x_fb_lsd: str
    sec_ch_ua: str

    def _get_graphql_header(self):
        return {
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en;q=0.9,fr-FR;q=0.8,fr;q=0.7,en-US;q=0.6,zh-CN;q=0.5,zh;q=0.4",
            "content-length": 1263,
            "content-type": "application/x-www-form-urlencoded",
            "origin": "https://www.facebook.com",
            "priority": "u=1, i",
            "referer": "https://www.facebook.com",
            "sec-ch-prefers-color-scheme": "dask",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-full-version-list": "",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "sec-ch-ua-platform-version": "",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": self.user_agent,
            "x-asbd-id": "359341",
            "x-fb-friendly-name": "AdLibrarySearchPaginationQuery",
            "x-fb-lsd": self.x_fb_lsd,
        }
