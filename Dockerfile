# Repo Pilot. Targets Hugging Face Spaces, which runs the container as a
# non-root user on port 7860, but nothing here is Spaces-specific: PORT is
# read at start so the same image runs on Fly, Render or locally.
FROM python:3.13-slim

# Spaces runs as uid 1000. Creating that user here means the writable
# paths below belong to whoever the platform actually runs as, rather than
# to root, which is the usual cause of a permission error on first boot.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies first so a code change does not invalidate the layer that
# takes minutes to build. torch arrives as a sentence-transformers
# dependency and dominates both image size and build time.
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=user . .

# The embedding model is downloaded once at build time rather than on the
# first request. Without this the first visitor waits out a model download
# on a cold container, which on a free tier is most visitors.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')" \
    && chown -R user:user /home/user

# SQLite and Chroma both write here. On Spaces this is ephemeral unless a
# persistent volume is attached; the cache is designed to be rebuildable,
# so losing it costs a re-ingest and nothing else.
RUN mkdir -p /app/data && chown -R user:user /app/data

USER user
ENV PORT=7860
EXPOSE 7860

# Single-quoted inside double: no nested escaping to get wrong, which
# would otherwise show up only as a container that never reports healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/health').read()"

# Shell form so $PORT is expanded. One worker on purpose: the rate limiter
# and the daily quota guard hold their counters in memory, so a second
# worker would silently double every limit.
CMD uvicorn app.interface.main:app --host 0.0.0.0 --port $PORT --workers 1
