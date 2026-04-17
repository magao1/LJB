# Unified Anti-Fraud API

**统一反欺诈 API** - 基于视觉语言模型（VLM）的多场景 KYC 欺诈检测服务

当前版本：`v0.3.0`

---

## 功能特点

- **KYC 照片综合取证检测**：规则层（EXIF + ELA + 高通滤波）与 VLM 多模态推理双重校验，判断证件照是否经过 PS 篡改或 AIGC 生成
- **KYC 证件欺诈检测**：VLM 读取证件原图，检测视觉真实性并与用户申报字段逐一比对（目前支持印尼 KTP）
- **KYC EXIF 快速检测**：纯规则层 EXIF 关键词扫描，无需 VLM 调用，速度最快
- **批量处理**：所有检测接口支持单次最多 10 张图片的并发处理
- **模块化 Prompt**：Prompt 以 Markdown 文件管理，按国家/地区分目录，便于版本控制和扩展

---

## 项目结构

```
UnifiedAntiFraudAPI/
├── app.py                          # FastAPI 应用入口（路由、鉴权、并发调度）
├── config.py                       # VLMConfig 配置类（环境变量加载）
├── requirements.txt                # Python 依赖清单
├── .env.example                    # 环境变量示例
├── models/
│   ├── __init__.py                 # 导出所有 Pydantic 数据模型
│   └── responses.py                # 响应体模型定义
├── prompts/
│   ├── __init__.py                 # load_prompt() 通用 Prompt 加载器
│   └── in/                         # 印度尼西亚（Indonesia）专属 Prompt
│       ├── kyc.photo.fraud.detect.md    # 照片综合取证检测（PS/AIGC/高风险场景）
│       └── kyc.idcard.fraud.detect.md  # 印尼 KTP 证件欺诈检测
├── templates/
│   └── upload.html                 # 浏览器测试 UI（无框架，原生 JS）
└── utils/
    ├── image_processing.py         # 图像处理工具集（ELA、高通滤波、EXIF 提取、规则检测）
    └── vlm_client.py               # VLM 异步 HTTP 客户端
```

---

## 环境要求

- Python 3.10+
- 阿里云 DashScope API Key（或任意兼容 OpenAI Chat Completions 格式的 VLM 服务）

---

## 安装与启动

**1. 安装依赖**

```bash
pip install -r requirements.txt
```

**2. 配置环境变量**

复制示例文件并填入 API 密钥：

```bash
cp .env.example .env
```

`.env` 文件内容示例：

```dotenv
VLM_API_KEY=sk-your-dashscope-api-key
VLM_MODEL=qwen-vl-max
VLM_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
VLM_MAX_TOKENS=500
VLM_TIMEOUT=120
SERVICE_API_KEY=your-service-api-key   # 可选，设置后部分接口需鉴权
LOG_LEVEL=INFO
CORS_ALLOW_ORIGINS=*
```

**3. 启动服务**

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

启动后可访问：

| 地址 | 说明 |
|------|------|
| http://localhost:8000/docs | Swagger 交互式 API 文档 |
| http://localhost:8000/redoc | ReDoc 文档 |
| http://localhost:8000/upload | 浏览器测试页面 |
| http://localhost:8000/v1/health | 健康检查 |

---

## 配置说明

