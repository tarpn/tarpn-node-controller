from setuptools import setup, find_packages

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
                 'kiss-bench = tarpn.tools.kiss_bench:main'
             ]},
    python_requires='>=3.7',
    install_requires=[
        'appdirs==1.4.4',
        'asyncio==3.4.3',
        'hexdump==3.3',
        'pyserial==3.4',
        'pyserial-asyncio==0.4'
    ],
    setup_requires=[
        # Linting, according to PEP8
        'flake8==3.8.3',

        # Type checker
        'mypy==0.780',
        'pytest-runner'
    ],
    tests_requires=['pytest']
)
