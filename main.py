import os
import json
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any

import cv2
import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Optional Gemini import
try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None


# --------------------------------------------------
# Paths and environment
# --------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

load_dotenv()

APP_TITLE = os.getenv("APP_TITLE", "Filipino Sign Language Video-to-Label App")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

LABELS_FILE = os.getenv("LABELS_FILE", "labels.txt").strip()

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
MAX_RECORD_SECONDS = int(os.getenv("MAX_RECORD_SECONDS", "4"))
MAX_FRAMES_TO_SEND = int(os.getenv("MAX_FRAMES_TO_SEND", "8"))
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "85"))

# PostgreSQL config
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost").strip()
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "").strip()
POSTGRES_USER = os.getenv("POSTGRES_USER", "").strip()
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "").strip()
POSTGRES_SCHEMA = os.getenv("POSTGRES_SCHEMA", "dbo").strip()
POSTGRES_APIKEY_TABLE = os.getenv("POSTGRES_APIKEY_TABLE", "APIKeyManagement").strip()
POSTGRES_SSLMODE = os.getenv("POSTGRES_SSLMODE", "prefer").strip()


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def resolve_page_file(filename: str) -> Path:
    """
    Resolve HTML page location.
    Priority:
    1) /templates/<filename>
    2) /static/<filename>
    """
    candidates = [
        TEMPLATES_DIR / filename,
        STATIC_DIR / filename,
    ]

    for path in candidates:
        if path.exists():
            return path

    # fallback to templates path for clearer error location
    return TEMPLATES_DIR / filename


def load_labels() -> List[str]:
    label_path = BASE_DIR / LABELS_FILE

    if label_path.exists():
        labels = [
            line.strip().upper()
            for line in label_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if labels:
            unique = []
            seen = set()
            for item in labels:
                if item not in seen:
                    seen.add(item)
                    unique.append(item)
            return unique

    return [
        "HELLO",
        "THANK_YOU",
        "YES",
        "NO",
        "PLEASE",
        "SORRY",
        "I_LOVE_YOU",
        "UNKNOWN",
    ]


FSL_LABELS = load_labels()


def safe_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def error_result(message: str) -> dict:
    return {
        "success": False,
        "mode": "error",
        "label": "UNKNOWN",
        "confidence": 0.0,
        "top_k": [],
        "message": message,
    }


def evenly_spaced_indices(total: int, count: int) -> List[int]:
    if total <= 0:
        return []
    if total <= count:
        return list(range(total))

    indices = []
    for i in range(count):
        pos = int(round(i * (total - 1) / (count - 1)))
        indices.append(pos)

    return sorted(list(set(indices)))


def extract_sample_frames(video_path: Path, max_frames: int, jpeg_quality: int) -> List[bytes]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    extracted: List[bytes] = []

    try:
        if total_frames <= 0:
            raw_frames = []
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                raw_frames.append(frame)

            if not raw_frames:
                return []

            indices = evenly_spaced_indices(len(raw_frames), max_frames)
            chosen_frames = [raw_frames[i] for i in indices]
        else:
            indices = evenly_spaced_indices(total_frames, max_frames)
            chosen_frames = []

            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if ok and frame is not None:
                    chosen_frames.append(frame)

        for frame in chosen_frames:
            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality],
            )
            if ok:
                extracted.append(encoded.tobytes())

        return extracted
    finally:
        cap.release()


def parse_model_json(text: str) -> dict:
    text = text.strip()

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        return json.loads(candidate)

    raise ValueError("No valid JSON found in model response.")


# --------------------------------------------------
# PostgreSQL API key rotation
# --------------------------------------------------
def db_is_configured() -> bool:
    ok = all([
        bool(POSTGRES_HOST),
        bool(POSTGRES_PORT),
        bool(POSTGRES_DB),
        bool(POSTGRES_USER),
        bool(POSTGRES_PASSWORD),
    ])
    print(
        f"[DB] Config check: host={POSTGRES_HOST}, port={POSTGRES_PORT}, "
        f"db={POSTGRES_DB}, user={POSTGRES_USER}, password_set={bool(POSTGRES_PASSWORD)}"
    )
    return ok


def get_db_connection():
    return psycopg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        autocommit=False,
        sslmode=POSTGRES_SSLMODE,
    )


