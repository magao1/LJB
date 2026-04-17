from __future__ import annotations

import base64
import hashlib
import io

import numpy as np
from PIL import Image, ImageChops, ImageEnhance, ExifTags, ImageFilter

# 模块级反向索引，import 时只计算一次，将 _get 查找从 O(n) 降为 O(1)
_TAG_NAME_TO_ID: dict = {name: tag_id for tag_id, name in ExifTags.TAGS.items()}
_GPS_NAME_TO_ID: dict = {name: tag_id for tag_id, name in ExifTags.GPSTAGS.items()}

# 统一关键词列表，detect_ps_crop_traces 与 exif_keyword_flags 共享同一份定义
_PS_KEYWORDS = [
    "photoshop", "lightroom", "gimp", "snapseed", "meitu",
    "picsart", "facetune", "vsco",
]
_AIGC_KEYWORDS = [
    "ai", "aigc", "midjourney", "stable diffusion", "generative",
]
_ALL_EDIT_KEYWORDS = _PS_KEYWORDS + _AIGC_KEYWORDS

# 允许的图片 MIME 类型
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/bmp", "image/webp"}
# 最大文件大小 (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


def validate_image_file(file_bytes: bytes, filename: str) -> tuple[bool, str]:
    """
    验证上传的文件是否为有效图片

    Args:
        file_bytes: 文件字节数据
        filename: 文件名

    Returns:
        (是否有效, 错误信息)
    """
    if not file_bytes:
        return False, "Empty file"

    if len(file_bytes) > MAX_FILE_SIZE:
        return False, f"File too large: {len(file_bytes) / 1024 / 1024:.2f}MB (max: 10MB)"

    # 检查文件头判断真实类型
    image_signatures = {
        b'\xff\xd8\xff': 'image/jpeg',
        b'\x89PNG\r\n\x1a\n': 'image/png',
        b'GIF87a': 'image/gif',
        b'GIF89a': 'image/gif',
        b'BM': 'image/bmp',
        b'RIFF': 'image/webp',
    }

    is_valid_image = False
    for sig, mime_type in image_signatures.items():
        if file_bytes.startswith(sig):
            is_valid_image = True
            break

    if not is_valid_image:
        return False, "Invalid image format"

    # 尝试用 PIL 打开验证
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.verify()
    except Exception as e:
        return False, f"Cannot open image: {str(e)}"

    return True, ""


def read_image_from_bytes(image_bytes: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image_bytes)).convert("RGB")


def to_base64_jpeg(pil_img: Image.Image, quality: int = 92) -> str:
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def generate_ela_image(image_bytes: bytes, quality: int = 90) -> Image.Image:
    original = read_image_from_bytes(image_bytes)

    buf_compressed = io.BytesIO()
    original.save(buf_compressed, format="JPEG", quality=quality)
    buf_compressed.seek(0)
    compressed = Image.open(buf_compressed).convert("RGB")

    diff = ImageChops.difference(original, compressed)
    extrema = diff.getextrema()
    max_diff = max([ex[1] for ex in extrema]) or 1
    scale = 255.0 / max_diff
    ela_image = ImageEnhance.Brightness(diff).enhance(scale)
    return ela_image


def high_pass_filter(image_bytes: bytes) -> Image.Image:
    """
    简化版高通滤波：使用边缘检测替代 OpenCV Laplacian，
    以避免引入重量级 opencv-python 依赖。
    """
    pil_img = read_image_from_bytes(image_bytes).convert("L")
    edges = pil_img.filter(ImageFilter.FIND_EDGES)
    hp_rgb = Image.merge("RGB", (edges, edges, edges))
    return hp_rgb


def _gps_to_decimal(gps_coord, gps_ref) -> float | None:
    if not gps_coord or not gps_ref:
        return None
    try:
        d, m, s = gps_coord
        deg = float(d[0]) / float(d[1])
        minutes = float(m[0]) / float(m[1])
        seconds = float(s[0]) / float(s[1])
        value = deg + (minutes / 60.0) + (seconds / 3600.0)
        if gps_ref in ["S", "W"]:
            value = -value
        return round(value, 6)
    except (ValueError, TypeError, IndexError, ZeroDivisionError):
        return None


