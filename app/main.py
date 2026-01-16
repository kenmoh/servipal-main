from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import (
    user_routes, payment_route, delivery_route, 
    notification_router, review_router, food_router, laundry_route, auth_router, wallet_route
)


app = FastAPI(
    title="ServiPal API",
    description="Backend API for ServiPal - Food, Laundry, and Delivery Services",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
app.include_router(auth_router.router)
app.include_router(user_routes.router)
app.include_router(wallet_route.router)
app.include_router(payment_route.router)
app.include_router(delivery_route.router)
app.include_router(notification_router.router)
app.include_router(review_router.router)
app.include_router(food_router.router)
app.include_router(laundry_route.router)

@app.get("/")
async def root():
    return {
        "message": "Welcome to ServiPal API",
        "docs": "/docs",
        "status": "active"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}