def reserve_least_used_api_key(exclude_ids: Optional[set[int]] = None) -> Optional[Dict[str, Any]]:
    """
    Select the least-used active API key.
    No Name filter.
    Optional exclude_ids lets us skip keys already tried in this request.
    """
    if not db_is_configured():
        print("[DB] PostgreSQL config is incomplete.")
        return None

    exclude_ids = exclude_ids or set()

    base_sql = f'''
        SELECT "Id", "Email", "Name", "APIKey", "Usage"
        FROM {POSTGRES_SCHEMA}."{POSTGRES_APIKEY_TABLE}"
        WHERE "Active" = true
    '''

    params: List[Any] = []

    if exclude_ids:
        base_sql += ' AND "Id" <> ALL(%s)'
        params.append(list(exclude_ids))

    base_sql += '''
        ORDER BY "Usage" ASC, "Id" ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    '''

    print("[DB] Running reserve_least_used_api_key()")
    print(f"[DB] Schema={POSTGRES_SCHEMA}")
    print(f"[DB] Table={POSTGRES_APIKEY_TABLE}")
    print(f"[DB] Exclude Ids={exclude_ids}")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(base_sql, params)
                row = cur.fetchone()

                print(f"[DB] Query row result = {row}")

                if not row:
                    conn.rollback()
                    print("[DB] No matching active API key found.")
                    return None

                result = {
                    "Id": row[0],
                    "Email": row[1],
                    "Name": row[2],
                    "APIKey": row[3],
                    "Usage": row[4],
                }

                conn.commit()
                print(f"[DB] Reserved API key Id={result['Id']} Usage={result['Usage']}")
                return result

    except Exception as e:
        print(f"[DB ERROR] reserve_least_used_api_key failed: {e}")
        return None


def increment_api_key_usage(api_key_id: int, amount: int = 1) -> None:
    if not db_is_configured():
        return

    sql = f'''
        UPDATE {POSTGRES_SCHEMA}."{POSTGRES_APIKEY_TABLE}"
        SET "Usage" = "Usage" + %s
        WHERE "Id" = %s
    '''

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (amount, api_key_id))
            conn.commit()
        print(f"[DB] Usage incremented by {amount} for API key Id={api_key_id}")
    except Exception as e:
        print(f"[DB ERROR] increment_api_key_usage failed: {e}")


def penalize_api_key_usage(api_key_id: int, penalty: int = 100) -> None:
    """
    Push a bad/exhausted key to the back by increasing Usage a lot.
    Example: 0 -> 100, 5 -> 105
    """
    if not db_is_configured():
        return

    sql = f'''
        UPDATE {POSTGRES_SCHEMA}."{POSTGRES_APIKEY_TABLE}"
        SET "Usage" = "Usage" + %s
        WHERE "Id" = %s
    '''

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (penalty, api_key_id))
            conn.commit()
        print(f"[DB] Usage penalized by {penalty} for API key Id={api_key_id}")
    except Exception as e:
        print(f"[DB ERROR] penalize_api_key_usage failed: {e}")


def is_retryable_gemini_key_error(error_text: str) -> bool:
    """
    Detect errors where we should penalize the current key and try another one.
    """
    if not error_text:
        return False

    text = error_text.upper()

    retry_patterns = [
        "429",
        "RESOURCE_EXHAUSTED",
        "QUOTA",
        "RATE LIMIT",
        "RATE_LIMIT",
        "TOO MANY REQUESTS",
        "API KEY INVALID",
        "INVALID API KEY",
        "API_KEY_INVALID",
        "PERMISSION_DENIED",
        "403",
    ]

    return any(pattern in text for pattern in retry_patterns)


