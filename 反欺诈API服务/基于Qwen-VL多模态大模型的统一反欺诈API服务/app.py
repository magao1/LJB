import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 必须在所有项目模块导入之前加载 .env，
# 否则 config.py 中的 default_vlm_config 会在未读取到 .env 的情况下被创建。
load_dotenv()

# ---------------------------------------------------------------------------
# 日志配置：输出到控制台，级别可通过环境变量 LOG_LEVEL 调整
# ---------------------------------------------------------------------------
_log_level = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.DEBUG),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from fastapi import (  # noqa: E402
    APIRouter,
    FastAPI,
    Request,
    UploadFile,
    File,
    Form,
    Header,
    Depends,
    HTTPException,
    status,
)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import RedirectResponse, HTMLResponse  # noqa: E402

from config import default_vlm_config  # noqa: E402
from models import (  # noqa: E402
    KycForensicsAnalyzeBatchResponse,
    KycForensicsAnalyzeItem,
    IdcardFieldMismatch,
    IdcardFraudAnalyzeBatchResponse,
    IdcardFraudAnalyzeItem,
    ErrorResponse,
    HealthResponse,
    ExifAnalyzeBatchResponse,
    ExifAnalyzeItem,
)
from prompts import load_prompt  # noqa: E402
from utils.image_processing import (  # noqa: E402
    generate_ela_image,
    high_pass_filter,
    read_image_from_bytes,
    to_base64_jpeg,
    extract_exif_summary,
    detect_ps_crop_traces,
    exif_keyword_flags,
    validate_image_file,
)
from utils.vlm_client import VLMClient  # noqa: E402

APP_VERSION = "0.3.0"
MAX_BATCH_SIZE = 10

logger = logging.getLogger(__name__)

SERVICE_API_KEY = os.getenv("SERVICE_API_KEY")

_TEMPLATE_DIR = Path(__file__).parent / "templates"

app = FastAPI(
    title="Unified Anti-Fraud API",
    version=APP_VERSION,
    description="统一反欺诈 API - 支持多种场景的欺诈检测与 OCR 识别",
)

_cors_origins_env = os.getenv("CORS_ALLOW_ORIGINS")
if _cors_origins_env:
    _cors_origins = [o.strip()
                     for o in _cors_origins_env.split(",") if o.strip()]
