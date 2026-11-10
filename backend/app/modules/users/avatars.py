"""Account-owned image storage, independent of the note application's assets."""
import hashlib
import io
import re
import uuid
import warnings
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings

AVATAR_PATH = re.compile(r"^/users/avatars/([a-f0-9]{24})/([a-f0-9]{32}\.png)$")
Image.MAX_IMAGE_PIXELS = 20_000_000


def owner_key(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:24]


def avatar_file(owner: str, name: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{24}", owner) or not re.fullmatch(r"[a-f0-9]{32}\.png", name):
        raise HTTPException(404, "프로필 사진을 찾을 수 없습니다.")
    return get_settings().avatar_upload_path / owner / name


async def save_avatar(file: UploadFile, user_id: str) -> str:
    settings = get_settings()
    data = await file.read(settings.max_avatar_bytes + 1)
    if len(data) > settings.max_avatar_bytes:
        raise HTTPException(413, "프로필 사진은 5MB 이하여야 합니다.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "GIF", "WEBP", "AVIF", "BMP"}:
                    raise ValueError("Unsupported image")
                image.load()
                result = image.convert("RGBA")
                result.thumbnail((1024, 1024))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise HTTPException(415, "PNG, JPG, GIF, WebP, AVIF 또는 BMP 이미지만 업로드할 수 있습니다.") from error
    owner, name = owner_key(user_id), f"{uuid.uuid4().hex}.png"
    path = avatar_file(owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result.save(path, format="PNG")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return f"/users/avatars/{owner}/{name}"


def delete_owned_avatar(url: str | None, user_id: str):
    match = AVATAR_PATH.fullmatch(url or "")
    if match and match[1] == owner_key(user_id):
        avatar_file(match[1], match[2]).unlink(missing_ok=True)