def extract_exif_summary(image_bytes: bytes) -> dict:
    """提取 EXIF 摘要并进行归一化"""
    img = Image.open(io.BytesIO(image_bytes))
    exif = img.getexif()

    def _get(tag_name: str):
        tag_id = _TAG_NAME_TO_ID.get(tag_name)
        return exif.get(tag_id) if tag_id is not None else None

    def _get_gps(tag_name: str):
        tag_id = _GPS_NAME_TO_ID.get(tag_name)
        return gps_info.get(tag_id) if tag_id is not None else None

    def _as_str(value):
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8", errors="ignore")
            except (UnicodeDecodeError, AttributeError):
                return None
        return str(value)

    def _as_list(value):
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    gps_info = exif.get(0x8825)
    gps_data = {}
    if gps_info:
        gps_map = ExifTags.GPSTAGS
        for k, v in gps_info.items():
            gps_data[gps_map.get(k, k)] = v

    lat = _gps_to_decimal(gps_data.get("GPSLatitude"), gps_data.get("GPSLatitudeRef"))
    lon = _gps_to_decimal(gps_data.get("GPSLongitude"), gps_data.get("GPSLongitudeRef"))

    thumbnail_bytes = None
    if isinstance(img.info.get("thumbnail"), (bytes, bytearray)):
        thumbnail_bytes = img.info.get("thumbnail")
    else:
        try:
            thumbnail_bytes = exif.get_thumbnail()
        except (AttributeError, OSError):
            thumbnail_bytes = None

    thumb_size = None
    thumb_hash = None
    if thumbnail_bytes:
        try:
            thumb_img = Image.open(io.BytesIO(thumbnail_bytes))
            thumb_size = list(thumb_img.size)
            thumb_hash = hashlib.sha256(thumbnail_bytes).hexdigest()[:16]
        except (OSError, IOError):
            thumb_size = None
            thumb_hash = None

    summary = {
        "format": img.format,
        "image_width": img.size[0],
        "image_height": img.size[1],
        "image_description": _as_str(_get("ImageDescription")),
        "make": _get("Make"),
        "model": _get("Model"),
        "orientation": _get("Orientation"),
        "x_resolution": _as_str(_get("XResolution")),
        "y_resolution": _as_str(_get("YResolution")),
        "resolution_unit": _as_str(_get("ResolutionUnit")),
        "thumbnail_offset": _as_str(_get("ThumbnailOffset")),
        "thumbnail_length": _as_str(_get("ThumbnailLength")),
        "thumbnail_width_pixels": thumb_size[0] if thumb_size else None,
        "thumbnail_height_pixels": thumb_size[1] if thumb_size else None,
        "software": _get("Software"),
        "processing_software": _get("ProcessingSoftware"),
        "datetime": _get("DateTime"),
        "datetime_original": _get("DateTimeOriginal"),
        "datetime_digitized": _get("DateTimeDigitized"),
        "exif_image_width": _get("ExifImageWidth"),
        "exif_image_height": _get("ExifImageHeight"),
        "bits_per_sample": _as_list(_get("BitsPerSample")),
        "y_cb_cr_positioning": _as_str(_get("YCbCrPositioning")),
        "document_name": _as_str(_get("DocumentName")),
        "exposure_time": _as_str(_get("ExposureTime")),
        "aperture_value": _as_str(_get("ApertureValue")),
        "exposure_program": _as_str(_get("ExposureProgram")),
        "iso_speed_ratings": _as_str(_get("ISOSpeedRatings")),
        "exif_version": _as_str(_get("ExifVersion")),
        "components_configuration": _as_str(_get("ComponentsConfiguration")),
        "shutter_speed_value": _as_str(_get("ShutterSpeedValue")),
        "brightness_value": _as_str(_get("BrightnessValue")),
        "exposure_bias_value": _as_str(_get("ExposureBiasValue")),
        "metering_mode": _as_str(_get("MeteringMode")),
        "white_balance": _as_str(_get("WhiteBalance")),
        "flash": _as_str(_get("Flash")),
        "focal_length": _as_str(_get("FocalLength")),
        "sub_sec_time": _as_str(_get("SubSecTime")),
        "subsec_time_original": _as_str(_get("SubSecTimeOriginal")),
        "subsec_time_digitized": _as_str(_get("SubSecTimeDigitized")),
        "flash_pix_version": _as_str(_get("FlashPixVersion")),
        "color_space": _as_str(_get("ColorSpace")),
        "color_model": _as_str(_get("PhotometricInterpretation")),
        "sensing_method": _as_str(_get("SensingMethod")),
        "file_source": _as_str(_get("FileSource")),
        "scene_type": _as_str(_get("SceneType")),
        "custom_rendered": _as_str(_get("CustomRendered")),
        "exposure_mode": _as_str(_get("ExposureMode")),
        "focal_length_35": _as_str(_get("FocalLengthIn35mmFilm")),
        "scene_capture_type": _as_str(_get("SceneCaptureType")),
        "gain_control": _as_str(_get("GainControl")),
        "contrast": _as_str(_get("Contrast")),
        "saturation": _as_str(_get("Saturation")),
        "sharpness": _as_str(_get("Sharpness")),
        "subject_distance_range": _as_str(_get("SubjectDistanceRange")),
        "interoperability_index": _as_str(_get("InteroperabilityIndex")),
        "interoperability_version": _as_str(_get("InteroperabilityVersion")),
        "compression": _as_str(_get("Compression")),
        "data_precision": _as_str(_get("DataPrecision")),
        "f_number": _as_str(_get("FNumber")),
        "maker_note": _as_str(_get("MakerNote")),
        "number_of_tables": _as_str(_get("NumberOfTables")),
        "pixel_x_dimension": _as_str(_get("PixelXDimension")),
        "pixel_y_dimension": _as_str(_get("PixelYDimension")),
        "lens_specification": _as_str(_get("LensSpecification")),
        "lens_model": _as_str(_get("LensModel")),
        "lens_make": _as_str(_get("LensMake")),
        "offset_time_original": _as_str(_get("OffsetTimeOriginal")),
        "offset_time": _as_str(_get("OffsetTime")),
        "offset_time_digitized": _as_str(_get("OffsetTimeDigitized")),
        "composite_image": _as_str(_get("CompositeImage")),
        "subject_area": _as_str(_get("SubjectArea")),
        "host_computer": _as_str(_get("HostComputer")),
        "gps_version_id": _as_str(_get_gps("GPSVersionID")) if gps_info else None,
        "gps_latitude_ref": _as_str(_get_gps("GPSLatitudeRef")) if gps_info else None,
        "gps_latitude": _as_str(_get_gps("GPSLatitude")) if gps_info else None,
        "gps_longitude_ref": _as_str(_get_gps("GPSLongitudeRef")) if gps_info else None,
        "gps_longitude": _as_str(_get_gps("GPSLongitude")) if gps_info else None,
        "gps_altitude_ref": _as_str(_get_gps("GPSAltitudeRef")) if gps_info else None,
        "gps_altitude": _as_str(_get_gps("GPSAltitude")) if gps_info else None,
        "gps_time_stamp": _as_str(_get_gps("GPSTimeStamp")) if gps_info else None,
        "gps_processing_method": _as_str(_get_gps("GPSProcessingMethod")) if gps_info else None,
        "gps_date_stamp": _as_str(_get_gps("GPSDateStamp")) if gps_info else None,
        "gps_present": bool(gps_info),
        "gps_lat": lat,
        "gps_lon": lon,
        "thumbnail_present": bool(thumbnail_bytes),
        "thumbnail_size": thumb_size,
        "thumbnail_hash": thumb_hash,
    }
    return summary


