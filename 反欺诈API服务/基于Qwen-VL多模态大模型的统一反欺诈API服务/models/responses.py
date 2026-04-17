from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class KycForensicsAnalyzeItem(BaseModel):
    """KYC 校验单项结果（VLM 综合）"""

    merchant_id: str = Field(description="国家")
    filename: str = Field(description="文件名")
    is_ps: bool | None = Field(default=None, description="是否 PS 篡改（失败时为空）")
    is_aigc: bool | None = Field(default=None, description="是否 AIGC 篡改（失败时为空）")
    is_high_risk_scene: bool | None = Field(default=None, description="是否高风险场景（失败时为空）")
    evidence: list[str] = Field(default_factory=list, description="综合证据列表")
    exif_summary: dict[str, Any] | None = Field(default=None, description="EXIF 摘要")
    model: str | None = Field(default=None, description="使用的 VLM 模型名称")


class KycForensicsAnalyzeBatchResponse(BaseModel):
    """KYC 校验批量响应"""

    total: int = Field(description="总文件数")
    success: int = Field(description="检测成功数量")
    failed: int = Field(description="检测失败数量")
    results: list[KycForensicsAnalyzeItem] = Field(
        default_factory=list, description="每个文件的检测结果"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "total": 2,
                "success": 2,
                "failed": 0,
                "results": [
                    {
                        "merchant_id": "ph",
                        "filename": "id_1.jpg",
                        "is_ps": True,
                        "is_aigc": False,
                        "is_high_risk_scene": False,
                        "evidence": [
                            "RULE: EXIF 软件字段显示存在编辑工具",
                            "REPASTE: 照片剪切粘贴: ELA图像 — 照片框左上角 — 明显的亮光晕",
                            "VLM: 人像边缘融合异常",
                        ],
                        "model": "qwen-vl-max",
                    },
                    {
                        "merchant_id": "ph",
                        "filename": "id_2.jpg",
                        "is_ps": False,
                        "is_aigc": False,
                        "evidence": [],
                        "exif_summary": {"make": "Canon", "model": "EOS"},
                        "model": None,
                    },
                ],
            }
        }


class IdcardFieldMismatch(BaseModel):
    """单个字段的比对结果"""

    field: str = Field(description="字段名")
    declared_value: str = Field(description="用户申报值")
    photo_value: str = Field(description="照片上识别到的值")
    status: str = Field(description="比对结果：match / mismatch / unreadable")


class IdcardFraudAnalyzeItem(BaseModel):
    """证件照 VLM 欺诈分析单项结果"""

    merchant_id: str = Field(description="国家/地区代码")
    filename: str = Field(description="文件名")
    is_fraud: bool | None = Field(default=None, description="是否存在欺诈迹象（失败时为空）")
    has_mismatch: bool | None = Field(default=None, description="证件信息与申报信息是否存在不一致（失败时为空）")
    evidence: list[str] = Field(default_factory=list, description="欺诈/篡改证据列表")
    field_mismatches: list[IdcardFieldMismatch] = Field(default_factory=list, description="字段比对结果")
    model: str | None = Field(default=None, description="使用的 VLM 模型名称")


class IdcardFraudAnalyzeBatchResponse(BaseModel):
    """证件照 VLM 欺诈分析批量响应"""

    total: int = Field(description="总文件数")
    success: int = Field(description="分析成功数量")
    failed: int = Field(description="分析失败数量")
    results: list[IdcardFraudAnalyzeItem] = Field(
        default_factory=list, description="每个文件的分析结果"
    )


class ExifAnalyzeItem(BaseModel):
    """EXIF 单项结果"""

    merchant_id: str = Field(description="国家")
    filename: str = Field(description="文件名")
    is_ps: bool | None = Field(default=None, description="是否 PS 篡改（失败时为空）")
    is_aigc: bool | None = Field(default=None, description="是否 AIGC 篡改（失败时为空）")
    evidence: list[str] = Field(default_factory=list, description="关键词证据列表")
    exif_summary: dict[str, Any] | None = Field(default=None, description="EXIF 摘要")


class ExifAnalyzeBatchResponse(BaseModel):
    """EXIF 解析批量响应（/v1/kyc/exif-analyze）"""

    total: int = Field(description="总文件数")
    success: int = Field(description="解析成功数量")
    failed: int = Field(description="解析失败数量")
    results: list[ExifAnalyzeItem] = Field(
        default_factory=list, description="每个文件的解析结果"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "total": 1,
                "success": 1,
                "failed": 0,
                "results": [
                    {
                        "merchant_id": "ph",
                        "filename": "id_1.jpg",
                        "is_ps": True,
                        "is_aigc": False,
                        "evidence": ["RULE: EXIF 软件字段显示存在编辑工具"],
                        "exif_summary": {
                            "make": "Canon",
                            "model": "EOS",
                            "software": "Adobe Photoshop",
                        },
                    }
                ],
            }
        }


class ErrorResponse(BaseModel):
    """错误响应"""

    error: str = Field(description="错误信息")
    detail: str | None = Field(default=None, description="详细错误信息（可选）")

    class Config:
        json_schema_extra = {
            "example": {
                "error": "VLM API key is missing",
                "detail": "Please provide api_key in request or set VLM_API_KEY environment variable",
            }
        }


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(description="服务状态")
    version: str = Field(description="API 版本")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "ok",
                "version": "0.3.0",
            }
        }
