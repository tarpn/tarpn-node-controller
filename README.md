# Setup

Create a virtualenv, activate, and install deps (using Python 3)

```
python3 -m venv venv
source venv/bin/activate
pip install -r req.txt
```

# Docker setup

```
docker build . -t tarpn
docker run tarpn:latest
```

# References

* https://tinkering.xyz/async-serial/
