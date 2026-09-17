import uvicorn
from app.config import settings
from app.main import app

# Re-export app so `uvicorn main:app` works seamlessly
__all__ = ["app"]

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )