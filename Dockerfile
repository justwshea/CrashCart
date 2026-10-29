FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python tools/make_incident.py --all --seed 7 --out /srv/incidents

ENV INCIDENTS_DIR=/srv/incidents \
    RUNS_DIR=/srv/runs \
    MPLBACKEND=Agg \
    PORT=8080 \
    LLM_MODE=mock

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
