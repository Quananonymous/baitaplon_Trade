# Image Python nhẹ
FROM python:3.11-slim

# Không tạo file .pyc + log ra stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Thư mục làm việc trong container
WORKDIR /app

# Cài thư viện
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ project vào container
COPY . .

# App lắng nghe trên port 8080
EXPOSE 8080

# Chạy FastAPI bằng uvicorn
# Nếu Fly đặt PORT thì dùng PORT đó, không thì dùng 8080
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
