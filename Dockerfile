FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY web ./web
COPY out ./out
COPY data/vani_prompt_for_agent.txt ./data/vani_prompt_for_agent.txt
ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080
CMD ["python3", "src/server.py"]