else:
    _cors_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_req_logger = logging.getLogger("request")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """记录每次 HTTP 请求的详细信息，方便排查问题。"""
    request_id = request.headers.get("X-Request-ID", "-")
    client_host = request.client.host if request.client else "unknown"
    start = time.perf_counter()

    _req_logger.info(
        ">>> %s %s | client=%s | request_id=%s | content-type=%s | content-length=%s",
        request.method,
        request.url.path,
        client_host,
        request_id,
        request.headers.get("content-type", "-"),
        request.headers.get("content-length", "-"),
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        _req_logger.exception(
            "!!! UNHANDLED EXCEPTION %s %s | %.1fms | request_id=%s",
            request.method,
            request.url.path,
            elapsed,
            request_id,
        )
        raise exc

    elapsed = (time.perf_counter() - start) * 1000
    log_fn = _req_logger.warning if response.status_code >= 400 else _req_logger.info
    log_fn(
        "<<< %s %s | status=%d | %.1fms | request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed,
        request_id,
    )
    return response


_llm_client = VLMClient()

# ---------------------------------------------------------------------------
# /v1 路由
# ---------------------------------------------------------------------------
v1 = APIRouter(prefix="/v1")


async def verify_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> None:
    """
    基于请求头的 API Key 认证。
    若 SERVICE_API_KEY 未配置则不强制认证（方便开发环境）。
    """
    if not SERVICE_API_KEY:
        return
    if not x_api_key or x_api_key != SERVICE_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


@app.on_event("startup")
async def on_startup() -> None:
    is_valid, msg = default_vlm_config.validate()
    if not is_valid:
        logger.warning("VLM configuration invalid on startup: %s", msg)
    if not SERVICE_API_KEY:
        logger.warning(
            "SERVICE_API_KEY is not set, API authentication is disabled")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await _llm_client.aclose()


# ---------------------------------------------------------------------------
# 内部处理函数
# ---------------------------------------------------------------------------

async def _process_kyc_file(
    file: UploadFile,
    merchant_id: str,
    kyc_photo_prompt: str,
    ela_quality: int,
) -> tuple[KycForensicsAnalyzeItem, bool]:
    """处理单张 KYC 图片，返回 (result_item, success)。"""
    filename = file.filename or "unknown"
    try:
        img_bytes = await file.read()
        is_valid, err_msg = validate_image_file(img_bytes, filename)
        if not is_valid:
            return (
                KycForensicsAnalyzeItem(
                    merchant_id=merchant_id,
                    filename=filename,
                    is_ps=None,
                    is_aigc=None,
                    evidence=[f"ERROR: {err_msg}"],
                    exif_summary=None,
                    model=None,
                ),
                False,
            )

        exif_summary = extract_exif_summary(img_bytes)
        rule_result = detect_ps_crop_traces(img_bytes, exif_summary)

        ela_img = generate_ela_image(img_bytes, quality=ela_quality)
        hp_img = high_pass_filter(img_bytes)
        original_b64 = to_base64_jpeg(read_image_from_bytes(img_bytes))
        ela_b64 = to_base64_jpeg(ela_img)
        hp_b64 = to_base64_jpeg(hp_img)

        combined_prompt = (
            f"{kyc_photo_prompt}\n\n"
            f"[EXIF_SUMMARY_JSON]\n{json.dumps(exif_summary, ensure_ascii=False)}\n"
            f"[RULE_ANALYSIS_JSON]\n{json.dumps(rule_result, ensure_ascii=False)}\n"
        )
        combined_result = await _llm_client.call_multimodal(
            combined_prompt,
            [original_b64, ela_b64, hp_b64],
        )

        evidence: list[str] = []

        for e in rule_result.get("evidence", []):
            if e == "EXIF 软件字段显示存在编辑/生成工具":
                continue
            evidence.append(f"RULE: {e}")

        llm_is_ps = None
        llm_is_aigc = None
        llm_is_high_risk_scene = None
        if "error" in combined_result:
            evidence.append(f"VLM_ERROR: {combined_result.get('error')}")
        else:
            llm_is_ps = combined_result.get("is_ps")
            llm_is_aigc = combined_result.get("is_aigc")
            llm_is_high_risk_scene = combined_result.get("is_high_risk_scene")
            for e in combined_result.get("evidence", []):
                evidence.append(f"VLM: {e}")

        exif_is_ps, exif_is_aigc, exif_evidence = exif_keyword_flags(
            exif_summary)
        evidence.extend(exif_evidence)

        is_ps = bool(llm_is_ps) or exif_is_ps
        is_aigc = bool(llm_is_aigc) or exif_is_aigc
        is_high_risk_scene = bool(llm_is_high_risk_scene)

        return (
            KycForensicsAnalyzeItem(
                merchant_id=merchant_id,
                filename=filename,
                is_ps=is_ps,
                is_aigc=is_aigc,
                is_high_risk_scene=is_high_risk_scene,
                evidence=evidence,
                exif_summary=exif_summary,
                model=_llm_client.config.model,
            ),
            True,
        )

    except Exception as e:
        logger.exception("Error processing KYC file %s: %s", filename, e)
        return (
            KycForensicsAnalyzeItem(
                merchant_id=merchant_id,
                filename=filename,
                is_ps=None,
                is_aigc=None,
                evidence=[f"ERROR: {str(e)}"],
                exif_summary=None,
                model=None,
            ),
            False,
        )


async def _process_idcard_file(
    file: UploadFile,
    merchant_id: str,
    idcard_prompt: str,
    idcard_fields: dict[str, str],
) -> tuple[IdcardFraudAnalyzeItem, bool]:
    """处理单张证件照，用 VLM 分析是否存在欺诈，返回 (result_item, success)。"""
    filename = file.filename or "unknown"
    try:
        img_bytes = await file.read()
        is_valid, err_msg = validate_image_file(img_bytes, filename)
        if not is_valid:
            return (
                IdcardFraudAnalyzeItem(
                    merchant_id=merchant_id,
                    filename=filename,
                    is_fraud=None,
                    has_mismatch=None,
                    evidence=[f"ERROR: {err_msg}"],
                    model=None,
                ),
                False,
            )
        original_b64 = to_base64_jpeg(read_image_from_bytes(img_bytes))

        combined_prompt = (
            f"{idcard_prompt}\n\n"
            f"[USER_DECLARED_FIELDS]\n{json.dumps(idcard_fields, ensure_ascii=False)}\n"
        )
        combined_result = await _llm_client.call_multimodal(
            combined_prompt,
            [original_b64],
        )

        evidence: list[str] = []
        field_mismatches: list[dict] = []
        is_fraud = None
        has_mismatch = None
        ok = True
        if "error" in combined_result:
            evidence.append(f"VLM_ERROR: {combined_result.get('error')}")
            ok = False
        else:
            is_fraud = combined_result.get("is_fraud", False)
            has_mismatch = combined_result.get("has_mismatch", False)
            evidence = list(combined_result.get("evidence", []))
            field_mismatches = list(
                combined_result.get("field_mismatches", []))

        return (
            IdcardFraudAnalyzeItem(
                merchant_id=merchant_id,
                filename=filename,
                is_fraud=is_fraud,
                has_mismatch=has_mismatch,
                evidence=evidence,
                field_mismatches=[
                    IdcardFieldMismatch(**fm) for fm in field_mismatches
                    if isinstance(fm, dict)
                ],
                model=_llm_client.config.model,
            ),
            ok,
        )
    except Exception as e:
        logger.exception("Error processing idcard file %s: %s", filename, e)
        return (
            IdcardFraudAnalyzeItem(
                merchant_id=merchant_id,
                filename=filename,
                is_fraud=None,
                has_mismatch=None,
                evidence=[f"ERROR: {str(e)}"],
                model=None,
            ),
            False,
        )


async def _process_exif_file(
    file: UploadFile,
    merchant_id: str,
) -> tuple[ExifAnalyzeItem, bool]:
    """处理单张图片的 EXIF 分析。"""
    filename = file.filename or "unknown"
    try:
        img_bytes = await file.read()
        is_valid, err_msg = validate_image_file(img_bytes, filename)
        if not is_valid:
            return (
                ExifAnalyzeItem(
                    merchant_id=merchant_id,
                    filename=filename,
                    is_ps=None,
                    is_aigc=None,
                    evidence=[f"ERROR: {err_msg}"],
                    exif_summary=None,
                ),
                False,
            )

        exif_summary = extract_exif_summary(img_bytes)
        is_ps, is_aigc, evidence = exif_keyword_flags(exif_summary)
        return (
            ExifAnalyzeItem(
                merchant_id=merchant_id,
                filename=filename,
                is_ps=is_ps,
                is_aigc=is_aigc,
                evidence=evidence,
                exif_summary=exif_summary,
            ),
            True,
        )

    except Exception as e:
        logger.exception("Error processing EXIF file %s: %s", filename, e)
        return (
            ExifAnalyzeItem(
                merchant_id=merchant_id,
                filename=filename,
                is_ps=None,
                is_aigc=None,
                evidence=[f"ERROR: {str(e)}"],
                exif_summary=None,
            ),
            False,
        )


# ---------------------------------------------------------------------------
# /v1 业务端点
# ---------------------------------------------------------------------------

@v1.post(
    "/kyc/photo-vlm-analyze",
    response_model=KycForensicsAnalyzeBatchResponse,
    responses={500: {"model": ErrorResponse, "description": "处理错误"}},
    summary="KYC 大模型综合检测（二次贴附 + 裁剪痕迹 + PS/AIGC 判断）",
    description="批量检测多张证件照片的二次贴附、裁剪痕迹，并结合大模型判断 PS/AIGC",
)
async def photo_analyze_batch(
    files: list[UploadFile] = File(..., description="证件图片文件列表"),
    merchant_id: str = Form(..., description="国家", examples=[""]),
    ela_quality: int = Form(90, ge=1, le=100, description="ELA 分析的 JPEG 压缩质量"),
    x_request_id: Optional[str] = Header(
        default=None, alias="X-Request-ID", description="请求追踪 ID（可选）",
    ),
):
    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"最多支持 {MAX_BATCH_SIZE} 张图片",
        )

    logger.info(
        "photo_analyze_batch start | request_id=%s merchant_id=%s files=%d ela_quality=%d",
        x_request_id or "-", merchant_id, len(files), ela_quality,
    )

    try:
        kyc_photo_prompt = load_prompt(f"{merchant_id}/kyc.photo.fraud.detect")
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未找到国家 [{merchant_id}] 对应的 KYC 照片检测提示词: {e}",
        )

    tasks = [
        _process_kyc_file(file, merchant_id, kyc_photo_prompt, ela_quality)
        for file in files
    ]
    outcomes = await asyncio.gather(*tasks)

    results = [item for item, _ in outcomes]
    success = sum(1 for _, ok in outcomes if ok)
    failed = len(outcomes) - success

    logger.info(
        "photo_analyze_batch done | request_id=%s total=%d success=%d failed=%d",
        x_request_id or "-", len(files), success, failed,
    )

    if failed == len(files):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="ALL_FILES_FAILED",
                detail="All files failed to process in photo-vlm-analyze",
            ).model_dump(),
        )

    return KycForensicsAnalyzeBatchResponse(
        total=len(files), success=success, failed=failed, results=results,
    )


