import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)
from pydantic import BaseModel, Field

logger = logging.getLogger("uvicorn.error")

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# 使用 127.0.0.1，避免 localhost 走系统代理导致 502
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
# qwen3:4b 为思考版；要直接回答请用 instruct（非思考）
DEFAULT_MODEL = "qwen3:4b-instruct"
REQUEST_TIMEOUT = 300.0
MAX_NOTE_CHARS = 20000
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/bmp",
    "image/gif",
}

_ocr_engine: Any = None
_ocr_ready = False
_ocr_error: str | None = None


def load_project_env() -> None:
    """加载项目根目录 .env，不覆盖已有的非空环境变量。"""
    if not ENV_FILE.is_file():
        return

    load_dotenv(dotenv_path=ENV_FILE, override=False)

    for key, value in dotenv_values(dotenv_path=ENV_FILE).items():
        if value is None:
            continue
        current = os.environ.get(key)
        if current is None or not str(current).strip():
            os.environ[key] = value


def get_model_name() -> str:
    load_project_env()
    return (os.getenv("OLLAMA_MODEL") or "").strip() or DEFAULT_MODEL


def init_ocr_engine() -> None:
    global _ocr_engine, _ocr_ready, _ocr_error
    if _ocr_ready or _ocr_error:
        return
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
        _ocr_ready = True
        _ocr_error = None
        logger.info("RapidOCR 已就绪")
    except Exception as exc:
        _ocr_engine = None
        _ocr_ready = False
        _ocr_error = str(exc)
        logger.exception("RapidOCR 初始化失败")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_project_env()
    init_ocr_engine()
    yield


load_project_env()

app = FastAPI(title="笔记摘要助手", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SummarizeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)


class SummarizeResponse(BaseModel):
    summary: str
    key_points: list[str]
    mode: str = "ollama"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=MAX_NOTE_CHARS)


class ChatResponse(BaseModel):
    mode: str
    configured: bool
    reply: str


class OcrResponse(BaseModel):
    text: str
    summary: str
    key_points: list[str]
    mode: str = "ollama"


def strip_think_tags(text: str) -> str:
    cleaned = text.strip()
    if "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[-1].strip()
    return cleaned


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = strip_think_tags(text)
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def parse_summary_payload(raw: str) -> tuple[str, list[str]]:
    data = extract_json_object(raw)
    if data:
        summary = str(data.get("summary") or "").strip()
        points_raw = data.get("key_points") or data.get("points") or []
        key_points: list[str] = []
        if isinstance(points_raw, list):
            key_points = [str(item).strip() for item in points_raw if str(item).strip()]
        elif isinstance(points_raw, str) and points_raw.strip():
            key_points = [points_raw.strip()]
        if summary:
            return summary, key_points

    fallback = strip_think_tags(raw)
    lines = [line.strip(" -•\t") for line in fallback.splitlines() if line.strip()]
    if not lines:
        return fallback, []
    if len(lines) == 1:
        return lines[0], []
    return lines[0], lines[1:6]


def map_ollama_error(exc: Exception) -> HTTPException:
    if isinstance(exc, APITimeoutError):
        logger.warning("Ollama 请求超时")
        return HTTPException(
            status_code=504,
            detail="本地模型响应超时，请稍后重试。",
        )
    if isinstance(exc, APIConnectionError):
        logger.warning("无法连接 Ollama：%s", OLLAMA_BASE_URL)
        return HTTPException(
            status_code=503,
            detail="无法连接本地 Ollama，请确认 Ollama 正在运行。",
        )
    if isinstance(exc, APIStatusError):
        logger.exception(
            "Ollama API 错误：status=%s, message=%s",
            exc.status_code,
            exc.message,
        )
        return HTTPException(
            status_code=502,
            detail=f"Ollama 返回错误，HTTP 状态码：{exc.status_code}。请查看后端终端日志。",
        )
    logger.exception("本地模型调用出现未预期错误")
    return HTTPException(
        status_code=502,
        detail="调用本地模型时出现问题，请检查终端日志。",
    )


async def chat_with_ollama(messages: list[dict[str, str]]) -> str:
    http_client = httpx.AsyncClient(trust_env=False, timeout=REQUEST_TIMEOUT)
    client = AsyncOpenAI(
        api_key="ollama",
        base_url=OLLAMA_BASE_URL,
        timeout=REQUEST_TIMEOUT,
        http_client=http_client,
    )
    try:
        completion = await client.chat.completions.create(
            model=get_model_name(),
            messages=messages,
            extra_body={"think": False, "reasoning_effort": "none"},
        )
        reply = ""
        if completion.choices:
            reply = strip_think_tags(completion.choices[0].message.content or "")
        if not reply:
            raise HTTPException(
                status_code=502,
                detail="本地模型没有返回可用内容，请稍后重试。",
            )
        return reply
    except HTTPException:
        raise
    except Exception as exc:
        raise map_ollama_error(exc) from exc
    finally:
        await client.close()
        await http_client.aclose()


