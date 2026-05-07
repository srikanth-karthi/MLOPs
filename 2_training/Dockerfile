FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

ENV GIT_PYTHON_REFRESH=quiet

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY train.py .

ENTRYPOINT ["python", "train.py"]
