import json
import logging
import os
import sys

if os.getenv("API_ENV") != "production":
    from dotenv import load_dotenv

    load_dotenv()

import tempfile

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    MessagingApiBlob,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    AudioMessageContent,
    FileMessageContent,
    ImageMessageContent,
    MessageEvent,
    TextMessageContent,
)

logging.basicConfig(level=os.getenv("LOG", "WARNING"))
logger = logging.getLogger(__file__)

app = FastAPI()

channel_secret = os.getenv("LINE_CHANNEL_SECRET", None)
channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", None)
if channel_secret is None:
    print("Specify LINE_CHANNEL_SECRET as environment variable.")
    sys.exit(1)
if channel_access_token is None:
    print("Specify LINE_CHANNEL_ACCESS_TOKEN as environment variable.")
    sys.exit(1)

configuration = Configuration(access_token=channel_access_token)

handler = WebhookHandler(channel_secret)


import google.generativeai as genai
from firebase import firebase
from utils import (
    check_image,
    create_gcal_url,
    is_url_valid,
    shorten_url_by_reurl_api,
    speech_translate_summary,
)

firebase_url = os.getenv("FIREBASE_URL")
gemini_key = os.getenv("GEMINI_API_KEY")

CS_begin = False
CS_gotAudio = False
CS_gotpdf = False
CS_audio = None
CS_pdf = None

# Initialize the Gemini Pro API
genai.configure(api_key=gemini_key)


@app.get("/health")
async def health():
    return "ok"


@app.get("/")
async def find_image_keyword(img_url: str):
    image_data = check_image(img_url)
    image_data = json.loads(image_data)

    g_url = create_gcal_url(
        image_data["title"],
        image_data["time"],
        image_data["location"],
        image_data["content"],
    )
    if is_url_valid(g_url):
        return RedirectResponse(g_url)
    else:
        return "Error"


@app.post("/webhooks/line")
async def handle_callback(request: Request):
    signature = request.headers["X-Line-Signature"]

    # get request body as text
    body = await request.body()
    body = body.decode()

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    logging.info(event)
    text = event.message.text
    user_id = event.source.user_id

    fdb = firebase.FirebaseApplication(firebase_url, None)

    user_chat_path = f"chat/{user_id}"
    # chat_state_path = f'state/{user_id}'
    conversation_data = fdb.get(user_chat_path, None)
    model = genai.GenerativeModel("gemini-1.5-flash")

    if conversation_data is None:
        messages = []
    else:
        messages = conversation_data

    global CS_begin, CS_gotAudio, CS_gotpdf, CS_audio, CS_pdf

    if CS_begin:
        if text == "C":
            CS_begin = False
            CS_gotAudio = False
            CS_gotpdf = False
            reply_msg = "已取消"
        elif text == "n":
            line_bot_api.show_loading_animation(
                ShowLoadingAnimationRequest(chat_id=user_id, loading_seconds=5)
            )
            try:
                summary = speech_translate_summary(CS_audio, None)
                CS_begin = False
                CS_gotAudio = False
                CS_gotpdf = False
                CS_audio = None
                CS_pdf = None
                reply_msg = summary
            except Exception as e:
                reply_msg = f"Error: {e}"

    elif text == "C":
        fdb.delete(user_chat_path, None)
        reply_msg = "已清空對話紀錄"
    elif is_url_valid(text):
        image_data = check_image(text)
        image_data = json.loads(image_data)
        g_url = create_gcal_url(
            image_data["title"],
            image_data["time"],
            image_data["location"],
            image_data["content"],
        )
        reply_msg = shorten_url_by_reurl_api(g_url)
    elif text == "A":
        response = model.generate_content(
            f"Summary the following message in Traditional Chinese by less 5 list points. \n{messages}"
        )
        reply_msg = response.text
    elif text == "course summary":
        CS_begin = True
        reply_msg = "好的，請給我課程的錄音檔！"
    else:
        messages.append({"role": "user", "parts": [text]})
        response = model.generate_content(messages)
        messages.append({"role": "model", "parts": [response.text]})
        # 更新firebase中的對話紀錄
        fdb.put_async(user_chat_path, None, messages)
        reply_msg = response.text

    with AsyncApiClient(configuration) as api_client:
        line_bot_api = AsyncMessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_msg)],
            )
        )

    return "OK"


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_github_message(event):
    image_content = b""
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        image_content = line_bot_blob_api.get_message_content(event.message.id)

    global CS_begin, CS_gotpdf, CS_pdf, CS_audio, CS_gotAudio

    if CS_begin:
        CS_pdf = image_content
        CS_gotpdf = True
        summary = speech_translate_summary(CS_audio, CS_pdf)
        CS_begin = False
        CS_gotAudio = False
        CS_gotpdf = False
        CS_audio = None
        CS_pdf = None
        reply_msg = summary
    else:
        reply_msg = "你想做什麼呢？如果想整理社課筆記，請先輸入course summary！"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                replyToken=event.reply_token, messages=[TextMessage(text=reply_msg)]
            )
        )
    return "OK"


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio_message(event):
    global CS_begin, CS_gotAudio, CS_audio
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        audio_content = line_bot_blob_api.get_message_content(event.message.id)
    if CS_begin:
        with tempfile.NamedTemporaryFile(
            suffix=".mp3", delete=False
        ) as temp_audio_file:
            temp_audio_file.write(audio_content)
            audio_content = temp_audio_file.name

        CS_audio = audio_content
        CS_gotAudio = True
        # reply_msg = f"audio_content type: {type(audio_content)}"
        reply_msg = '已收到錄音檔，如果有的話，請給我課程相關的截圖或圖片！\n如果沒有，請輸入"n"告訴我～'  # \n(目前尚未支援上傳pdf檔，請輸入任意字元繼續)
    else:
        reply_msg = "你想做什麼呢？如果想整理社課筆記，請先輸入course summary！"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_msg)],
            )
        )

    return "OK"


# @handler.add(MessageEvent, message=FileMessageContent)
# def handle_file_message(event):
#     global CS_begin, CS_gotpdf, CS_pdf, CS_audio, CS_gotAudio
#     with ApiClient(configuration) as api_client:
#         line_bot_blob_api = MessagingApiBlob(api_client)
#         file_content = line_bot_blob_api.get_message_content(event.message.id)
#     reply_msg = f"file_content type: {type(file_content)}"
#     with ApiClient(configuration) as api_client:
#         line_bot_api = MessagingApi(api_client)
#         line_bot_api.reply_message(
#             ReplyMessageRequest(
#                 reply_token=event.reply_token,
#                 messages=[TextMessage(text=reply_msg)],
#             )
#         )

#     if CS_begin:
#         CS_pdf = file_content
#         CS_gotpdf = True
#         summary = speech_translate_summary(CS_audio, CS_pdf)
#         CS_begin = False
#         CS_gotAudio = False
#         CS_gotpdf = False
#         CS_audio = None
#         CS_pdf = None
#         reply_msg = summary
#     else:
#         reply_msg = "你想做什麼呢？如果想整理社課筆記，請先輸入course summary！"

#     with ApiClient(configuration) as api_client:
#         line_bot_api = MessagingApi(api_client)
#         line_bot_api.reply_message(
#             ReplyMessageRequest(
#                 reply_token=event.reply_token,
#                 messages=[TextMessage(text=reply_msg)],
#             )
#         )

#     return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", default=8080))
    debug = True if os.environ.get("API_ENV", default="develop") == "develop" else False
    logging.info("Application will start...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=debug)
