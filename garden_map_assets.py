from __future__ import annotations

from dataclasses import dataclass
import mimetypes
import os
from pathlib import Path
from uuid import uuid4

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover
    storage = None


@dataclass
class SavedMapAsset:
    path: str
    mime_type: str


class GardenMapAssetStore:
    def __init__(self, *, local_dir: Path, bucket_name: str | None = None) -> None:
        self.local_dir = local_dir
        self.local_dir.mkdir(parents=True, exist_ok=True)
        self.bucket_name = (bucket_name or "").strip()
        self._storage_client = None

    def save_background(self, *, filename: str, data: bytes, mime_type: str) -> SavedMapAsset:
        extension = Path(filename or "garden-map").suffix.lower()
        if not extension:
            guessed = mimetypes.guess_extension(mime_type or "") or ".jpg"
            extension = guessed.lower()

        object_name = f"garden-map/background-{uuid4().hex}{extension}"
        if self.bucket_name:
            if storage is None:
                raise RuntimeError("google-cloud-storage is niet beschikbaar.")
            bucket = self._client().bucket(self.bucket_name)
            blob = bucket.blob(object_name)
            blob.upload_from_string(data, content_type=mime_type or "application/octet-stream")
            return SavedMapAsset(path=f"gs://{self.bucket_name}/{object_name}", mime_type=mime_type)

        target = self.local_dir / object_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return SavedMapAsset(path=str(target), mime_type=mime_type)

    def load_background(self, path: str) -> tuple[bytes, str]:
        cleaned = (path or "").strip()
        if not cleaned:
            raise FileNotFoundError("Geen kaartachtergrond opgeslagen.")

        if cleaned.startswith("gs://"):
            if storage is None:
                raise RuntimeError("google-cloud-storage is niet beschikbaar.")
            bucket_name, object_name = cleaned.removeprefix("gs://").split("/", 1)
            blob = self._client().bucket(bucket_name).blob(object_name)
            if not blob.exists():
                raise FileNotFoundError("Kaartachtergrond niet gevonden.")
            return blob.download_as_bytes(), (blob.content_type or "application/octet-stream")

        target = Path(cleaned)
        if not target.exists():
            raise FileNotFoundError("Kaartachtergrond niet gevonden.")
        guessed_mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return target.read_bytes(), guessed_mime

    def _client(self):
        if self._storage_client is None:
            self._storage_client = storage.Client()
        return self._storage_client
