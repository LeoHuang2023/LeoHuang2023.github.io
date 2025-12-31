import os
import json
import traceback
from io import BytesIO
from PIL import Image
from flask import Flask, request, abort
from dotenv import load_dotenv
import google.generativeai as genai
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    ImageMessageContent,
    LocationMessageContent
)

# 嘗試載入 cwa 模組
try:
    import cwa
except ImportError:
    cwa = None

# 1. 初始化環境變數
load_dotenv()

CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
CWA_KEY = os.getenv('CWA_KEY')

# 2. 設定 Gemini (使用 2.0 Flash)
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    model = None
    print("⚠️ 警告: 未設定 GEMINI_API_KEY")

app = Flask(__name__)

if CHANNEL_ACCESS_TOKEN and CHANNEL_SECRET:
    configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(CHANNEL_SECRET)
else:
    configuration = None
    handler = None
    print("⚠️ 警告: 未設定 LINE Channel Token/Secret")

# 3. Webhook 入口
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        if handler:
            handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info("Invalid signature.")
        abort(400)
    return 'OK'

# 4. 文字訊息處理
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    ask = event.message.text
    ask_lower = ask.lower()
    
    ask_map = {
        'hello': '我很好', 
        'hi': '您哪位',
        '你好': '你好呀！傳張寵物照片給我看看？'
    }
    
    ans = ask_map.get(ask_lower)
    
    if not ans and cwa and CWA_KEY:
        try:
            weather_data = cwa.cwa2(ask, CWA_KEY)
            if weather_data:
                ans = cwa.tostr(weather_data, '\n')
            else:
                ans = None 
        except Exception:
            ans = None

    if not ans:
        ans = "我聽不懂你在說什麼～試試傳一張寵物照片給我！🐶🐱"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=ans)]
            )
        )

# 5. 地點訊息處理
@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location_message(event):
    if not cwa or not CWA_KEY:
        return
        
    site = (event.message.latitude, event.message.longitude)
    try:
        ans = cwa.cwa2(site, CWA_KEY)
        ans = cwa.tostr(ans, '\n') or '無此站'
    except:
        ans = "無法查詢該地點氣象"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=ans)]
            )
        )

# 6. 圖片訊息處理
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_content_message(event):
    if not model:
        return

    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        message_content = line_bot_blob_api.get_message_content(message_id=event.message.id)
        image_bytes = message_content
        image = Image.open(BytesIO(image_bytes))

        try:
            prompt = """
            請分析這張圖片。
            第一步：判斷圖片主體是否為「真實的動物寵物」（如貓、狗、兔、倉鼠、鳥等）。
            第二步：回傳 JSON 格式結果。
            若「不是寵物」，回傳： {"is_pet": false}
            若「是寵物」，回傳（繁體中文）：
            {"is_pet": true, "species": "物種", "breed": "品種", "colors": ["顏色"], "mood": "情緒", "features": "特徵", "care_tips": "建議"}
            只回傳 JSON。
            """
            response = model.generate_content([prompt, image])
            
            # 清理 JSON 字串
            text = response.text.strip()
            if text.startswith("```json"): text = text[7:]
            if text.startswith("```"): text = text[3:]
            if text.endswith("```"): text = text[:-3]
            json_str = text.strip()

            data = json.loads(json_str)

            if not data.get("is_pet"):
                reply_text = "這不是毛小孩相片 🐶🐱"
            else:
                reply_text = (
                    f"這是一隻可愛的 {data.get('breed', '毛小孩')} ({data.get('species')})！\n"
                    f"🎨 毛色：{', '.join(data.get('colors', []))}\n"
                    f"😺 心情：{data.get('mood')}\n"
                    f"📝 特徵：{data.get('features')}\n"
                    f"💡 照顧建議：{data.get('care_tips')}"
                )

        except Exception:
            traceback.print_exc()
            reply_text = "AI 辨識發生錯誤，請稍後再試。"

        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

# 重要：這是給 Render 啟動用的
if __name__ == "__main__":
    app.run(port=8080)