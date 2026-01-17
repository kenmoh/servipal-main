from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.routes import (
    user_routes,
    payment_route,
    delivery_route,
    notification_router,
    review_router,
    food_router,
    laundry_route,
    auth_router,
    wallet_route,
    admin_router,
    product_route,
    dispute_route,
)
from app.config.logging import logger


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Handle application lifespan events"""
    # Startup
    logger.info("application_started", version="1.0.0")
    yield
    # Shutdown
    logger.info("application_shutdown")


app = FastAPI(
    title="ServiPal API",
    description="Backend API for ServiPal - Food, Laundry, and Delivery Services",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all incoming requests"""
    import time

    start_time = time.time()

    logger.info(
        "request_started",
        method=request.method,
        path=request.url.path,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )

    try:
        response = await call_next(request)
        process_time = time.time() - start_time

        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            process_time=round(process_time, 3),
        )

        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(
            "request_failed",
            method=request.method,
            path=request.url.path,
            error=str(e),
            process_time=round(process_time, 3),
            exc_info=True,
        )
        raise


@app.get("/", tags=["Root"])
async def root():
    """
    Root endpoint to verify API status.
    
    Returns:
        dict: A welcome message, link to docs, and status.
    """
    logger.debug("root_endpoint_accessed")
    return {"message": "Welcome to ServiPal API", "docs": "/docs", "status": "active"}


@app.get("/health", tags=["Root"])
async def health_check():
    """
    Health check endpoint.
    
    Returns:
        dict: The health status of the application.
    """
    logger.debug("health_check_accessed")
    return {"status": "healthy"}


# Include Routers
app.include_router(auth_router.router)
app.include_router(user_routes.router)
app.include_router(wallet_route.router, include_in_schema=False)
app.include_router(payment_route.router)
app.include_router(delivery_route.router)
app.include_router(notification_router.router)
app.include_router(review_router.router)
app.include_router(food_router.router)
app.include_router(laundry_route.router)
app.include_router(product_route.router)
app.include_router(dispute_route.router)
app.include_router(admin_router.router, include_in_schema=False)
