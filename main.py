import uvicorn
import jmespath
from config import *
from fastapi import FastAPI, Request
from curl_cffi import requests
from datetime import datetime, timezone
from fastapi.responses import JSONResponse
from pymongo import MongoClient

MONGO_URI = "mongodb+srv://atulkumaractowiz_db_user:utXB2kQPyiuxUxTk@cluster0.uxw0aog.mongodb.net/?retryWrites=true&w=majority&tls=true&appName=Cluster0"
DB_NAME = "morrisons_db"

app = FastAPI(
    title="Morrison's Product API",
    description="Fetch Morrison's product details and reviews programmatically",
    version="1.0.0",
)

# ---------------------- MongoDB connection ----------------------
@app.on_event("startup")
def startup_db_client():
    app.mongodb_client = MongoClient(MONGO_URI)
    app.db = app.mongodb_client[DB_NAME]
    app.logs_collection = app.db["api_logs"]
    print("✅ MongoDB connection established")

@app.on_event("shutdown")
def shutdown_db_client():
    app.mongodb_client.close()
    print("❌ MongoDB connection closed")
# ----------------------------------------------------------------

def convert_datetime(dt_str):
    try:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)

def review_fetch(product_uuid, sort_option="NEWEST"):
    url = f"https://groceries.morrisons.com/api/ecomreviews/v1/products/{product_uuid}/reviews"
    reviews_list = []
    try:
        params = {"sortOptionId": sort_option, "nextPage": 1}
        response = requests.get(url, params=params, headers=HEADERS, impersonate="chrome120", timeout=15)
        response.raise_for_status()
        json_data = response.json()

        for review in json_data.get("reviews", []):
            reviews_list.append({
                "id": review.get("id"),
                "review_title": review.get("headline"),
                "review_text": review.get("comments"),
                "review_rating": review.get("rating"),
                "review_date": review.get("createdDate"),
                "reviewer_profile": review.get("nickname"),
                "verified_purchase": review.get("isVerifiedBuyer"),
                "location": review.get("locale"),
            })
        return reviews_list
    except Exception as e:
        print(f"[ERROR] Failed to fetch reviews for {product_uuid}: {e}")
        return []

def fetch_product_details(retailer_product_id):
    url = "https://groceries.morrisons.com/api/webproductpagews/v5/products/bop"
    params = {"retailerProductId": retailer_product_id}
    try:
        response = requests.get(url, params=params, headers=HEADERS, impersonate="chrome120", timeout=15)
        response.raise_for_status()
        data = response.json()

        product_id = jmespath.search("product.retailerProductId", data)
        if not product_id:
            return None

        product = {
            "platform": "https://groceries.morrisons.com/",
            "product_url": f"https://groceries.morrisons.com/products/{product_id}",
            "product_title": jmespath.search("product.name", data),
            "description": (jmespath.search("bopData.detailedDescription", data) or "").replace("<br />", ""),
            "product_brand": jmespath.search("product.brand", data),
            "categories": [
                {"id": i + 1, "name": name}
                for i, name in enumerate(jmespath.search("product.categoryPath", data) or [])
            ],
            "size": jmespath.search("product.packSizeDescription", data),
            "available": jmespath.search("product.available", data),
            "mrp": jmespath.search("product.price.amount", data),
            "selling_price": jmespath.search("product.promoPrice.amount", data),
            "discount": False,
            "rating_value": jmespath.search("product.ratingSummary.overallRating", data),
            "rating_count": jmespath.search("product.ratingSummary.count", data),
            "images": [img.get("src") for img in jmespath.search("product.images", data) or []],
        }

        mrp = product["mrp"]
        selling = product["selling_price"]
        if mrp and selling and float(mrp) > float(selling):
            product["discount"] = True
        else:
            product["selling_price"] = mrp

        review_id = jmespath.search("product.productId", data)
        product["reviews"] = review_fetch(review_id)
        return product
    except Exception as e:
        print(f"[ERROR] Fetching product {retailer_product_id}: {e}")
        return None

@app.get("/")
def home():
    return {"message": "Welcome to Morrison's Product API"}

@app.get("/product/{product_id}")
def get_product(request: Request, product_id: str):
    start_time = datetime.now(timezone.utc)
    client_ip = request.client.host

    logs_collection = app.logs_collection
    log_entry = {
        "endpoint": f"/product/{product_id}",
        "method": "GET",
        "client_ip": client_ip,
        "timestamp": start_time,
        "status": "STARTED",
        "product_id": product_id,
    }

    log_id = logs_collection.insert_one(log_entry).inserted_id

    try:
        result = fetch_product_details(product_id)
        end_time = datetime.now(timezone.utc)
        response_time_ms = (end_time - start_time).total_seconds() * 1000

        if not result:
            logs_collection.update_one({"_id": log_id}, {
                "$set": {
                    "status": "FAILED",
                    "message": "Product not found",
                    "response_time_ms": response_time_ms,
                    "end_time": end_time,
                }
            })
            return JSONResponse({"error": "Product not found or unavailable"}, status_code=404)

        logs_collection.update_one({"_id": log_id}, {
            "$set": {
                "status": "SUCCESS",
                "response_time_ms": response_time_ms,
                "end_time": end_time,
                "response_data": result,
            }
        })

        return result

    except Exception as e:
        end_time = datetime.now(timezone.utc)
        response_time_ms = (end_time - start_time).total_seconds() * 1000
        logs_collection.update_one({"_id": log_id}, {
            "$set": {
                "status": "ERROR",
                "message": str(e),
                "response_time_ms": response_time_ms,
                "end_time": end_time,
            }
        })
        return JSONResponse({"error": "Internal server error"}, status_code=500)

if __name__ == "__main__":
    uvicorn.run("main:app", host="localhost", port=8000, reload=True)