所有配置通过环境变量（或 `.env` 文件）加载，**无需修改代码**。

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `VLM_API_KEY` | （必填） | VLM 服务 API 密钥 |
| `VLM_API_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | API 端点，兼容 OpenAI 格式 |
| `VLM_MODEL` | `qwen-vl-plus` | 模型名称 |
| `VLM_MAX_TOKENS` | `500` | 最大输出 Token 数 |
| `VLM_TIMEOUT` | `120` | 请求超时（秒） |
| `SERVICE_API_KEY` | （未设置） | 服务鉴权密钥，设置后 `/v1/kyc/exif-analyze` 需在 Header 携带 `X-API-Key` |
| `LOG_LEVEL` | `DEBUG` | 日志级别（DEBUG / INFO / WARNING / ERROR） |
| `CORS_ALLOW_ORIGINS` | `*` | CORS 允许来源，多个用英文逗号分隔 |

> 支持切换至任意兼容 OpenAI Chat Completions 格式的 VLM 服务（如 OpenAI GPT-4o、Azure OpenAI），修改 `VLM_API_URL` 和 `VLM_MODEL` 即可。

---

## API 接口详情

所有业务接口挂载于 `/v1` 前缀。

---

### `POST /v1/kyc/photo-vlm-analyze`

**KYC 照片综合取证检测**（规则 + VLM）

将原图、ELA 分析图、高通滤波图三张图片连同 EXIF 摘要和规则检测结果一并发送给 VLM，综合判断是否存在 PS 篡改、AIGC 生成或高风险场景。

**请求**（`multipart/form-data`）：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `files` | `File[]` | ✅ | - | 证件图片列表（最多 10 张，单文件最大 10 MB，支持 JPEG/PNG/GIF/BMP/WebP） |
| `merchant_id` | `str` | ✅ | - | 国家/地区代码，用于加载对应 Prompt（如 `in` 表示印尼） |
| `ela_quality` | `int` | ❌ | `90` | ELA JPEG 压缩质量（1-100） |

**请求 Header**：

| Header | 说明 |
|--------|------|
| `X-Request-ID` | 请求追踪 ID（可选） |

**处理流程**：

1. 校验图片文件合法性（文件头魔数 + PIL 校验）
2. 并发处理每张图片：
   - 提取 EXIF 摘要
   - 执行规则层检测（`detect_ps_crop_traces`）
   - 生成 ELA 图像
   - 生成高通滤波图像
   - 将三张图片 + EXIF 摘要 + 规则结果发送给 VLM
3. VLM 结果与 EXIF 关键词规则结果取"或"合并 `is_ps` / `is_aigc`

**响应示例**：

```json
{
  "total": 2,
  "success": 2,
  "failed": 0,
  "results": [
    {
      "merchant_id": "in",
      "filename": "id_1.jpg",
      "is_ps": true,
      "is_aigc": false,
      "is_high_risk_scene": false,
      "evidence": [
        "RULE: EXIF 软件字段显示存在编辑工具（Adobe Photoshop）",
        "VLM: 图像边缘存在明显的剪切光晕，ELA 分析显示局部区域压缩误差异常"
      ],
      "exif_summary": {
        "make": "Canon",
        "model": "EOS 5D",
        "software": "Adobe Photoshop 2024",
        "datetime_original": "2024:01:15 10:23:45"
      },
      "model": "qwen-vl-max"
    }
  ]
}
```

**证据前缀说明**：

| 前缀 | 含义 |
|------|------|
| `RULE:` | 来自规则层（EXIF / ELA / 高通滤波）的判断依据 |
| `VLM:` | 来自 VLM 多模态推理的判断依据 |
| `VLM_ERROR:` | VLM 调用出错，结果仅依赖规则层 |
| `ERROR:` | 图片处理过程中发生异常 |

---

### `POST /v1/kyc/idcard-vlm-analyze`

**KYC 证件欺诈检测**（VLM + 字段比对）

VLM 读取证件原图，检测视觉真实性，并将识别到的字段值与用户申报字段逐一比对。目前支持印尼 KTP，内置 NIK 编码规则（38 个省份代码、性别编码）。

**请求**（`multipart/form-data`）：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `files` | `File[]` | ✅ | - | 证件图片列表（最多 10 张） |
| `merchant_id` | `str` | ✅ | - | 国家/地区代码（如 `in`） |
| `idcard_fields` | `str`（JSON） | ✅ | - | 用户申报信息，JSON 字符串，字段名与目标国证件一致 |

**`idcard_fields` 示例**（印尼 KTP）：

```json
{
  "NIK": "3201011501980001",
  "Nama": "BUDI SANTOSO",
  "Tempat/Tgl Lahir": "JAKARTA, 15-01-1998",
  "Jenis Kelamin": "LAKI-LAKI",
  "Alamat": "JL. MERDEKA NO. 1 RT 001/RW 002"
}
```

**请求 Header**：

| Header | 说明 |
|--------|------|
| `X-Request-ID` | 请求追踪 ID（可选） |

**响应示例**：

```json
{
  "total": 1,
  "success": 1,
  "failed": 0,
  "results": [
    {
      "merchant_id": "in",
      "filename": "ktp.jpg",
      "is_fraud": false,
      "has_mismatch": true,
      "evidence": [
        "字段 Nama 不一致：申报 BUDI SANTOSO，照片识别 BUDI SUSANTO"
      ],
      "field_mismatches": [
        {
          "field": "Nama",
          "declared_value": "BUDI SANTOSO",
          "photo_value": "BUDI SUSANTO",
          "status": "mismatch"
        },
        {
          "field": "NIK",
          "declared_value": "3201011501980001",
          "photo_value": "3201011501980001",
          "status": "match"
        }
      ],
      "model": "qwen-vl-max"
    }
  ]
}
```

**`field_mismatches[].status` 取值**：

| 值 | 含义 |
|----|------|
| `match` | 申报值与照片识别值一致 |
| `mismatch` | 申报值与照片识别值不一致 |
| `unreadable` | 照片中该字段无法识别 |

---

### `POST /v1/kyc/exif-analyze`

**KYC EXIF 快速检测**（纯规则层，不调用 VLM）

仅提取 EXIF 元数据并基于关键词列表判断 PS/AIGC，无 VLM 调用，响应速度最快。

**请求**（`multipart/form-data`）：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `files` | `File[]` | ✅ | 证件图片列表（最多 10 张） |
| `merchant_id` | `str` | ✅ | 国家/地区代码 |

**请求 Header**：

| Header | 说明 |
|--------|------|
| `X-Request-ID` | 请求追踪 ID（可选） |
| `X-API-Key` | 服务鉴权密钥（若配置了 `SERVICE_API_KEY` 则必填） |

**响应示例**：

```json
{
  "total": 1,
  "success": 1,
  "failed": 0,
  "results": [
    {
      "merchant_id": "in",
      "filename": "id_1.jpg",
      "is_ps": true,
      "is_aigc": false,
      "evidence": [
        "RULE: EXIF 软件字段包含 PS 工具关键词：photoshop"
      ],
      "exif_summary": {
        "make": "Canon",
        "model": "EOS 5D",
        "software": "Adobe Photoshop 2024"
      }
    }
  ]
}
```

---

### `GET /v1/health`

**健康检查**

```json
{
  "status": "ok",
  "version": "0.3.0"
}
```

---

### 页面路由

| 路由 | 说明 |
|------|------|
| `GET /` | 重定向至 `/upload` |
| `GET /upload` | 浏览器测试页面，支持综合检测和 EXIF 检测两种模式切换、拖拽上传、高级参数配置 |

---

## 鉴权说明

| 接口 | 鉴权方式 |
|------|----------|
| `POST /v1/kyc/photo-vlm-analyze` | 无强制鉴权 |
| `POST /v1/kyc/idcard-vlm-analyze` | 无强制鉴权 |
| `POST /v1/kyc/exif-analyze` | 若配置了 `SERVICE_API_KEY`，需携带 Header `X-API-Key: <key>` |
| `GET /v1/health` | 无鉴权 |

> 若未配置 `SERVICE_API_KEY`，所有接口均无需鉴权（适合开发/内网环境）。

---

## 调用示例

### curl

```bash
# KYC 照片综合取证检测
curl -X POST "http://localhost:8000/v1/kyc/photo-vlm-analyze" \
  -F "files=@test1.jpg" \
  -F "files=@test2.jpg" \
  -F "merchant_id=in" \
  -F "ela_quality=90"

