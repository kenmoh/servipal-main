from fastapi import UploadFile, HTTPException, status
from supabase import AsyncClient
from uuid import uuid4
import os


async def upload_to_supabase_storage(
    file: UploadFile,
    supabase: AsyncClient,
    bucket: str = "delivery-proofs",
    folder: str = "proofs",
) -> str:
    """
    Upload file to Supabase Storage and return public URL.

    Args:
        file: UploadFile from FastAPI
        supabase: Supabase async client
        bucket: Storage bucket name (create in Supabase dashboard)
        folder: Subfolder inside bucket

    Returns:
        Public URL of uploaded file
    """
    try:
        # 0. Handle None file
        if file is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="File is required"
            )

        # 1. Validate file
        if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only JPG, PNG, WEBP images allowed",
            )

        if file.size and file.size > 8 * 1024 * 1024:  # 10MB limit
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File too large. Max 8MB",
            )

        # 2. Read file content
        contents = await file.read()

        # 3. Generate unique filename
        file_ext = file.filename.split(".")[-1].lower()
        unique_filename = f"{uuid4().hex}.{file_ext}"
        file_path = f"{folder}/{unique_filename}" if folder else unique_filename

        # 4. Upload to Supabase Storage
        upload_resp = await supabase.storage.from_(bucket).upload(
            path=file_path,
            file=contents,
            file_options={"content-type": file.content_type, "upsert": False},
        )

        # 5. Get public URL - MOVED BEFORE the print statements
        public_url = await supabase.storage.from_(bucket).get_public_url(file_path)

        return public_url

    except HTTPException:
        raise
    except Exception as e:
        if "Duplicate" in str(e):
            # Rare case — retry with new name
            return await upload_to_supabase_storage(file, supabase, bucket, folder)
        raise HTTPException(status_code=500, detail=f"Storage upload failed: {str(e)}")