# --------------------------------------------------
# Gemini
# --------------------------------------------------
def call_gemini_with_frames(frame_jpegs: List[bytes], api_key: str) -> dict:
    if genai is None or types is None:
        raise RuntimeError("Gemini SDK not available.")

    client = genai.Client(api_key=api_key)

    preferred_candidates = [
        "HELLO",
        "HI",
        "NO",
        "YES",
        "THANK YOU",
        "SORRY",
        "GOOD MORNING",
        "GOOD AFTERNOON",
        "GOOD EVENING",
        "UNKNOWN",
    ]

    candidate_labels = [label for label in preferred_candidates if label in FSL_LABELS]

    if not candidate_labels:
        candidate_labels = FSL_LABELS

    prompt = f"""
You are classifying a short Filipino Sign Language or gesture clip from extracted frames.

Return ONLY valid JSON.
Do not use markdown.
Do not use code fences.

Full allowed labels:
{json.dumps(FSL_LABELS)}

Priority candidate labels for this clip:
{json.dumps(candidate_labels)}

Very important decision rules:
1. Use MOTION across the full frame sequence as the primary signal.
2. Use handshape only as the secondary signal.
3. If an open palm is moving side-to-side like a greeting wave, prefer HELLO or HI, not FIVE.
4. Choose FIVE only when the hand is mainly static and clearly representing the number five.
5. Do not choose a number label just because the fingers are extended if the motion suggests greeting.
6. If the gesture is a greeting but the exact greeting label is uncertain, prefer HELLO if available.
7. Choose NO only if the gesture clearly expresses refusal/negation rather than greeting.
8. If motion evidence is weak or ambiguous, return UNKNOWN instead of forcing a wrong label.

Task:
Inspect the sequence as an ordered motion sequence from earliest frame to latest frame.
Determine the best label using motion first, handshape second.

Return this exact JSON shape:
{{
  "success": true,
  "mode": "gemini",
  "label": "ONE_ALLOWED_LABEL_OR_UNKNOWN",
  "confidence": 0.0,
  "top_k": [
    {{"label": "LABEL1", "confidence": 0.0}},
    {{"label": "LABEL2", "confidence": 0.0}},
    {{"label": "LABEL3", "confidence": 0.0}}
  ],
  "message": "short explanation",
  "warning": "short warning"
}}

Rules:
- label must be from the full allowed labels
- prefer choosing from the priority candidate labels when appropriate
- confidence must be between 0 and 1
- top_k should contain at most 3 items
- prefer correctness over guessing
- keep message short
"""

    contents = [
        prompt,
        "These images are ordered in time from the beginning of the clip to the end. Analyze the motion across frames, not only the handshape in a single frame."
    ]

    for jpg in frame_jpegs:
        contents.append(types.Part.from_bytes(data=jpg, mime_type="image/jpeg"))

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
    )

    raw_text = getattr(response, "text", None)
    if not raw_text:
        raw_text = str(response)

    parsed = parse_model_json(raw_text)

    raw_label = str(parsed.get("label", "UNKNOWN")).strip().upper()
    confidence = max(0.0, min(1.0, safe_float(parsed.get("confidence", 0.0))))

    top_k = []
    for item in parsed.get("top_k", [])[:3]:
        if isinstance(item, dict):
            item_label = str(item.get("label", "UNKNOWN")).strip().upper()
            item_conf = max(0.0, min(1.0, safe_float(item.get("confidence", 0.0))))
            top_k.append({
                "label": item_label,
                "confidence": item_conf,
            })

    if raw_label in FSL_LABELS:
        final_label = raw_label
    elif top_k:
        final_label = top_k[0]["label"]
        confidence = top_k[0]["confidence"]
    else:
        final_label = "UNKNOWN"

    return {
        "success": True,
        "mode": "gemini",
        "label": final_label,
        "confidence": confidence,
        "top_k": top_k,
        "message": str(parsed.get("message", "Prediction generated by Gemini.")),
        "warning": str(
            parsed.get(
                "warning",
                "This is an approximate frame-based prediction, not a trained local FSL model."
            )
        ),
        "frames_used": len(frame_jpegs),
        "candidate_labels_used": candidate_labels,
        "allowed_labels": FSL_LABELS,
    }


