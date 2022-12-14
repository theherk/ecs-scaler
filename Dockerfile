FROM python:3
    MAINTAINER Adam Sherwood <theherk@gmail.com>

COPY requirements.txt requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT [ "python", "./svcmgr.py" ]
