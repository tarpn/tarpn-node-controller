from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("tarpn")
except PackageNotFoundError:
    # package is not installed
    pass