@v1.post(
    "/kyc/exif-analyze",
    response_model=ExifAnalyzeBatchResponse,
    responses={500: {"model": ErrorResponse, "description": "处理错误"}},
    summary="KYC EXIF 提取与关键词检测",
    description="批量提取 EXIF 信息并基于关键词判断 PS/AIGC（无大模型调用）",
)
async def kyc_exif_analyze(
    files: list[UploadFile] = File(..., description="证件图片文件列表"),
    merchant_id: str = Form(..., description="国家", examples=[""]),
    x_request_id: Optional[str] = Header(
        default=None, alias="X-Request-ID", description="请求追踪 ID（可选）",
    ),
    _: None = Depends(verify_api_key),
):
    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"最多支持 {MAX_BATCH_SIZE} 张图片",
        )

    logger.info(
        "kyc_exif_analyze start | request_id=%s merchant_id=%s files=%d",
        x_request_id or "-", merchant_id, len(files),
    )

    tasks = [_process_exif_file(file, merchant_id) for file in files]
    outcomes = await asyncio.gather(*tasks)

    results = [item for item, _ in outcomes]
    success = sum(1 for _, ok in outcomes if ok)
    failed = len(outcomes) - success

    logger.info(
        "kyc_exif_analyze done | request_id=%s total=%d success=%d failed=%d",
        x_request_id or "-", len(files), success, failed,
    )

    if failed == len(files):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="ALL_FILES_FAILED",
                detail="All files failed to process in exif-analyze",
            ).model_dump(),
        )

    return ExifAnalyzeBatchResponse(
        total=len(files), success=success, failed=failed, results=results,
    )


