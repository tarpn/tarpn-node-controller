from setuptools import setup, find_packages


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
    name='tarpn',
    version='',
    packages=find_packages(),
    url='',
    license='',
    author='David Arthur',
    author_email='mumrah@gmail.com',
    description='',
    entry_points={
             'console_scripts': [
                 'tarpn-serial-dump = tarpn.tools.serial_dump:main',
                 'tarpn-kiss-cat = tarpn.tools.kiss_cat:main',
                 'tarpn-packet-dump = tarpn.tools.packet_dump:main',
                 'tarpn-node = tarpn.main:main',
                 'tarpn-node2 = tarpn.main2:main',
                 'kiss-bench = tarpn.tools.kiss_bench:main',
                 'tarpn-tty = tarpn.tools.tty:main'
             ]},
    python_requires='>=3.7',
    install_requires=[
        'appdirs==1.4.4',
        'asyncio==3.4.3',
        'hexdump==3.3',
        'pyserial==3.4',
        'pyserial-asyncio==0.4',
        'msgpack==1.0.0',
        'pyformance==0.4'
    ],
    extras_require=EXTRA_REQUIRES
)
