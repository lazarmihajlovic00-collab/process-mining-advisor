FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY pmadvisor ./pmadvisor
RUN pip install --no-cache-dir .
COPY web ./web
COPY docs ./docs
ENV PMA_DB=/app/data/advisor.db
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["pma", "serve", "--host", "0.0.0.0", "--port", "8000"]
