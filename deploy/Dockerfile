# Enterprise GPT retail runtime -- one image, four entrypoints (R1).
#
# Build from the REPOSITORY ROOT, because the image installs from two directories:
#
#     docker build -f deploy/Dockerfile -t egpt-retail:dev .
#
# `libs/agno` is installed first, on purpose. `product/pyproject.toml` pins `egpt==0.2.0`, and
# public PyPI holds an unrelated `egpt==0.0.1`; installing the framework first satisfies the pin
# so pip never reaches the index for it. The `[os]` extra is what carries fastapi, uvicorn,
# sqlalchemy and PyJWT -- they are not runtime deps of the bare `egpt` distribution.
#
# `product/pyproject.toml` separately pins `egpt[os,markdown]`, so the second `pip install
# "/src/product"` below pulls in `[markdown]` too -- that's what `load-knowledge`'s prose half
# needs (`MarkdownChunking` requires `unstructured`); without it the image runs `load-knowledge`
# default (or `--prose-only`) straight into `ModuleNotFoundError` (#253). The pin in that extra
# (`unstructured<0.18.31`) is deliberate: it is the version already proven to install cleanly
# with no native build step (`demo_setup.sh` uses the same extra); an unpinned
# `pip install unstructured` resolves a newer release whose `numba` dependency fails to build
# here.
#
# No HEALTHCHECK and no curl: in Kubernetes the probe is the kubelet's job, and curl is only
# attack surface. compose declares its own healthcheck with a Python one-liner.
# No gcc / libpq-dev: psycopg[binary] ships a wheel.
# exec-form ENTRYPOINT: PID 1 is Python, so SIGTERM reaches it. That is what lets the consolidator
# drain its in-flight tick and the API drain its cancel-persist tasks on a pod delete.

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY libs/agno /src/libs/agno
COPY product   /src/product
RUN pip install "/src/libs/agno[os]" \
 && pip install "/src/product" \
 && rm -rf /src

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin egpt
USER 10001

EXPOSE 7777

ENTRYPOINT ["egpt-retail"]
CMD ["api"]
