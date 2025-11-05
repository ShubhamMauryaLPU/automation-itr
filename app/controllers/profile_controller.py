# app/controllers/profile_controller.py
from fastapi import APIRouter, Request, HTTPException
from app.services.itr_service import  fetch_itr_profile
import traceback

router = APIRouter(tags=["ITR Profile Automation"])

@router.post("/process")
async def process_itr_profile(request: Request):
    """
    POST endpoint to trigger ITR profile automation.
    Expects JSON: { "userId": "...", "password": "..." }
    """
    try:
        body = await request.json()
        user_id = body.get("userId")
        password = body.get("password")

        if not user_id or not password:
            raise HTTPException(status_code=400, detail="Missing userId or password")

        # Run automation service
        result = await fetch_itr_profile(user_id, password)

        return {
            "status": "success",
            "message": "Profile fetched successfully",
            "data": result
        }

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")