@v1.post(
    "/kyc/idcard-vlm-analyze",
    response_model=IdcardFraudAnalyzeBatchResponse,
    responses={500: {"model": ErrorResponse, "description": "处理错误"}},
    summary="KYC 证件照 VLM 欺诈分析",
    description="上传证件照片，由大模型分析证件照可能存在的欺诈问题（按国家加载提示词）",
)
async def idcard_vlm_analyze(
    files: list[UploadFile] = File(..., description="证件图片文件列表"),
    merchant_id: str = Form(..., description="国家/地区代码", examples=["in"]),
    idcard_fields: str = Form(
        default="{}",
        description='证件原始字段 JSON（用户手填信息），例如 {"name":"John","id_number":"123456","dob":"1990-01-01"}',
    ),
    x_request_id: Optional[str] = Header(
        default=None, alias="X-Request-ID", description="请求追踪 ID（可选）",
    ),
):
    try:
        parsed_fields: dict[str, str] = json.loads(idcard_fields)
        if not isinstance(parsed_fields, dict):
            raise ValueError("idcard_fields 必须是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"idcard_fields 格式错误，需要合法 JSON 对象: {e}",
        )
    if len(files) > MAX_BATCH_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"最多支持 {MAX_BATCH_SIZE} 张图片",
        )
    try:
        idcard_prompt = load_prompt(f"{merchant_id}/kyc.idcard.fraud.detect")
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未找到国家 [{merchant_id}] 对应的证件照欺诈检测提示词: {e}",
        )
    logger.info(
        "idcard_vlm_analyze start | request_id=%s merchant_id=%s files=%d fields_keys=%s",
        x_request_id or "-", merchant_id, len(
            files), list(parsed_fields.keys()),
    )
    tasks = [
        _process_idcard_file(file, merchant_id, idcard_prompt, parsed_fields)
        for file in files
    ]
    outcomes = await asyncio.gather(*tasks)
    results = [item for item, _ in outcomes]
    success = sum(1 for _, ok in outcomes if ok)
    failed = len(outcomes) - success
    logger.info(
        "idcard_vlm_analyze done | request_id=%s total=%d success=%d failed=%d",
        x_request_id or "-", len(files), success, failed,
    )
    if failed == len(files):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="ALL_FILES_FAILED",
                detail="All files failed to process in idcard-vlm-analyze",
            ).model_dump(),
        )
    return IdcardFraudAnalyzeBatchResponse(
        total=len(files), success=success, failed=failed, results=results,
    )


@v1.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查",
    description="检查服务是否正常运行",
)
def health():
    return HealthResponse(status="ok", version=APP_VERSION)


app.include_router(v1)

# ---------------------------------------------------------------------------
# 页面 & 兼容路由
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/upload")


@app.get("/upload", response_class=HTMLResponse, include_in_schema=False)
def upload_page():
    html_path = _TEMPLATE_DIR / "upload.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
