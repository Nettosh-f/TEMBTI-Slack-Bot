import os
import time
import re
import threading
from fastapi import APIRouter, Form, UploadFile, File, Request, FastAPI
from fastapi.responses import PlainTextResponse
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv
from openai import OpenAI
import uvicorn
from io import BytesIO
import base64
from PIL import Image
from consts import *
from utils import pdf_to_images_pymupdf, slack_format, create_pdf_from_text


answered_cache = {}  # key: unique event or thread, value: timestamp last answered
ANSWER_TIMEOUT = 60 * 60  # 2 hours in seconds
load_dotenv()
app = FastAPI()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

slack_client = WebClient(token=SLACK_BOT_TOKEN)
openai_client = OpenAI(api_key=OPENAI_API_KEY)


def cleanup_answered_cache():
    now = time.time()
    to_delete = [key for key, expiry in answered_cache.items() if expiry < now]
    for key in to_delete:
        del answered_cache[key]
    # Run again in 1 minute (60 seconds)
    threading.Timer(60, cleanup_answered_cache).start()


cleanup_answered_cache()


@app.post("/slack/events")
async def slack_events(req: Request):
    data = await req.json()
    if "challenge" in data:
        return {"challenge": data["challenge"]}
    event = data.get("event", {})
    event_type = event.get("type")

    # Unique key for each user in each channel
    event_key = f"{event.get('channel')}_{event.get('user')}"
    now = time.time()

    # Skip if already answered in last 2 hours
    if event_key in answered_cache:
        if now - answered_cache[event_key] < ANSWER_TIMEOUT:
            print("Already answered recently, skipping.")
            return {"ok": True}

    if event_type == "app_mention":
        text = event.get("text", "")
        channel_id = event.get("channel")
        user_id = event.get("user")

        # Regex to extract: [YOUR MBTI] @mention [THEIR MBTI]
        pattern = r'(\b[A-Z]{4}\b).*<@([A-Z0-9]+)>.*?(\b[A-Z]{4}\b)'
        match = re.search(pattern, text)
        if match:
            mbti_1 = match.group(1)
            mentioned_user = match.group(2)
            mbti_2 = match.group(3)

            if mbti_1 in MBTI_TYPES and mbti_2 in MBTI_TYPES:
                print(f"Comparing {mbti_1} with {mbti_2}")
                prompt = (
                    f"Compare a romantic couple with MBTI types {mbti_1} and {mbti_2}. "
                    "Give a detailed compatibility analysis in 2 paragraphs: one about their strengths and one about their challenges."
                )
                # Call OpenAI
                response = openai_client.chat.completions.create(
                    model="gpt-4o",  # Or "gpt-4.1"
                    messages=[
                        {"role": "system", "content": "You are an expert on MBTI couple dynamics."},
                        {"role": "user", "content": prompt}
                    ]
                )
                reply = response.choices[0].message.content.strip()
                reply_msg = f"<@{user_id}> ({mbti_1}) + <@{mentioned_user}> ({mbti_2})\n\n{reply}"
            else:
                reply_msg = "Invalid MBTI types provided. Please use format: `@SlackBot [Your MBTI] @user [Their MBTI]`"

            try:
                slack_client.chat_postMessage(channel=channel_id, text=reply_msg)
            except SlackApiError as e:
                print(f"Slack error: {e.response['error']}")

        # Mark as answered
        answered_cache[event_key] = time.time() + ANSWER_TIMEOUT

    return {"ok": True}


@app.get("/")
def read_root():
    return {"status": "running"}


@app.post("/slack/insight")
async def slack_insight(
    request: Request,
    token: str = Form(...),
    user_id: str = Form(...),
    channel_id: str = Form(...),
    text: str = Form(""),
    files: list[UploadFile] = File(None)  # Slack sends files as multipart
):
    # Validate token (optional)
    if token != os.getenv("SLACK_VERIFICATION_TOKEN"):
        return PlainTextResponse("Invalid token", status_code=403)

    # Find the first PDF file
    pdf_bytes = None
    if files:
        for f in files:
            if f.filename.lower().endswith('.pdf'):
                pdf_bytes = await f.read()
                break
    if not pdf_bytes:
        return PlainTextResponse("Please attach a PDF MBTI report to the command.", status_code=200)

    # Process PDF to images
    images = pdf_to_images_pymupdf(pdf_bytes)

    # Convert images to OpenAI image messages
    def images_to_openai_messages(images):
        messages = []
        for img in images:
            buf = BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            messages.append({
                "type": "image_url",
                "image_url": {
                    "url": "data:image/png;base64," + b64
                }
            })
        return messages

    image_messages = images_to_openai_messages(images)

    # System and user prompt
    system_prompt = (
        "You are an MBTI expert. Analyze the following official MBTI report. "
        "Summarize key strengths, blind spots, communication styles, and give actionable advice. "
        "Write as if addressing the report's subject personally. give 2-3 paragraphs maximum."
    )
    user_message = [{"type": "text", "text": "Analyze this MBTI report."}]
    if text.strip():
        user_message.append({"type": "text", "text": text.strip()})
    user_message += image_messages

    # Call OpenAI
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=900
        )
        insight = response.choices[0].message.content.strip()
        insight_slack = f"Your MBTI report insight:\n\n{slack_format(insight)}"
        pdf_file = create_pdf_from_text(insight)
    except Exception as e:
        return PlainTextResponse(f"Error while analyzing MBTI report: {str(e)}", status_code=500)

    # Upload PDF and post message
    upload_response = slack_client.files_upload(
        channels=channel_id,
        file=pdf_file,
        filename="mbti_insight.pdf",
        title="MBTI Insight Report",
        initial_comment="Download the detailed insight as PDF:"
    )
    slack_client.chat_postMessage(
        channel=channel_id,
        text=insight_slack
    )
    return PlainTextResponse("", status_code=200)


if __name__ == "__main__":

    uvicorn.run("main:app", host="127.0.0.1", port=3000, reload=True)