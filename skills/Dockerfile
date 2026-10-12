FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY skill_manager ./skill_manager
RUN mkdir -p /data/skills /data/archives
ENV SKILL_MANAGER_ROOT=/data
EXPOSE 8000
CMD ["uvicorn", "skill_manager.app:app", "--host", "0.0.0.0", "--port", "8000"]
