import os
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, List, Union
from fastapi import Request
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config

from config import settings


def build_base_url(request: Optional[Request] = None) -> str:
    """Builds base URL dynamically from Request, or falls back to settings host/port."""
    if request:
        forwarded_host = request.headers.get("x-forwarded-host")
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_host:
            scheme = forwarded_proto or "https"
            return f"{scheme}://{forwarded_host}".rstrip("/")
        return str(request.base_url).rstrip("/")
    return f"http://{settings.HOST if settings.HOST != '0.0.0.0' else 'localhost'}:{settings.PORT}".rstrip("/")


class StorageService:
    """
    Unified Production Storage Service.
    Supports both local filesystem and S3-compatible cloud storage (AWS S3, Supabase, Cloudflare R2).
    Switched purely via .env:
        S3_MODE=local or S3_MODE=s3
        S3_ENDPOINT=storage (for local) or https://<s3-endpoint> (for cloud)
    """

    def __init__(self):
        self.mode = (getattr(settings, "S3_MODE", None) or os.getenv("S3_MODE", "local")).strip().lower()
        self.endpoint = (getattr(settings, "S3_ENDPOINT", None) or os.getenv("S3_ENDPOINT", "storage")).strip()
        self.bucket = (getattr(settings, "S3_BUCKET", None) or os.getenv("S3_BUCKET", "")).strip()
        self.key = (getattr(settings, "S3_KEY", None) or os.getenv("S3_KEY", "")).strip()
        self.secret = (getattr(settings, "S3_SECRET", None) or os.getenv("S3_SECRET", "")).strip()
        self.region = (getattr(settings, "S3_REGION", None) or os.getenv("S3_REGION", "us-east-1")).strip()

        # Local storage directory root
        # If endpoint is a relative folder name (e.g. "storage"), resolve to backend/storage
        if self.mode == "local" or not self.endpoint.startswith(("http://", "https://")):
            dir_name = self.endpoint if not self.endpoint.startswith(("http://", "https://")) else "storage"
            self.local_dir = Path(__file__).resolve().parent.parent / dir_name
            self.local_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.local_dir = Path(__file__).resolve().parent.parent / "storage"
            self.local_dir.mkdir(parents=True, exist_ok=True)

        self._s3_client = None

    @property
    def is_s3(self) -> bool:
        return self.mode == "s3"

    def _get_s3_client(self):
        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.key or None,
                aws_secret_access_key=self.secret or None,
                region_name=self.region,
                config=Config(signature_version="s3v4"),
            )
        return self._s3_client

    def extract_key(self, path_or_url: str) -> str:
        """Extracts the storage relative key (e.g. images/user/art.png) from a path or full URL."""
        if not path_or_url:
            return ""
        clean = str(path_or_url).replace("\\", "/")
        clean = clean.split("?")[0]
        for prefix in ["images/", "gcode/", "scripts/"]:
            idx = clean.find(prefix)
            if idx != -1:
                return clean[idx:]
        if self.bucket and f"/{self.bucket}/" in clean:
            return clean.split(f"/{self.bucket}/", 1)[1]
        return clean.lstrip("/")

    def get_url(self, key: str, request: Optional[Request] = None) -> str:
        """
        Returns full accessible URL to access the file.
        In local mode: returns http://<host>/storage/{key}
        In cloud S3 mode: generates a 7-day SigV4 presigned URL so images/files are 100%
        accessible to mobile apps and browsers without 403 AccessDenied errors.
        """
        clean_key = self.extract_key(key)

        if self.is_s3:
            s3 = self._get_s3_client()
            try:
                # 7-day presigned URL (604,800 seconds max allowed by SigV4)
                return s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": clean_key},
                    ExpiresIn=604800,
                )
            except Exception as e:
                endpoint_url = self.endpoint.rstrip("/")
                return f"{endpoint_url}/{self.bucket}/{clean_key}"

        # Local mode
        base_url = build_base_url(request)
        folder = self.local_dir.name
        return f"{base_url}/{folder}/{clean_key}"

    def get_local_path(self, key: str) -> Path:
        """Returns local filesystem Path for a key. Creates parent directories if needed."""
        clean_key = key.lstrip("/").replace("\\", "/")
        path = self.local_dir / clean_key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def upload_file(
        self,
        key: str,
        file_path: Union[str, Path],
        content_type: Optional[str] = None,
        request: Optional[Request] = None,
    ) -> str:
        """
        Uploads an existing file from disk into storage (Local or S3).
        Returns the resolved URL.
        """
        clean_key = key.lstrip("/").replace("\\", "/")
        src_path = Path(file_path)

        if not src_path.is_file():
            raise FileNotFoundError(f"Source file not found: {src_path}")

        if self.is_s3:
            s3 = self._get_s3_client()
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type
            s3.upload_file(str(src_path), self.bucket, clean_key, ExtraArgs=extra_args)
            return self.get_url(clean_key, request)

        # Local mode
        dest_path = self.get_local_path(clean_key)
        if src_path.resolve() != dest_path.resolve():
            shutil.copy2(str(src_path), str(dest_path))
        return self.get_url(clean_key, request)

    def upload_bytes(
        self,
        key: str,
        data: bytes,
        content_type: Optional[str] = None,
        request: Optional[Request] = None,
    ) -> str:
        """
        Uploads raw bytes directly into storage (Local or S3).
        Returns the resolved URL.
        """
        clean_key = key.lstrip("/").replace("\\", "/")

        if self.is_s3:
            s3 = self._get_s3_client()
            content_type = content_type or "application/octet-stream"
            s3.put_object(
                Bucket=self.bucket,
                Key=clean_key,
                Body=data,
                ContentType=content_type,
            )
            return self.get_url(clean_key, request)

        # Local mode
        dest_path = self.get_local_path(clean_key)
        dest_path.write_bytes(data)
        return self.get_url(clean_key, request)

    def read_text(self, key: str) -> Optional[str]:
        """Reads text content for a stored key (e.g. G-code files)."""
        clean_key = key.lstrip("/").replace("\\", "/")

        if self.is_s3:
            try:
                s3 = self._get_s3_client()
                response = s3.get_object(Bucket=self.bucket, Key=clean_key)
                return response["Body"].read().decode("utf-8")
            except Exception as e:
                print(f"[STORAGE S3] Could not read text for {clean_key}: {e}")
                return None

        # Local mode
        path = self.get_local_path(clean_key)
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[STORAGE LOCAL] Could not read text for {clean_key}: {e}")
                return None
        return None

    def read_bytes(self, key: str) -> Optional[bytes]:
        """Reads raw bytes for a stored key."""
        clean_key = key.lstrip("/").replace("\\", "/")

        if self.is_s3:
            try:
                s3 = self._get_s3_client()
                response = s3.get_object(Bucket=self.bucket, Key=clean_key)
                return response["Body"].read()
            except Exception as e:
                print(f"[STORAGE S3] Could not read bytes for {clean_key}: {e}")
                return None

        # Local mode
        path = self.get_local_path(clean_key)
        if path.is_file():
            try:
                return path.read_bytes()
            except Exception as e:
                print(f"[STORAGE LOCAL] Could not read bytes for {clean_key}: {e}")
                return None
        return None

    def file_exists(self, key: str) -> bool:
        """Checks if a file exists in Local storage or S3 bucket."""
        clean_key = key.lstrip("/").replace("\\", "/")

        if self.is_s3:
            try:
                s3 = self._get_s3_client()
                s3.head_object(Bucket=self.bucket, Key=clean_key)
                return True
            except ClientError:
                return False
            except Exception:
                return False

        # Local mode
        path = self.get_local_path(clean_key)
        return path.is_file() and path.stat().st_size > 0

    def delete_file(self, key: str) -> bool:
        """Deletes a single file by key."""
        clean_key = key.lstrip("/").replace("\\", "/")

        if self.is_s3:
            try:
                s3 = self._get_s3_client()
                s3.delete_object(Bucket=self.bucket, Key=clean_key)
                return True
            except Exception as e:
                print(f"[STORAGE S3] Could not delete {clean_key}: {e}")
                return False

        # Local mode
        path = self.get_local_path(clean_key)
        if path.is_file():
            try:
                path.unlink()
                return True
            except Exception as e:
                print(f"[STORAGE LOCAL] Could not delete {clean_key}: {e}")
                return False
        return False

    def delete_art_files(self, user_id: str, art_id: str) -> Dict[str, List[str]]:
        """
        Deletes all files associated with an artwork (original photo, canvas preview, G-code, script PDF).
        Works seamlessly across both Local storage and S3 bucket.
        """
        deleted_files: Dict[str, List[str]] = {
            "images": [],
            "canvas": [],
            "gcode": [],
            "scripts": [],
        }

        user_id_str = str(user_id)
        art_id_str = str(art_id)

        if self.is_s3:
            s3 = self._get_s3_client()
            prefixes = [
                ("images", f"images/{user_id_str}/{art_id_str}"),
                ("canvas", f"images/{user_id_str}/canvas/{art_id_str}"),
                ("gcode", f"gcode/{user_id_str}/{art_id_str}"),
                ("scripts", f"scripts/{user_id_str}/{art_id_str}"),
            ]
            for category, prefix in prefixes:
                try:
                    paginator = s3.get_paginator("list_objects_v2")
                    for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                        for obj in page.get("Contents", []):
                            k = obj["Key"]
                            s3.delete_object(Bucket=self.bucket, Key=k)
                            deleted_files[category].append(k)
                except Exception as e:
                    print(f"[STORAGE S3] Error deleting prefix {prefix}: {e}")
            return deleted_files

        # Local mode
        categories = {
            "images": self.local_dir / "images" / user_id_str,
            "canvas": self.local_dir / "images" / user_id_str / "canvas",
            "gcode": self.local_dir / "gcode" / user_id_str,
            "scripts": self.local_dir / "scripts" / user_id_str,
        }

        for cat, folder in categories.items():
            if folder.exists():
                for f in folder.glob(f"{art_id_str}.*"):
                    if f.is_file() and (cat != "images" or f.parent == folder):
                        try:
                            f.unlink()
                            deleted_files[cat].append(f.name)
                        except Exception as e:
                            print(f"[STORAGE LOCAL] Error deleting file {f}: {e}")

        # Also clean any legacy path: storage/{user_id}/{art_id}.*
        legacy_dir = self.local_dir / user_id_str
        if legacy_dir.exists():
            for f in legacy_dir.glob(f"{art_id_str}.*"):
                if f.is_file():
                    try:
                        f.unlink()
                        deleted_files["images"].append(f.name)
                    except Exception:
                        pass

        return deleted_files


# Global singleton instance
storage = StorageService()