def predict_with_rotated_api_key(frame_jpegs: List[bytes]) -> dict:
    """
    Try least-used keys one by one.

    Rules:
    - success => usage +1
    - retryable Gemini key/quota error => usage +100, then try next key
    - non-retryable error => return fallback immediately
    """
    tried_key_ids: set[int] = set()
    attempts: List[Dict[str, Any]] = []

    while True:
        key_row = reserve_least_used_api_key(exclude_ids=tried_key_ids)

        if not key_row:
            if attempts:
                return {
                    "success": True,
                    "mode": "fallback_all_keys_failed",
                    "label": "UNKNOWN",
                    "confidence": 0.0,
                    "top_k": [],
                    "message": "No usable Gemini API key is currently available in PostgreSQL after trying the available keys.",
                    "frames_used": len(frame_jpegs),
                    "allowed_labels": FSL_LABELS,
                    "attempts": attempts,
                }

            return {
                "success": True,
                "mode": "fallback_no_api_key_available",
                "label": "UNKNOWN",
                "confidence": 0.0,
                "top_k": [],
                "message": "No active Gemini API key is available in PostgreSQL.",
                "frames_used": len(frame_jpegs),
                "allowed_labels": FSL_LABELS,
                "attempts": attempts,
            }

        tried_key_ids.add(key_row["Id"])

        try:
            print(f"[AI] Trying API key Id={key_row['Id']} Email={key_row['Email']}")
            result = call_gemini_with_frames(frame_jpegs, key_row["APIKey"])

            increment_api_key_usage(key_row["Id"], 1)

            result["api_key_info"] = {
                "id": key_row["Id"],
                "email": key_row["Email"],
                "name": key_row["Name"],
                "previous_usage": key_row["Usage"],
                "usage_incremented": 1,
            }
            result["attempts"] = attempts + [{
                "id": key_row["Id"],
                "email": key_row["Email"],
                "status": "success",
            }]
            return result

        except Exception as e:
            error_text = str(e)
            print(f"[AI ERROR] Gemini failed for key Id={key_row['Id']}: {error_text}")

            if is_retryable_gemini_key_error(error_text):
                penalize_api_key_usage(key_row["Id"], 100)

                attempts.append({
                    "id": key_row["Id"],
                    "email": key_row["Email"],
                    "status": "penalized_and_skipped",
                    "penalty": 100,
                    "error": error_text,
                })

                print(f"[AI] Key Id={key_row['Id']} penalized by 100. Trying next key.")
                continue

            return {
                "success": True,
                "mode": "fallback_after_gemini_error",
                "label": "UNKNOWN",
                "confidence": 0.0,
                "top_k": [],
                "message": f"Gemini failed. Returning fallback response. Details: {error_text}",
                "frames_used": len(frame_jpegs),
                "allowed_labels": FSL_LABELS,
                "api_key_info": {
                    "id": key_row["Id"],
                    "email": key_row["Email"],
                    "name": key_row["Name"],
                    "previous_usage": key_row["Usage"],
                    "usage_incremented": 0,
                },
                "attempts": attempts + [{
                    "id": key_row["Id"],
                    "email": key_row["Email"],
                    "status": "failed_no_retry",
                    "error": error_text,
                }],
            }


# --------------------------------------------------
# FastAPI app
# --------------------------------------------------
app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def home():
    return FileResponse(resolve_page_file("index.html"))


@app.get("/translate")
def translate_page():
    return FileResponse(resolve_page_file("translate.html"))

@app.get("/tutorial")
def tutorial_page():
    return FileResponse(resolve_page_file("tutorial.html"))


@app.get("/api/health")
def health():
    return {
        "success": True,
        "app": APP_TITLE,
        "gemini_model": GEMINI_MODEL,
        "labels_file": LABELS_FILE,
        "labels_count": len(FSL_LABELS),
        "labels": FSL_LABELS,
        "max_record_seconds": MAX_RECORD_SECONDS,
        "max_frames_to_send": MAX_FRAMES_TO_SEND,
        "postgres_configured": db_is_configured(),
        "static_dir": str(STATIC_DIR),
        "templates_dir": str(TEMPLATES_DIR),
    }


@app.post("/api/predict")
async def predict(file: UploadFile = File(...)):
    try:
        if not file.filename:
            return JSONResponse(status_code=400, content=error_result("No file received."))

        raw = await file.read()
        if not raw:
            return JSONResponse(status_code=400, content=error_result("Uploaded file is empty."))

        max_bytes = MAX_UPLOAD_MB * 1024 * 1024
        if len(raw) > max_bytes:
            return JSONResponse(
                status_code=413,
                content=error_result(f"File too large. Max allowed is {MAX_UPLOAD_MB} MB."),
            )

        suffix = Path(file.filename).suffix.lower() or ".webm"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)

        try:
            frames = extract_sample_frames(
                video_path=tmp_path,
                max_frames=MAX_FRAMES_TO_SEND,
                jpeg_quality=JPEG_QUALITY,
            )
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        if not frames:
            return JSONResponse(
                status_code=400,
                content=error_result("Could not extract frames from the uploaded video."),
            )

        result = predict_with_rotated_api_key(frames)
        return JSONResponse(content=result)

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content=error_result(f"Server error: {str(e)}"),
        )