async def summarize_text(text: str) -> tuple[str, list[str]]:
    prompt = (
        "请阅读下面的笔记，输出严格 JSON（不要 markdown 代码块，不要多余说明）："
        '{"summary":"一段中文摘要","key_points":["要点1","要点2","要点3"]}\n'
        "要求：summary 2-4 句；key_points 3-6 条，每条简短。\n\n"
        f"笔记内容：\n{text}"
    )
    raw = await chat_with_ollama(
        [
            {
                "role": "system",
                "content": "你是中文笔记摘要助手，只输出合法 JSON。",
            },
            {"role": "user", "content": prompt},
        ]
    )
    return parse_summary_payload(raw)


def run_ocr_on_bytes(image_bytes: bytes) -> str:
    init_ocr_engine()
    if not _ocr_ready or _ocr_engine is None:
        raise HTTPException(
            status_code=503,
            detail=f"OCR 引擎未就绪：{_ocr_error or '未知错误'}",
        )

    try:
        # RapidOCR 接受 str/Path/bytes/numpy，不接受 PIL Image
        result, _ = _ocr_engine(image_bytes)
    except HTTPException:
        raise
    except Exception:
        logger.exception("OCR 识别失败")
        raise HTTPException(
            status_code=502,
            detail="图片文字识别失败，请换一张更清晰的图片重试。",
        ) from None

    if not result:
        return ""

    lines: list[str] = []
    for item in result:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            text = str(item[1]).strip()
            if text:
                lines.append(text)
    return "\n".join(lines).strip()


@app.get("/api/health")
def health():
    init_ocr_engine()
    return {
        "status": "ok",
        "message": "笔记摘要助手后端运行正常",
        "env_file_exists": ENV_FILE.is_file(),
        "api_key_configured": True,
        "model": get_model_name(),
        "provider": "ollama",
        "ocr_ready": _ocr_ready,
        "ocr_error": _ocr_error,
        "frontend_dist_exists": FRONTEND_DIST.is_dir(),
    }


@app.post("/api/summarize", response_model=SummarizeResponse)
async def summarize(body: SummarizeRequest):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="文本不能为空")

    summary, key_points = await summarize_text(text)
    return SummarizeResponse(summary=summary, key_points=key_points, mode="ollama")


@app.post("/api/ocr", response_model=OcrResponse)
async def ocr(file: UploadFile = File(...), summarize_after: bool = True):
    content_type = (file.content_type or "").lower()
    if content_type and content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=422, detail="仅支持 PNG / JPEG / WebP / BMP / GIF 图片")

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=422, detail="上传文件为空")
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="图片不能超过 8MB")

    text = run_ocr_on_bytes(image_bytes)
    if not text:
        raise HTTPException(
            status_code=422,
            detail="未能识别出文字，请上传更清晰的笔记图片。",
        )

    summary = ""
    key_points: list[str] = []
    if summarize_after:
        summary, key_points = await summarize_text(text[:MAX_NOTE_CHARS])

    return OcrResponse(
        text=text[:MAX_NOTE_CHARS],
        summary=summary,
        key_points=key_points,
        mode="ollama",
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    message = body.message.strip()
    context = (body.context or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")
    if not context:
        raise HTTPException(
            status_code=422,
            detail="请先粘贴、上传文本或识别图片，生成当前笔记后再提问。",
        )

    system_prompt = (
        "你是中文笔记问答助手。请严格依据用户提供的笔记内容回答；"
        "若笔记中没有相关信息，请明确说明「笔记中未提及」。回答简洁清楚。"
    )
    user_prompt = f"【当前笔记】\n{context}\n\n【用户问题】\n{message}"
    reply = await chat_with_ollama(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    return ChatResponse(mode="ollama", configured=True, reply=reply)


def _register_frontend() -> None:
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    index_file = FRONTEND_DIST / "index.html"
    if not index_file.is_file():
        @app.get("/")
        def home_missing():
            raise HTTPException(
                status_code=503,
                detail="前端尚未构建。请进入 frontend 目录执行 npm install && npm run build。",
            )
        return

    @app.get("/")
    def home():
        return FileResponse(index_file)

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)


_register_frontend()