def detect_ps_crop_traces(image_bytes: bytes, exif_summary: dict) -> dict:
    """基于 EXIF 与基础规则的 PS/裁剪痕迹检测"""
    img = Image.open(io.BytesIO(image_bytes))
    evidence: list[str] = []
    flags: dict = {}

    software = (exif_summary.get("software") or "")
    processing = (exif_summary.get("processing_software") or "")
    software_text = f"{software} {processing}".lower()

    if any(k in software_text for k in _ALL_EDIT_KEYWORDS):
        flags["edited_software"] = True
        evidence.append("EXIF 软件字段显示存在编辑/生成工具")

    if not exif_summary.get("datetime_original") and not exif_summary.get("datetime_digitized"):
        flags["missing_original_datetime"] = True
        evidence.append("EXIF 缺少原始拍摄时间字段")

    exif_w = exif_summary.get("exif_image_width")
    exif_h = exif_summary.get("exif_image_height")
    if exif_w and exif_h:
        if int(exif_w) != img.size[0] or int(exif_h) != img.size[1]:
            flags["exif_size_mismatch"] = True
            evidence.append("EXIF 尺寸与实际图像尺寸不一致（可能裁剪/重采样）")

    orientation = exif_summary.get("orientation")
    if orientation in [5, 6, 7, 8] and (img.size[0] > img.size[1]):
        flags["orientation_suspect"] = True
        evidence.append("EXIF 方向标记与图像宽高关系异常")

    thumb_size = exif_summary.get("thumbnail_size")
    if thumb_size:
        img_ratio = img.size[0] / max(img.size[1], 1)
        thumb_ratio = thumb_size[0] / max(thumb_size[1], 1)
        if abs(img_ratio - thumb_ratio) > 0.02:
            flags["thumbnail_ratio_mismatch"] = True
            evidence.append("缩略图与原图比例不一致（可能裁剪）")

    if img.format == "JPEG":
        quant_tables = getattr(img, "quantization", None)
        quant_count = len(quant_tables) if quant_tables else 0
        if quant_count == 0:
            flags["jpeg_quant_missing"] = True
            evidence.append("JPEG 量化表缺失或异常")
    else:
        flags["non_jpeg"] = True
        evidence.append("非 JPEG 格式，EXIF/压缩痕迹可能不完整")

    if not exif_summary.get("make") and not exif_summary.get("model"):
        flags["missing_device"] = True
        evidence.append("EXIF 缺少设备信息（Make/Model）")

    risk_score = min(100, len(evidence) * 20)
    return {
        "risk_score": risk_score,
        "flags": flags,
        "evidence": evidence,
    }


def exif_keyword_flags(exif_summary: dict) -> tuple[bool, bool, list[str]]:
    """基于统一关键词列表判断 PS/AIGC"""
    software = (exif_summary.get("software") or "")
    processing = (exif_summary.get("processing_software") or "")
    text = f"{software} {processing}".lower()
    is_ps = any(k in text for k in _PS_KEYWORDS)
    is_aigc = any(k in text for k in _AIGC_KEYWORDS)
    evidence: list[str] = []
    if is_ps:
        evidence.append("RULE: EXIF 软件字段显示存在编辑工具")
    if is_aigc:
        evidence.append("RULE: EXIF 软件字段显示存在 AIGC 工具")
    return is_ps, is_aigc, evidence