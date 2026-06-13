import os
import uvicorn
from src.web.app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
