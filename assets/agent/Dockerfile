FROM public.ecr.aws/docker/library/python:3.12-slim
 
# Unbuffered stdout/stderr so logs reach CloudWatch; force 0.0.0.0 binding.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DOCKER_CONTAINER=1
 
WORKDIR /app
 
COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
# Shared domain package (forecast, and later betting/wallet), then the agent app.
COPY domain/python/domain/ ./domain/
COPY agent/agent.py agent/tools.py ./
 
# AgentCore Runtime contract: POST /invocations and GET /ping on 8080.
EXPOSE 8080
 
CMD ["python", "agent.py"]