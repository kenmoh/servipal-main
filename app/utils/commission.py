from supabase import  AsyncClient
async def get_commission_rate(order_type: str, supabase: AsyncClient) -> float:
    """
    Fetch commission rate for a specific service type
    Returns the vendor/dispatch share (e.g., 0.85)
    """
    column_map = {
        "DELIVERY": "delivery_commission_rate",
        "FOOD": "food_commission_rate",
        "LAUNDRY": "laundry_commission_rate",
        "PRODUCT": "product_commission_rate"
    }

    if order_type not in column_map:
        raise ValueError(f"Unknown order type: {order_type}")

    column = column_map[order_type]

    resp = await supabase.table("charges_and_commissions")\
        .select(column)\
        .single()\
        .execute()

    if not resp.data:
        raise ValueError("Charges configuration not found")

    return float(resp.data[column])