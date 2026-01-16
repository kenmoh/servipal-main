from fastapi import UploadFile, HTTPException
from supabase import AsyncClient
from app.database.supabase import get_supabase_client
from uuid import uuid4
import os

async def upload_to_supabase_storage(
    file: UploadFile,
        supabase: AsyncClient,
    bucket: str = "delivery-proofs",
    folder: str = "proofs"
) -> str:
    """
    Upload file to Supabase Storage and return public URL.
    
    Args:
        file: UploadFile from FastAPI
        bucket: Storage bucket name (create in Supabase dashboard)
        folder: Subfolder inside bucket
    
    Returns:
        Public URL of uploaded file
    """
    try:

        # 1. Validate file
        if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
            raise HTTPException(
                status_code=400,
                detail="Only JPG, PNG, WEBP images allowed"
            )

        if file.size > 10 * 1024 * 1024:  # 10MB limit
            raise HTTPException(
                status_code=400,
                detail="File too large. Max 10MB"
            )

        # 2. Read file content
        contents = await file.read()

        # 3. Generate unique filename
        file_ext = file.filename.split(".")[-1].lower()
        unique_filename = f"{uuid4().hex}.{file_ext}"
        file_path = f"{folder}/{unique_filename}" if folder else unique_filename

        # 4. Upload to Supabase Storage
        upload_resp = supabase.storage.from_(bucket).upload(
            path=file_path,
            file=contents,
            file_options={"content-type": file.content_type, "upsert": False}
        )

        if upload_resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=500,
                detail="Upload to storage failed"
            )

        # 5. Get public URL
        public_url = supabase.storage.from_(bucket).get_public_url(file_path)

        return public_url

    except Exception as e:
        if "Duplicate" in str(e):
            # Rare case — retry with new name
            return await upload_to_supabase_storage(file, bucket, folder)
        raise HTTPException(
            status_code=500,
            detail=f"Storage upload failed: {str(e)}"
        )