import json
import logging
import os
import re
import urllib
from io import BytesIO

import google.generativeai as genai
import requests
from PIL import Image

logger = logging.getLogger(__file__)


def is_url_valid(url):
    regex = re.compile(
        r"^(?:http|ftp)s?://"  # http:// or https://
        # domain...
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"
        r"localhost|"  # localhost...
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # ...or ip
        r"(?::\d+)?"  # optional port
        r"(?:/?|[/?]\S+)$",
        re.IGNORECASE,
    )
    return re.match(regex, url) is not None


def create_gcal_url(
    title="看到這個..請重生",
    date="20230524T180000/20230524T220000",
    location="那邊",
    description="TBC",
):
    base_url = "https://www.google.com/calendar/render?action=TEMPLATE"
    event_url = f"{base_url}&text={urllib.parse.quote(title)}&dates={date}&location={urllib.parse.quote(location)}&details={urllib.parse.quote(description)}"
    return event_url + "&openExternalBrowser=1"


def check_image(url=None, b_image=None):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    if url is not None:
        response = requests.get(url)
        if response.status_code == 200:
            image_data = response.content
    elif b_image is not None:
        image_data = b_image
    else:
        return "None"
    logger.info(f"URL: {url} \n Image: {b_image}")
    image = Image.open(BytesIO(image_data))

    model = genai.GenerativeModel("gemini-1.5-flash")
    response = model.generate_content(
        [
            """
        請幫我把圖片中的時間、地點、活動標題 以及活動內容提取出來。
        其中時間區間的格式必須符合 Google Calendar 的格式，像是 "20240409T070000Z/20240409T080000Z"。
        由於時區為 GMT+8，所以請記得將時間換算成 GMT+0 的時間。
        如果是中華民國年，請轉換成西元年，例如 110 年要轉換成 2021 年。
        content 請只保留純文字，不要有任何 HTML 標籤，並且幫忙列點一些活動的注意事項。
        不准有 markdown 的格式。
        輸出成 JSON 格式，絕對不能有其他多餘的格式，例如：
        {
            "time": "20240409T070000Z",
            "location": "台北市",
            "title": "大直美術館極限公園",
            "content": "這是一個很棒的地方，歡迎大家來參加！"
        }
        """,
            image,
        ]
    )

    logger.info(response.text)

    return response.text


def shorten_url_by_reurl_api(short_url):
    url = "https://api.reurl.cc/shorten"

    headers = {
        "Content-Type": "application/json",
        "reurl-api-key": os.getenv("REURL_API_KEY"),
    }

    response = requests.post(
        url,
        headers=headers,
        data=json.dumps(
            {
                "url": short_url,
            }
        ),
    )
    logger.info(response.json())
    return response.json()["short_url"]


def speech_translate_summary(audio_file=None, pdf_file=None):
    from whisperx_audio2text import main as audio2text

    from translation import main as translate

    print("audio2text...")
    conv_json, text, language = audio2text(audio_file)

    output_file = None
    # output_file = "translated/" + language + "_" + audio_file.replace(".mp3", ".txt")
    # if not os.path.exists("translated"):
    #     os.makedirs("translated")

    # print("done. translate...")
    # translated_text = translate(text, output_file)

    return text