# KYC 证件欺诈检测（含字段比对）
curl -X POST "http://localhost:8000/v1/kyc/idcard-vlm-analyze" \
  -F "files=@ktp.jpg" \
  -F "merchant_id=in" \
  -F 'idcard_fields={"NIK":"3201011501980001","Nama":"BUDI SANTOSO"}'

# EXIF 快速检测
curl -X POST "http://localhost:8000/v1/kyc/exif-analyze" \
  -H "X-API-Key: your-service-api-key" \
  -F "files=@test.jpg" \
  -F "merchant_id=in"
```

### Python

```python
import httpx

# KYC 照片综合取证检测
with open("test.jpg", "rb") as f:
    response = httpx.post(
        "http://localhost:8000/v1/kyc/photo-vlm-analyze",
        files=[("files", ("test.jpg", f, "image/jpeg"))],
        data={"merchant_id": "in", "ela_quality": "90"},
    )
print(response.json())

# KYC 证件欺诈检测
import json

idcard_fields = json.dumps({
    "NIK": "3201011501980001",
    "Nama": "BUDI SANTOSO",
    "Jenis Kelamin": "LAKI-LAKI",
})
with open("ktp.jpg", "rb") as f:
    response = httpx.post(
        "http://localhost:8000/v1/kyc/idcard-vlm-analyze",
        files=[("files", ("ktp.jpg", f, "image/jpeg"))],
        data={"merchant_id": "in", "idcard_fields": idcard_fields},
    )
