import os
from setuptools import setup, find_packages

here = os.path.abspath(os.path.dirname(__file__))


def read_file_contents(path):
    import codecs

    with codecs.open(path, encoding="utf-8") as f:
        return f.read()


EXTRA_REQUIRES = dict(
    develop=[
        # Linting, according to PEP8
        'flake8==3.8.3',

        # Type checker
        'mypy==0.780',

        # Profiling
        'line_profiler==3.1.0',
        'flameprof==0.4',

        # Testing
        'pytest==6.0.1',
        'pytest-runner==5.2'
    ]
)
setup(
    name='tarpn-core',
    version='0.1.4',
    packages=find_packages(exclude=["tests", "tests.*"]),
    data_files=[
        ("config", ["config/defaults.ini", "config/node.ini.sample", "config/logging.ini"])
    ],
    url='https://github.com/tarpn/tarpn-node-controller',
    license='MIT License',
    author='David Arthur',
    author_email='mumrah@gmail.com',
   
    description='Python networking stack for packet radio',
    long_description=read_file_contents(os.path.join(here, "README.md")),
    long_description_content_type="text/markdown",
   
    entry_points={
             'console_scripts': [
                 'tarpn-serial-dump = tarpn.tools.serial_dump:main',
                 'tarpn-packet-dump = tarpn.tools.packet_dump:main',
                 'tarpn-node = tarpn.main:main',
                 'tarpn-app = tarpn.app.runner:main',
                 'tarpn-shell = tarpn.tools.shell:main'
             ]},
    python_requires='>=3.7',
    install_requires=[
        'hexdump==3.3',
        'pyserial==3.4',
        'pyserial-asyncio==0.4',
        'msgpack==1.0.0',
        'pyformance==0.4',
        'Flask==1.1.2',
        'networkx==2.6.2',
        'cmd2==2.1.2'
    ],
    extras_require=EXTRA_REQUIRES
)
