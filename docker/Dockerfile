FROM python:3.11
WORKDIR /pkg
COPY . /pkg
RUN pip install --no-cache-dir .
CMD ["uvicorn", "sc_keeper.api:app", "--host", "0.0.0.0", "--port", "80"]