print(response.json())
```

---

## 技术原理

### ELA（Error Level Analysis，错误级别分析）

JPEG 图像每次重新压缩都会引入新的压缩误差。若图像某区域经过后期编辑（粘贴、替换），该区域与原始区域的误差水平会不一致。

处理步骤：
1. 以指定质量（默认 90）重新压缩原图
2. 计算原图与重压缩图的像素差
3. 放大差异（乘以亮度系数）生成 ELA 图
4. 篡改区域在 ELA 图中显示为异常亮块

### 高通滤波（High-Pass Filter）

强化图像边缘特征，暴露非自然的切割边界。基于 PIL `ImageFilter.FIND_EDGES` 实现（无 OpenCV 依赖）。

处理步骤：
1. 转为灰度图
2. 应用边缘检测滤波器
3. 归一化到 0-255
4. 二次贴附的矩形边界呈现为尖锐白线

### 规则层检测（`detect_ps_crop_traces`）

综合 7 个维度评分（每项命中 +20 分，最高 100 分）：

| 检测维度 | 说明 |
|----------|------|
| EXIF 软件字段 | 包含 PS/AIGC 工具关键词 |
| 拍摄时间缺失 | 缺少 `DateTimeOriginal` 或 `DateTimeDigitized` |
| 尺寸不一致 | EXIF 记录尺寸与图像实际尺寸不符（疑似裁剪） |
| 方向标记异常 | EXIF 旋转方向与图像宽高关系矛盾 |
| 缩略图比例异常 | 缩略图与原图宽高比差异 > 0.02 |
| JPEG 量化表异常 | 量化表缺失或异常 |
| 设备信息缺失 | 缺少 `Make` / `Model` 字段 |

**PS 关键词**：`photoshop`, `lightroom`, `gimp`, `snapseed`, `meitu`, `picsart`, `facetune`, `vsco`

**AIGC 关键词**：`ai`, `aigc`, `midjourney`, `stable diffusion`, `generative`

### Prompt 系统

`load_prompt(prompt_name)` 按路径 `prompts/{prompt_name}.md` 加载 Markdown 文本，支持国家子路径。

```python
load_prompt("in/kyc.photo.fraud.detect")   # → prompts/in/kyc.photo.fraud.detect.md
load_prompt("in/kyc.idcard.fraud.detect")  # → prompts/in/kyc.idcard.fraud.detect.md
```

### VLM 客户端

`VLMClient`（`utils/vlm_client.py`）基于 `httpx.AsyncClient` 异步调用，特性：
- 遵循 OpenAI Chat Completions API 格式，支持多模态（文本 + 多张图片 Base64）
- 三层 JSON 容错解析：直接解析 → 正则提取 `{...}` → 正则提取 ` ```json...``` `
- 模块级连接池单例，复用 HTTP 连接
- 超时/网络错误统一返回 `{"error": ...}`，不抛异常

---

## 数据模型

### `KycForensicsAnalyzeItem`（综合取证检测结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| `merchant_id` | `str` | 国家/地区代码 |
| `filename` | `str` | 文件名 |
| `is_ps` | `bool \| None` | 是否存在 PS 篡改 |
| `is_aigc` | `bool \| None` | 是否为 AIGC 生成 |
| `is_high_risk_scene` | `bool \| None` | 是否为高风险场景（如翻拍、屏幕截图等） |
| `evidence` | `list[str]` | 综合证据列表 |
| `exif_summary` | `dict \| None` | EXIF 元数据摘要（70+ 字段） |
| `model` | `str \| None` | 所用 VLM 模型名 |

### `ExifAnalyzeItem`（EXIF 快速检测结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| `merchant_id` | `str` | 国家/地区代码 |
| `filename` | `str` | 文件名 |
| `is_ps` | `bool \| None` | 是否存在 PS 篡改 |
| `is_aigc` | `bool \| None` | 是否为 AIGC 生成 |
| `evidence` | `list[str]` | 规则层证据列表 |
| `exif_summary` | `dict \| None` | EXIF 元数据摘要 |

### `IdcardFraudAnalyzeItem`（证件欺诈检测结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| `merchant_id` | `str` | 国家/地区代码 |
| `filename` | `str` | 文件名 |
| `is_fraud` | `bool \| None` | 是否存在证件欺诈 |
| `has_mismatch` | `bool \| None` | 申报字段与照片是否存在不一致 |
| `evidence` | `list[str]` | 欺诈/篡改证据列表 |
| `field_mismatches` | `list[IdcardFieldMismatch]` | 字段逐一比对结果 |
| `model` | `str \| None` | 所用 VLM 模型名 |

---

## 依赖清单

| 包 | 版本 | 用途 |
|----|------|------|
| `fastapi` | 0.104.1 | Web 框架 |
| `uvicorn` | 0.24.0.post1 | ASGI 服务器 |
| `pillow` | 10.0.1 | 图像处理（ELA、EXIF、高通滤波） |
| `numpy` | 1.26.0 | 数值计算（ELA 差值放大） |
| `pydantic` | 2.5.0 | 数据模型与校验 |
| `python-multipart` | 0.0.6 | multipart/form-data 文件上传支持 |
| `httpx` | 0.25.0 | 异步 HTTP 客户端（调用 VLM） |
| `python-dotenv` | 1.0.1 | 加载 `.env` 文件 |

> 无 OpenCV 依赖，高通滤波使用 PIL 原生滤波器实现。

---

## 常见问题

**Q: 启动时提示 `VLM_API_KEY is not set`**

在 `.env` 文件或环境变量中配置 `VLM_API_KEY`。

**Q: 调用返回 `HTTP 401 Unauthorized`**

检查 VLM API 密钥是否正确，或 `/v1/kyc/exif-analyze` 请求是否携带了 `X-API-Key` Header。

**Q: 请求超时 `Request timeout after 120s`**

- 增大超时：`VLM_TIMEOUT=300`
- 检查网络至 DashScope 的连通性
- 适当压缩图片尺寸后重试

**Q: 启动失败 `ModuleNotFoundError`**

使用模块方式启动以确保项目根目录在 `sys.path` 中：

```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

**Q: 如何扩展支持其他国家的证件检测？**

在 `prompts/` 下新建对应国家代码子目录（如 `prompts/ph/`），参照印尼 Prompt 格式创建 `.md` 文件，调用时传入对应的 `merchant_id` 即可。
