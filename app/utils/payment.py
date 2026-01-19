import json
import httpx
from fastapi import HTTPException, status
from app.config.config import settings
from app.utils.redis_utils import cache_data, get_cached_data
from app.schemas.bank_schema import BankSchema, AccountDetails, AccountDetailResponse
from app.config.logging import logger
import hmac

flutterwave_base_url = "https://api.flutterwave.com/v3"
# https://api.flutterwave.com/v3/otps
servipal_base_url = "https://servipalbackend.onrender.com/api"
bank_url = "https://api.flutterwave.com/v3/banks/NG"


async def get_all_banks() -> list[BankSchema]:
    cache_key = "banks_list"
    cached_banks = await get_cached_data(cache_key)

    if cached_banks:
        return json.loads(cached_banks)
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(bank_url, headers=headers)
            banks = response.json()["data"]

            sorted_banks = sorted(banks, key=lambda bank: bank["name"])

            await cache_data(cache_key, json.dumps(sorted_banks, default=str), 86400)
            return sorted_banks

    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to get banks: {str(e)}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get banks: {str(e)}",
        )


async def resolve_account_details(
    data: AccountDetails,
) -> AccountDetailResponse:
    """
    Resolve bank account details using Flutterwave API

    Args:
        account_number: Bank account number
        account_bank: Bank code (e.g., "044" for Access Bank)

    Returns:
        Dict containing account details in format:
        {
            "account_number": "0690000032",
            "account_name": "Pastor Bright"
        }

    Raises:
        httpx.HTTPStatusError: If the API request fails
        httpx.RequestError: If there's a network error
    """

    payload = {"account_number": data.account_number, "account_bank": data.account_bank}

    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {settings.FLW_SECRET_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{flutterwave_base_url}/accounts/resolve",
                json=payload,
                headers=headers,
            )

            response.raise_for_status()

            # Get the raw response
            raw_response = response.json()

            # Extract and flatten the required fields
            if raw_response.get("status") == "success" and "data" in raw_response:
                data = raw_response["data"]

                formatted_response = {
                    "account_number": data["account_number"],
                    "account_name": data["account_name"],
                }
                return formatted_response

        except httpx.HTTPStatusError as e:
            logger.error(f"Error response from payment gateway: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Network error occurred: {str(e)}")
            raise

async def verify_transaction_tx_ref(tx_ref: str):
    try:
        headers = {"Authorization": f"Bearer {settings.FLW_SECRET_KEY}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{flutterwave_base_url}/transactions/verify_by_reference?tx_ref={tx_ref}",
                headers=headers,
            )
            response_data = response.json()
            return response_data
    except httpx.HTTPStatusError as e:
        logger.error(f"Payment gateway error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=502, detail=f"Payment gateway error: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to verify transaction reference: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Failed to verify transaction reference: {str(e)}"
        )
