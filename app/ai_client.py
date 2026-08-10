import logging
from dataclasses import dataclass
import openai

log = logging.getLogger(__name__)


class AIClientError(Exception):
    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class ChatResult:
    text: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class AIClient:
    def __init__(self, client):
        self._client = client

    @classmethod
    def _get_client(self):
        if self._client is None:
            self._client = openai
        return self._client

    @classmethod
    def _prepare():
        prompt = f"""
        Bạn là chuyên gia phân tích quảng cáo thương mại điện tử. Nhiệm vụ của bạn là đọc nội dung quảng cáo (title + body + hashtag) dưới đây và trả về DUY NHẤT 1 keyword mô tả chính xác nhất sản phẩm đang được quảng cáo.

        QUY TẮC BẮT BUỘC:
        1. Keyword phải mô tả ĐÚNG LOẠI SẢN PHẨM + ĐẶC ĐIỂM NỔI BẬT NHẤT (USP) — không phải cảm xúc, không phải lợi ích chung chung.
        2. Ưu tiên đặc điểm được nhắc lặp lại nhiều lần hoặc xuất hiện trong CẢ mô tả lẫn hashtag (tín hiệu mạnh nhất).
        3. TUYỆT ĐỐI KHÔNG dùng các từ chung chung, không đặc trưng cho sản phẩm này: "kitchen gadget", "home essentials", "must-have", "trending", "life hack", "cooking", và các hashtag mang tính cảm xúc/marketing (vd: "freedom", "satisfy cravings").
        4. KHÔNG dùng tên thương hiệu, tên shop, hoặc slug URL.
        5. Keyword phải là cụm danh từ (noun phrase) ngắn gọn, 3-6 từ, viết bằng tiếng Anh, dạng có thể dùng trực tiếp để search trên nền tảng quảng cáo hoặc sàn TMĐT (Google, Facebook Ads Library, Amazon...).
        6. Nếu sản phẩm có nhiều tính năng, chỉ chọn 1 tính năng khác biệt nhất (điểm khiến sản phẩm này khác với sản phẩm cùng loại thông thường trên thị trường).
        7. Chỉ trả về 1 dòng duy nhất là keyword, không giải thích, không markdown, không dấu ngoặc kép.

        VÍ DỤ:
        Input: nội dung quảng cáo về máy chiên có bộ lọc dầu tự động, kích thước nhỏ cho 1 người, chất liệu inox, dùng cho phòng ký túc xá.
        Output: mini deep fryer with oil filter

        Bây giờ hãy phân tích nội dung sau:

        ---
        TITLE: {{title}}
        CONTENT: {{content}}
        ---
        Trả về  keyword:
        """

    def _raise_http(
        self,
        status_code: int,
    ) -> None:
        if status_code in (401, 403):
            raise AIClientError(
                f"AI auth failed ({status_code}).",
                status_code=401,
            )

    async def complete() -> ChatResult:
        return """"""
